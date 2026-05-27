"""YouGov KPI data repository.

Search Priority: YouGov is searched FIRST before Nielsen.
Data Requirements:
- Minimum 12 data points required
- Data age: 2-3 years ideal, 4-5 years acceptable, >5 years rejected

NOTE: Updated to use the new 'yougov' table (YouGov model) instead of
the deprecated 'yougov_kpi' table (YouGovKPI model).
Column mappings:
- brand_label -> brand_label (same)
- sector -> sector_label
- year/month -> date (extract from date)
- adaware/aided/consider -> metric + score
"""

from datetime import datetime
from decimal import Decimal
from typing import List, Optional, Dict, Any, Tuple

from sqlalchemy import select, func, and_, extract
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.data import YouGov
from src.repositories.base import BaseRepository

# Data validation constants
MIN_DATA_POINTS = 12          # Minimum required data points
MAX_DATA_AGE_YEARS = 5        # Data older than 5 years is rejected
IDEAL_DATA_AGE_YEARS = 3      # 2-3 years is ideal


class YouGovRepository(BaseRepository[YouGov]):
    """Repository for YouGov brand KPI data.

    This is the PRIMARY data source - search YouGov FIRST, then Nielsen.

    Data Validation Rules:
    - Minimum 12 data points required for valid analysis
    - 2-3 years old: IDEAL quality
    - 4-5 years old: ACCEPTABLE quality
    - >5 years old: REJECTED (not used)
    """

    def __init__(self, session: AsyncSession):
        super().__init__(session, YouGov)
        self._current_year = datetime.now().year
        self._min_valid_year = self._current_year - MAX_DATA_AGE_YEARS

    def _get_valid_year_range(self) -> Tuple[int, int]:
        """Get valid year range (last 5 years only)."""
        return self._min_valid_year, self._current_year

    def _is_year_valid(self, year: int) -> bool:
        """Check if year is within valid range (not older than 5 years)."""
        return year >= self._min_valid_year

    async def get_by_brand(
        self,
        brand_label: str,
        year: Optional[int] = None,
        month: Optional[int] = None,
    ) -> List[YouGov]:
        """Get KPI data for a specific brand."""
        query = select(YouGov).where(YouGov.brand_label == brand_label)

        if year is not None:
            query = query.where(extract('year', YouGov.date) == year)
        if month is not None:
            query = query.where(extract('month', YouGov.date) == month)

        query = query.order_by(YouGov.date)
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def get_by_sector(
        self,
        sector: str,
        year: Optional[int] = None,
        limit: int = 1000,
    ) -> List[YouGov]:
        """Get KPI data for a sector (sector_label)."""
        query = select(YouGov).where(YouGov.sector_label == sector)

        if year is not None:
            query = query.where(extract('year', YouGov.date) == year)

        query = query.order_by(YouGov.brand_label, YouGov.date)
        query = query.limit(limit)

        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def get_brands_in_sector(
        self,
        sector: str,
        min_data_points: int = MIN_DATA_POINTS,
    ) -> List[str]:
        """Get distinct brand labels in a sector with sufficient data.

        Only returns brands that have at least min_data_points within
        the valid date range (last 5 years).

        Args:
            sector: YouGov sector_label
            min_data_points: Minimum data points required (default 12)

        Returns:
            List of brand labels with sufficient valid data
        """
        min_year, max_year = self._get_valid_year_range()

        # Get brands with data point count
        query = (
            select(
                YouGov.brand_label,
                func.count().label("data_points")
            )
            .where(
                and_(
                    YouGov.sector_label == sector,
                    extract('year', YouGov.date) >= min_year,
                    extract('year', YouGov.date) <= max_year,
                )
            )
            .group_by(YouGov.brand_label)
            .having(func.count() >= min_data_points)
            .order_by(YouGov.brand_label)
        )
        result = await self.session.execute(query)
        return [row[0] for row in result.all()]

    async def get_brands_in_sector_with_stats(
        self,
        sector: str,
        min_data_points: int = MIN_DATA_POINTS,
    ) -> List[Dict[str, Any]]:
        """Get brands in sector with data quality statistics.

        Args:
            sector: YouGov sector_label
            min_data_points: Minimum data points required

        Returns:
            List of dicts with brand_label, data_points, min_year, max_year
        """
        min_year, max_year = self._get_valid_year_range()

        query = (
            select(
                YouGov.brand_label,
                func.count().label("data_points"),
                func.min(extract('year', YouGov.date)).label("oldest_year"),
                func.max(extract('year', YouGov.date)).label("newest_year"),
            )
            .where(
                and_(
                    YouGov.sector_label == sector,
                    extract('year', YouGov.date) >= min_year,
                    extract('year', YouGov.date) <= max_year,
                )
            )
            .group_by(YouGov.brand_label)
            .having(func.count() >= min_data_points)
            .order_by(func.count().desc())
        )
        result = await self.session.execute(query)

        return [
            {
                "brand_label": row.brand_label,
                "data_points": row.data_points,
                "oldest_year": int(row.oldest_year) if row.oldest_year else None,
                "newest_year": int(row.newest_year) if row.newest_year else None,
                "data_quality": "ideal" if row.oldest_year and row.oldest_year >= (self._current_year - IDEAL_DATA_AGE_YEARS) else "acceptable",
            }
            for row in result.all()
        ]

    async def get_sectors(self) -> List[str]:
        """Get all distinct sectors (sector_label)."""
        query = select(YouGov.sector_label).distinct().order_by(YouGov.sector_label)
        result = await self.session.execute(query)
        return [row[0] for row in result.all() if row[0]]

    async def get_kpi_time_series(
        self,
        brand_label: str,
        kpi_name: str,
        year: Optional[int] = None,
        filter_valid_only: bool = True,
    ) -> List[Dict[str, Any]]:
        """Get time series for a specific KPI metric.

        Only returns data within valid range (last 5 years) by default.

        Args:
            brand_label: Brand to query
            kpi_name: One of 'adaware', 'aided', 'aware', 'consider'
            year: Optional year filter
            filter_valid_only: If True, only return data from last 5 years

        Returns:
            List of dicts with year, month, and value
        """
        # Map 'aided' to 'aware' for compatibility
        metric_name = kpi_name
        if kpi_name == "aided":
            metric_name = "aware"

        min_year, max_year = self._get_valid_year_range()

        conditions = [
            YouGov.brand_label == brand_label,
            YouGov.metric == metric_name,
            YouGov.score.isnot(None),
        ]

        # Apply valid year filter unless explicitly disabled
        if filter_valid_only:
            conditions.append(extract('year', YouGov.date) >= min_year)
            conditions.append(extract('year', YouGov.date) <= max_year)

        query = select(
            extract('year', YouGov.date).label('year'),
            extract('month', YouGov.date).label('month'),
            YouGov.score.label("value"),
        ).where(and_(*conditions))

        if year is not None:
            query = query.where(extract('year', YouGov.date) == year)

        query = query.order_by(YouGov.date)
        result = await self.session.execute(query)

        return [
            {"year": int(row.year), "month": int(row.month), "value": row.value}
            for row in result.all()
        ]

    async def get_kpi_time_series_validated(
        self,
        brand_label: str,
        kpi_name: str,
        min_data_points: int = MIN_DATA_POINTS,
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """Get validated KPI time series with quality info.

        Args:
            brand_label: Brand to query
            kpi_name: One of 'adaware', 'aided', 'aware', 'consider'
            min_data_points: Minimum required data points

        Returns:
            Tuple of (time_series, validation_info)
            validation_info contains: is_valid, data_points, quality, warnings
        """
        time_series = await self.get_kpi_time_series(
            brand_label, kpi_name, filter_valid_only=True
        )

        data_points = len(time_series)
        is_valid = data_points >= min_data_points

        # Determine data quality
        if time_series:
            newest_year = max(ts["year"] for ts in time_series)
            oldest_year = min(ts["year"] for ts in time_series)

            if oldest_year >= (self._current_year - IDEAL_DATA_AGE_YEARS):
                quality = "ideal"
            else:
                quality = "acceptable"
        else:
            newest_year = None
            oldest_year = None
            quality = "insufficient"

        warnings = []
        if not is_valid:
            warnings.append(
                f"Insufficient data: {data_points} points, need {min_data_points}"
            )
        if quality == "acceptable":
            warnings.append("Data is 4-5 years old, consider requesting fresher data")

        validation_info = {
            "is_valid": is_valid,
            "data_points": data_points,
            "min_required": min_data_points,
            "quality": quality,
            "oldest_year": oldest_year,
            "newest_year": newest_year,
            "warnings": warnings,
        }

        return time_series, validation_info

    async def get_latest_kpi(
        self,
        brand_label: str,
        kpi_name: str,
    ) -> Optional[Dict[str, Any]]:
        """Get the most recent KPI value for a brand."""
        # Map 'aided' to 'aware' for compatibility
        metric_name = kpi_name
        if kpi_name == "aided":
            metric_name = "aware"

        query = (
            select(
                extract('year', YouGov.date).label('year'),
                extract('month', YouGov.date).label('month'),
                YouGov.score.label("value"),
            )
            .where(
                and_(
                    YouGov.brand_label == brand_label,
                    YouGov.metric == metric_name,
                    YouGov.score.isnot(None),
                )
            )
            .order_by(YouGov.date.desc())
            .limit(1)
        )

        result = await self.session.execute(query)
        row = result.first()

        if row:
            return {"year": int(row.year), "month": int(row.month), "value": row.value}
        return None

    async def get_sector_average(
        self,
        sector: str,
        kpi_name: str,
        year: int,
        month: Optional[int] = None,
    ) -> Optional[Decimal]:
        """Get sector average for a KPI metric."""
        # Map 'aided' to 'aware' for compatibility
        metric_name = kpi_name
        if kpi_name == "aided":
            metric_name = "aware"

        conditions = [
            YouGov.sector_label == sector,
            YouGov.metric == metric_name,
            extract('year', YouGov.date) == year,
            YouGov.score.isnot(None),
        ]

        if month is not None:
            conditions.append(extract('month', YouGov.date) == month)

        query = select(func.avg(YouGov.score)).where(and_(*conditions))

        result = await self.session.execute(query)
        avg = result.scalar_one_or_none()
        return Decimal(str(avg)) if avg else None

    async def get_kpi_matrix(
        self,
        brand_labels: List[str],
        year: int,
    ) -> List[Dict[str, Any]]:
        """Get KPI matrix for multiple brands.

        Returns a list of dicts with brand, month, and all KPI values.
        Note: New table stores each metric in separate rows, so we pivot.
        """
        query = (
            select(
                YouGov.brand_label,
                extract('month', YouGov.date).label('month'),
                YouGov.metric,
                YouGov.score,
            )
            .where(
                and_(
                    YouGov.brand_label.in_(brand_labels),
                    extract('year', YouGov.date) == year,
                )
            )
            .order_by(YouGov.brand_label, YouGov.date)
        )

        result = await self.session.execute(query)

        # Pivot the data
        pivoted = {}
        for row in result.all():
            key = (row.brand_label, int(row.month))
            if key not in pivoted:
                pivoted[key] = {
                    "brand_label": row.brand_label,
                    "month": int(row.month),
                    "adaware": None,
                    "aided": None,  # maps to 'aware'
                    "consider": None,
                }
            metric = row.metric
            if metric == "aware":
                pivoted[key]["aided"] = row.score
            elif metric in ["adaware", "consider"]:
                pivoted[key][metric] = row.score

        return list(pivoted.values())

    async def get_year_range(self) -> tuple[Optional[int], Optional[int]]:
        """Get the range of years available in the data."""
        query = select(
            func.min(extract('year', YouGov.date)),
            func.max(extract('year', YouGov.date))
        )
        result = await self.session.execute(query)
        row = result.one()
        min_year = int(row[0]) if row[0] else None
        max_year = int(row[1]) if row[1] else None
        return min_year, max_year

    async def get_valid_year_range(self) -> tuple[int, int]:
        """Get the valid year range for analysis (last 5 years only).

        Returns:
            Tuple of (min_valid_year, current_year)
        """
        return self._get_valid_year_range()

    async def count_valid_data_points(
        self,
        brand_label: str,
        kpi_name: str,
    ) -> int:
        """Count valid data points for a brand (within last 5 years).

        Args:
            brand_label: Brand to check
            kpi_name: KPI metric name

        Returns:
            Number of valid data points
        """
        # Map 'aided' to 'aware' for compatibility
        metric_name = kpi_name
        if kpi_name == "aided":
            metric_name = "aware"

        min_year, max_year = self._get_valid_year_range()

        query = select(func.count()).where(
            and_(
                YouGov.brand_label == brand_label,
                YouGov.metric == metric_name,
                extract('year', YouGov.date) >= min_year,
                extract('year', YouGov.date) <= max_year,
                YouGov.score.isnot(None),
            )
        )

        result = await self.session.execute(query)
        return result.scalar_one() or 0

    async def has_sufficient_data(
        self,
        brand_label: str,
        kpi_name: str,
        min_points: int = MIN_DATA_POINTS,
    ) -> bool:
        """Check if brand has sufficient valid data points.

        Args:
            brand_label: Brand to check
            kpi_name: KPI metric name
            min_points: Minimum required (default 12)

        Returns:
            True if brand has at least min_points valid data points
        """
        count = await self.count_valid_data_points(brand_label, kpi_name)
        return count >= min_points
