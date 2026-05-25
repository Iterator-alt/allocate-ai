"""Stage 1 Repository - Database queries for YouGov and Nielsen.

All queries run against PostgreSQL tables.
When API keys arrive, only the ingestion layer changes - these queries stay identical.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional, Dict, Any, Tuple, Union

from sqlalchemy import select, func, and_, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.data import YouGov, Nielsen
from src.services.stage1.cache import stage1_cache


def _to_date(d: Union[str, date]) -> date:
    """Convert string to date if needed."""
    if isinstance(d, date):
        return d
    return datetime.strptime(d, "%Y-%m-%d").date()


class Stage1Repository:
    """Repository for Stage 1 database queries.

    Query order: YouGov FIRST, then Nielsen.
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    # =========================================================================
    # DISTINCT VALUE QUERIES (for AI resolution)
    # =========================================================================

    async def get_distinct_yougov_sectors(self, use_cache: bool = True) -> List[str]:
        """Get all distinct sector_label values from YouGov.

        Used by AI Call #1 for industry resolution.
        Cached for 24 hours.
        """
        if use_cache:
            cached = await stage1_cache.get_yougov_sectors()
            if cached is not None:
                return cached

        query = select(YouGov.sector_label).distinct().order_by(YouGov.sector_label)
        result = await self.session.execute(query)
        sectors = [row[0] for row in result.all() if row[0]]

        if use_cache:
            await stage1_cache.set_yougov_sectors(sectors)

        return sectors

    async def get_distinct_nielsen_sectors(self, use_cache: bool = True) -> List[str]:
        """Get all distinct Wirtschaftsgruppe values from Nielsen.

        Used by AI Call #1 for industry resolution.
        Cached for 24 hours.
        """
        if use_cache:
            cached = await stage1_cache.get_nielsen_sectors()
            if cached is not None:
                return cached

        query = select(Nielsen.wirtschaftsgruppe).distinct().order_by(Nielsen.wirtschaftsgruppe)
        result = await self.session.execute(query)
        sectors = [row[0] for row in result.all() if row[0]]

        if use_cache:
            await stage1_cache.set_nielsen_sectors(sectors)

        return sectors

    async def get_distinct_yougov_brands(
        self,
        sectors: List[str],
        use_cache: bool = True,
    ) -> List[str]:
        """Get distinct brand_label values within given sectors.

        Used by AI Call #2 for brand resolution.
        Cached for 24 hours per sector.
        """
        # For caching, use sorted sector list as key
        cache_key = ",".join(sorted(sectors))

        if use_cache:
            cached = await stage1_cache.get_yougov_brands(cache_key)
            if cached is not None:
                return cached

        query = (
            select(YouGov.brand_label)
            .where(YouGov.sector_label.in_(sectors))
            .distinct()
            .order_by(YouGov.brand_label)
        )
        result = await self.session.execute(query)
        brands = [row[0] for row in result.all() if row[0]]

        if use_cache:
            await stage1_cache.set_yougov_brands(cache_key, brands)

        return brands

    async def get_distinct_nielsen_brands(
        self,
        wirtschaftsgruppen: List[str],
        use_cache: bool = True,
    ) -> List[str]:
        """Get distinct Marke values within given Wirtschaftsgruppen.

        Used by AI Call #2 for brand resolution.
        Cached for 24 hours per sector.
        """
        cache_key = ",".join(sorted(wirtschaftsgruppen))

        if use_cache:
            cached = await stage1_cache.get_nielsen_brands(cache_key)
            if cached is not None:
                return cached

        query = (
            select(Nielsen.marke)
            .where(Nielsen.wirtschaftsgruppe.in_(wirtschaftsgruppen))
            .distinct()
            .order_by(Nielsen.marke)
        )
        result = await self.session.execute(query)
        brands = [row[0] for row in result.all() if row[0]]

        if use_cache:
            await stage1_cache.set_nielsen_brands(cache_key, brands)

        return brands

    async def get_distinct_yougov_brands_excluding(
        self,
        sectors: List[str],
        exclude_brand: str,
    ) -> List[str]:
        """Get distinct brand_label values within given sectors, excluding one brand.

        Used by AI Call #5 for competitor suggestion.
        No caching - called once per run.
        """
        query = (
            select(YouGov.brand_label)
            .where(
                and_(
                    YouGov.sector_label.in_(sectors),
                    func.lower(YouGov.brand_label) != func.lower(exclude_brand),
                )
            )
            .distinct()
            .order_by(YouGov.brand_label)
        )
        result = await self.session.execute(query)
        return [row[0] for row in result.all() if row[0]]

    async def get_distinct_produktmarke(
        self,
        marke: str,
        years: List[int] = None,
    ) -> List[str]:
        """Get all distinct Produktmarke values for a given Marke.

        Used by AI Call #6 for Produktmarke filtering.
        No caching - called per brand.
        """
        if years is None:
            years = [2023, 2024, 2025]

        query = (
            select(Nielsen.produktmarke)
            .where(
                and_(
                    func.upper(Nielsen.marke) == func.upper(marke),
                    Nielsen.jahr.in_(years),
                    Nielsen.produktmarke.isnot(None),
                )
            )
            .distinct()
            .order_by(Nielsen.produktmarke)
        )
        result = await self.session.execute(query)
        return [row[0] for row in result.all() if row[0]]

    # =========================================================================
    # YOUGOV QUERIES (PRIMARY - search first)
    # =========================================================================

    async def get_yougov_brand_kpi_scores(
        self,
        sectors: List[str],
        metrics: List[str] = None,
        date_from: str = "2023-01-01",
        date_to: str = "2025-12-31",
    ) -> List[Dict[str, Any]]:
        """Get brand KPI scores for candidate retrieval (Step 3b).

        Query from design doc:
        SELECT brand_label, metric, AVG(score) as avg_score
        FROM yougov
        WHERE sector_label = ANY(%(yougov_sectors)s)
          AND metric IN ('adaware', 'aware', 'consider')
          AND date BETWEEN '2023-01-01' AND '2025-12-31'
        GROUP BY brand_label, metric;
        """
        if metrics is None:
            metrics = ["adaware", "aware", "consider"]

        # Convert string dates to date objects
        from_date = _to_date(date_from)
        to_date = _to_date(date_to)

        query = (
            select(
                YouGov.brand_label,
                YouGov.metric,
                func.avg(YouGov.score).label("avg_score"),
            )
            .where(
                and_(
                    YouGov.sector_label.in_(sectors),
                    YouGov.metric.in_(metrics),
                    YouGov.date >= from_date,
                    YouGov.date <= to_date,
                )
            )
            .group_by(YouGov.brand_label, YouGov.metric)
        )

        result = await self.session.execute(query)
        return [
            {
                "brand_label": row.brand_label,
                "metric": row.metric,
                "avg_score": float(row.avg_score) if row.avg_score else None,
            }
            for row in result.all()
        ]

    async def get_yougov_competitors(
        self,
        sectors: List[str],
        exclude_brand: str,
        primary_kpi: str,
        target_score: float,
        date_from: str = "2023-01-01",
        date_to: str = "2025-12-31",
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """Get competitors ranked by score proximity (Step 5).

        Query from design doc:
        SELECT brand_label, metric, AVG(score) as avg_score
        FROM yougov
        WHERE sector_label = ANY(%(yougov_sectors)s)
          AND brand_label != %(confirmed_yougov_brand)s
          AND metric = %(primary_kpi)s
          AND date BETWEEN '2023-01-01' AND '2025-12-31'
        GROUP BY brand_label, metric
        ORDER BY ABS(AVG(score) - %(target_score)s) ASC
        LIMIT 10;
        """
        # SQLAlchemy doesn't have ABS for aggregates easily, so we compute in Python
        # Convert string dates to date objects
        from_date = _to_date(date_from)
        to_date = _to_date(date_to)

        # Debug logging
        import logging
        logging.warning(f"get_yougov_competitors: date_from={date_from} ({type(date_from)}), from_date={from_date} ({type(from_date)})")

        query = (
            select(
                YouGov.brand_label,
                YouGov.metric,
                func.avg(YouGov.score).label("avg_score"),
            )
            .where(
                and_(
                    YouGov.sector_label.in_(sectors),
                    YouGov.brand_label != exclude_brand,
                    YouGov.metric == primary_kpi,
                    YouGov.date >= from_date,
                    YouGov.date <= to_date,
                )
            )
            .group_by(YouGov.brand_label, YouGov.metric)
        )

        result = await self.session.execute(query)
        rows = [
            {
                "brand_label": row.brand_label,
                "metric": row.metric,
                "avg_score": float(row.avg_score) if row.avg_score else 0.0,
            }
            for row in result.all()
        ]

        # Sort by proximity to target score
        rows.sort(key=lambda x: abs(x["avg_score"] - target_score))

        return rows[:limit]

    async def get_yougov_brand_data(
        self,
        brand_label: str,
        metrics: List[str] = None,
    ) -> List[Dict[str, Any]]:
        """Get all data for a specific brand (12 data points requirement)."""
        if metrics is None:
            metrics = ["adaware", "aware", "consider"]

        query = (
            select(YouGov)
            .where(
                and_(
                    YouGov.brand_label == brand_label,
                    YouGov.metric.in_(metrics),
                )
            )
            .order_by(YouGov.date.desc())
        )

        result = await self.session.execute(query)
        return [
            {
                "date": row.date,
                "sector_label": row.sector_label,
                "brand_label": row.brand_label,
                "metric": row.metric,
                "score": row.score,
            }
            for row in result.scalars().all()
        ]

    async def get_yougov_latest_scores(
        self,
        brand_label: str,
    ) -> Dict[str, Any]:
        """Get latest scores for all metrics for a brand."""
        result = {}
        for metric in ["adaware", "aware", "consider"]:
            query = (
                select(YouGov.score, YouGov.date)
                .where(
                    and_(
                        YouGov.brand_label == brand_label,
                        YouGov.metric == metric,
                        YouGov.score.isnot(None),
                    )
                )
                .order_by(YouGov.date.desc())
                .limit(1)
            )
            row = await self.session.execute(query)
            row = row.first()
            if row:
                result[metric] = {"score": row.score, "date": row.date}

        return result

    # =========================================================================
    # NIELSEN QUERIES (SECONDARY - search after YouGov)
    # =========================================================================

    async def get_nielsen_brand_spend(
        self,
        marke: str,
        years: List[int] = None,
        produktmarke_filter: List[str] = None,
    ) -> List[Dict[str, Any]]:
        """Get spend data for a specific brand (Step 4).

        Query from design doc:
        SELECT "Mediengruppe", SUM("TEuro") as total_spend, "Jahr", "Monat"
        FROM nielsen
        WHERE "Marke" = %(nielsen_brand)s
          AND "Jahr" IN (2023, 2024, 2025)
          [AND "Produktmarke" IN (filtered list)]  -- NEW: optional filter
        GROUP BY "Mediengruppe", "Jahr", "Monat"
        ORDER BY "Jahr", "Monat";

        Args:
            marke: Nielsen brand name (Marke column)
            years: List of years to include (default: 2023-2025)
            produktmarke_filter: Optional list of Produktmarke to include.
                                 If None, includes all Produktmarke.
        """
        if years is None:
            years = [2023, 2024, 2025]

        # Build WHERE conditions
        conditions = [
            Nielsen.marke == marke,
            Nielsen.jahr.in_(years),
        ]

        # Add Produktmarke filter if provided
        if produktmarke_filter:
            conditions.append(Nielsen.produktmarke.in_(produktmarke_filter))

        query = (
            select(
                Nielsen.mediengruppe,
                func.sum(Nielsen.teuro).label("total_spend"),
                Nielsen.jahr,
                Nielsen.monat,
            )
            .where(and_(*conditions))
            .group_by(Nielsen.mediengruppe, Nielsen.jahr, Nielsen.monat)
            .order_by(Nielsen.jahr, Nielsen.monat)
        )

        result = await self.session.execute(query)
        return [
            {
                "mediengruppe": row.mediengruppe,
                "total_spend": float(row.total_spend) if row.total_spend else 0.0,
                "jahr": row.jahr,
                "monat": row.monat,
            }
            for row in result.all()
        ]

    async def get_nielsen_competitors(
        self,
        wirtschaftsgruppen: List[str],
        exclude_brand: str,
        years: List[int] = None,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """Get competitors ranked by spend (Step 5).

        Query from design doc:
        SELECT "Marke", SUM("TEuro") as total_spend
        FROM nielsen
        WHERE "Wirtschaftsgruppe" = ANY(%(nielsen_sectors)s)
          AND "Marke" != %(confirmed_nielsen_brand)s
          AND "Jahr" IN (2023, 2024, 2025)
        GROUP BY "Marke"
        ORDER BY total_spend DESC
        LIMIT 10;
        """
        if years is None:
            years = [2023, 2024, 2025]

        query = (
            select(
                Nielsen.marke,
                func.sum(Nielsen.teuro).label("total_spend"),
            )
            .where(
                and_(
                    Nielsen.wirtschaftsgruppe.in_(wirtschaftsgruppen),
                    Nielsen.marke != exclude_brand,
                    Nielsen.jahr.in_(years),
                )
            )
            .group_by(Nielsen.marke)
            .order_by(func.sum(Nielsen.teuro).desc())
            .limit(limit)
        )

        result = await self.session.execute(query)
        return [
            {
                "marke": row.marke,
                "total_spend": float(row.total_spend) if row.total_spend else 0.0,
            }
            for row in result.all()
        ]

    async def get_nielsen_brand_total_spend(
        self,
        marke: str,
        years: List[int] = None,
        produktmarke_filter: List[str] = None,
    ) -> float:
        """Get total spend for a brand across all channels.

        Args:
            marke: Nielsen brand name (Marke column)
            years: List of years to include (default: 2023-2025)
            produktmarke_filter: Optional list of Produktmarke to include.
                                 If None, includes all Produktmarke.
        """
        if years is None:
            years = [2023, 2024, 2025]

        # Build WHERE conditions
        conditions = [
            Nielsen.marke == marke,
            Nielsen.jahr.in_(years),
        ]

        # Add Produktmarke filter if provided
        if produktmarke_filter:
            conditions.append(Nielsen.produktmarke.in_(produktmarke_filter))

        query = (
            select(func.sum(Nielsen.teuro))
            .where(and_(*conditions))
        )

        result = await self.session.execute(query)
        total = result.scalar()
        return float(total) if total else 0.0

    async def get_nielsen_spend_by_channel(
        self,
        marke: str,
        years: List[int] = None,
        limit: int = 5,
        produktmarke_filter: List[str] = None,
    ) -> List[Dict[str, Any]]:
        """Get top channels by spend for a brand.

        Args:
            marke: Nielsen brand name (Marke column)
            years: List of years to include (default: 2023-2025)
            limit: Max number of channels to return
            produktmarke_filter: Optional list of Produktmarke to include.
                                 If None, includes all Produktmarke.
        """
        if years is None:
            years = [2023, 2024, 2025]

        # Build WHERE conditions
        conditions = [
            Nielsen.marke == marke,
            Nielsen.jahr.in_(years),
        ]

        # Add Produktmarke filter if provided
        if produktmarke_filter:
            conditions.append(Nielsen.produktmarke.in_(produktmarke_filter))

        query = (
            select(
                Nielsen.mediengruppe,
                func.sum(Nielsen.teuro).label("total_spend"),
            )
            .where(and_(*conditions))
            .group_by(Nielsen.mediengruppe)
            .order_by(func.sum(Nielsen.teuro).desc())
            .limit(limit)
        )

        result = await self.session.execute(query)
        return [
            {
                "mediengruppe": row.mediengruppe,
                "total_spend": float(row.total_spend) if row.total_spend else 0.0,
            }
            for row in result.all()
        ]
