"""Nielsen spend data repository.

Search Priority: Nielsen is searched AFTER YouGov (YouGov first, then Nielsen).
Data Requirements:
- Minimum 12 data points required
- Data age: 2-3 years ideal, 4-5 years acceptable, >5 years rejected

NOTE: Updated to use the new 'nielsen' table (Nielsen model) instead of
the deprecated 'nielsen_spend' table (NielsenSpend model).
Column mappings:
- brand_name -> marke
- year -> jahr
- month -> monat (German month names)
- channel -> mediengruppe
- spend_eur -> teuro (thousands EUR)
"""

from datetime import datetime
from decimal import Decimal
from typing import List, Optional, Dict, Any, Tuple

from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.data import Nielsen
from src.repositories.base import BaseRepository

# Data validation constants
MIN_DATA_POINTS = 12          # Minimum required data points
MAX_DATA_AGE_YEARS = 5        # Data older than 5 years is rejected
IDEAL_DATA_AGE_YEARS = 3      # 2-3 years is ideal

# Month mapping for German month names to numbers
MONTH_TO_NUM = {
    "Januar": 1, "Februar": 2, "März": 3, "April": 4,
    "Mai": 5, "Juni": 6, "Juli": 7, "August": 8,
    "September": 9, "Oktober": 10, "November": 11, "Dezember": 12,
}


class NielsenRepository(BaseRepository[Nielsen]):
    """Repository for Nielsen advertising spend data.

    This is the SECONDARY data source - search YouGov FIRST, then Nielsen.

    Data Validation Rules:
    - Minimum 12 data points required for valid analysis
    - 2-3 years old: IDEAL quality
    - 4-5 years old: ACCEPTABLE quality
    - >5 years old: REJECTED (not used)
    """

    def __init__(self, session: AsyncSession):
        super().__init__(session, Nielsen)
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
        brand_name: str,
        year: Optional[int] = None,
        month: Optional[int] = None,
    ) -> List[Nielsen]:
        """Get spend data for a specific brand (marke)."""
        query = select(Nielsen).where(Nielsen.marke == brand_name)

        if year is not None:
            query = query.where(Nielsen.jahr == year)
        # Note: month param is numeric but Nielsen uses German month names
        # For now we skip month filtering when using new table

        query = query.order_by(Nielsen.jahr, Nielsen.monat)
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def get_by_wirtschaftsgruppe(
        self,
        wirtschaftsgruppe: str,
        year: Optional[int] = None,
        limit: int = 1000,
    ) -> List[Nielsen]:
        """Get spend data for an industry classification."""
        query = select(Nielsen).where(
            Nielsen.wirtschaftsgruppe == wirtschaftsgruppe
        )

        if year is not None:
            query = query.where(Nielsen.jahr == year)

        query = query.order_by(Nielsen.marke, Nielsen.jahr, Nielsen.monat)
        query = query.limit(limit)

        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def get_brands_in_industry(
        self,
        wirtschaftsgruppe: str,
        min_data_points: int = MIN_DATA_POINTS,
    ) -> List[str]:
        """Get distinct brand names (marke) in an industry with sufficient data.

        Only returns brands that have at least min_data_points within
        the valid date range (last 5 years).

        Args:
            wirtschaftsgruppe: Industry classification
            min_data_points: Minimum data points required (default 12)

        Returns:
            List of brand names with sufficient valid data
        """
        min_year, max_year = self._get_valid_year_range()

        # Get brands with data point count
        query = (
            select(
                Nielsen.marke,
                func.count().label("data_points")
            )
            .where(
                and_(
                    Nielsen.wirtschaftsgruppe == wirtschaftsgruppe,
                    Nielsen.jahr >= min_year,
                    Nielsen.jahr <= max_year,
                )
            )
            .group_by(Nielsen.marke)
            .having(func.count() >= min_data_points)
            .order_by(Nielsen.marke)
        )
        result = await self.session.execute(query)
        return [row[0] for row in result.all()]

    async def get_brands_in_industry_with_stats(
        self,
        wirtschaftsgruppe: str,
        min_data_points: int = MIN_DATA_POINTS,
    ) -> List[Dict[str, Any]]:
        """Get brands in industry with data quality statistics.

        Args:
            wirtschaftsgruppe: Industry classification
            min_data_points: Minimum data points required

        Returns:
            List of dicts with brand_name, data_points, min_year, max_year, total_spend
        """
        min_year, max_year = self._get_valid_year_range()

        query = (
            select(
                Nielsen.marke,
                func.count().label("data_points"),
                func.min(Nielsen.jahr).label("oldest_year"),
                func.max(Nielsen.jahr).label("newest_year"),
                func.sum(Nielsen.teuro).label("total_spend"),
            )
            .where(
                and_(
                    Nielsen.wirtschaftsgruppe == wirtschaftsgruppe,
                    Nielsen.jahr >= min_year,
                    Nielsen.jahr <= max_year,
                )
            )
            .group_by(Nielsen.marke)
            .having(func.count() >= min_data_points)
            .order_by(func.sum(Nielsen.teuro).desc())
        )
        result = await self.session.execute(query)

        return [
            {
                "brand_name": row.marke,
                "data_points": row.data_points,
                "oldest_year": row.oldest_year,
                "newest_year": row.newest_year,
                "total_spend_eur": row.total_spend,
                "data_quality": "ideal" if row.oldest_year >= (self._current_year - IDEAL_DATA_AGE_YEARS) else "acceptable",
            }
            for row in result.all()
        ]

    async def get_channels(self) -> List[str]:
        """Get all distinct advertising channels (mediengruppe)."""
        query = select(Nielsen.mediengruppe).distinct().order_by(Nielsen.mediengruppe)
        result = await self.session.execute(query)
        return [row[0] for row in result.all() if row[0]]

    async def get_spend_by_channel(
        self,
        brand_name: str,
        year: Optional[int] = None,
        month: Optional[int] = None,
        filter_valid_only: bool = True,
    ) -> Dict[str, Decimal]:
        """Get spend breakdown by channel (mediengruppe) for a brand (marke).

        Args:
            brand_name: Brand (marke) to query
            year: Optional specific year (if None, uses all valid years)
            month: Optional specific month (ignored for new table)
            filter_valid_only: If True, only include data from last 5 years

        Returns:
            Dict of channel -> total_spend (in thousands EUR)
        """
        min_year, max_year = self._get_valid_year_range()

        conditions = [Nielsen.marke == brand_name]

        if year is not None:
            conditions.append(Nielsen.jahr == year)
        elif filter_valid_only:
            conditions.append(Nielsen.jahr >= min_year)
            conditions.append(Nielsen.jahr <= max_year)

        query = select(
            Nielsen.mediengruppe,
            func.sum(Nielsen.teuro).label("total_spend"),
        ).where(and_(*conditions)).group_by(Nielsen.mediengruppe)

        result = await self.session.execute(query)

        return {row.mediengruppe: Decimal(str(row.total_spend)) if row.total_spend else Decimal(0) for row in result.all() if row.mediengruppe}

    async def get_total_spend(
        self,
        brand_name: str,
        year: int,
        month: Optional[int] = None,
    ) -> Optional[Decimal]:
        """Get total spend for a brand (marke) in a time period."""
        query = select(func.sum(Nielsen.teuro)).where(
            and_(
                Nielsen.marke == brand_name,
                Nielsen.jahr == year,
            )
        )

        # Month filtering not supported for German month names

        result = await self.session.execute(query)
        total = result.scalar_one_or_none()
        return Decimal(str(total)) if total else None

    async def get_spend_matrix(
        self,
        brand_names: List[str],
        year: int,
    ) -> List[Dict[str, Any]]:
        """Get spend matrix for multiple brands.

        Returns a list of dicts with brand, channel, and monthly spend data.
        """
        query = (
            select(
                Nielsen.marke,
                Nielsen.mediengruppe,
                Nielsen.monat,
                func.sum(Nielsen.teuro).label("spend"),
            )
            .where(
                and_(
                    Nielsen.marke.in_(brand_names),
                    Nielsen.jahr == year,
                )
            )
            .group_by(Nielsen.marke, Nielsen.mediengruppe, Nielsen.monat)
            .order_by(Nielsen.marke, Nielsen.mediengruppe, Nielsen.monat)
        )

        result = await self.session.execute(query)
        return [
            {
                "brand_name": row.marke,
                "channel": row.mediengruppe,
                "month": MONTH_TO_NUM.get(row.monat, 0),  # Convert German month to number
                "spend": Decimal(str(row.spend)) if row.spend else Decimal(0),
            }
            for row in result.all()
        ]

    async def get_year_range(self) -> tuple[Optional[int], Optional[int]]:
        """Get the range of years (jahr) available in the data."""
        query = select(func.min(Nielsen.jahr), func.max(Nielsen.jahr))
        result = await self.session.execute(query)
        row = result.one()
        return row[0], row[1]

    async def get_wirtschaftsgruppen(self) -> List[str]:
        """Get all distinct Wirtschaftsgruppen."""
        query = (
            select(Nielsen.wirtschaftsgruppe)
            .distinct()
            .order_by(Nielsen.wirtschaftsgruppe)
        )
        result = await self.session.execute(query)
        return [row[0] for row in result.all() if row[0]]

    async def get_valid_year_range(self) -> tuple[int, int]:
        """Get the valid year range for analysis (last 5 years only).

        Returns:
            Tuple of (min_valid_year, current_year)
        """
        return self._get_valid_year_range()

    async def count_valid_data_points(
        self,
        brand_name: str,
    ) -> int:
        """Count valid data points for a brand (marke) within last 5 years.

        Args:
            brand_name: Brand (marke) to check

        Returns:
            Number of valid data points
        """
        min_year, max_year = self._get_valid_year_range()

        query = select(func.count()).where(
            and_(
                Nielsen.marke == brand_name,
                Nielsen.jahr >= min_year,
                Nielsen.jahr <= max_year,
            )
        )

        result = await self.session.execute(query)
        return result.scalar_one() or 0

    async def has_sufficient_data(
        self,
        brand_name: str,
        min_points: int = MIN_DATA_POINTS,
    ) -> bool:
        """Check if brand has sufficient valid data points.

        Args:
            brand_name: Brand to check
            min_points: Minimum required (default 12)

        Returns:
            True if brand has at least min_points valid data points
        """
        count = await self.count_valid_data_points(brand_name)
        return count >= min_points

    async def get_spend_time_series_validated(
        self,
        brand_name: str,
        min_data_points: int = MIN_DATA_POINTS,
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """Get validated spend time series with quality info.

        Args:
            brand_name: Brand (marke) to query
            min_data_points: Minimum required data points

        Returns:
            Tuple of (time_series, validation_info)
        """
        min_year, max_year = self._get_valid_year_range()

        query = (
            select(
                Nielsen.jahr,
                Nielsen.monat,
                Nielsen.mediengruppe,
                Nielsen.teuro,
            )
            .where(
                and_(
                    Nielsen.marke == brand_name,
                    Nielsen.jahr >= min_year,
                    Nielsen.jahr <= max_year,
                )
            )
            .order_by(Nielsen.jahr, Nielsen.monat)
        )

        result = await self.session.execute(query)
        rows = result.all()

        time_series = [
            {
                "year": row.jahr,
                "month": MONTH_TO_NUM.get(row.monat, 0),
                "channel": row.mediengruppe,
                "spend_eur": Decimal(str(row.teuro)) if row.teuro else Decimal(0),
            }
            for row in rows
        ]

        # Calculate unique month count
        unique_months = len(set((r["year"], r["month"]) for r in time_series))
        is_valid = unique_months >= min_data_points

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
                f"Insufficient data: {unique_months} months, need {min_data_points}"
            )
        if quality == "acceptable":
            warnings.append("Data is 4-5 years old, consider requesting fresher data")

        validation_info = {
            "is_valid": is_valid,
            "data_points": len(time_series),
            "unique_months": unique_months,
            "min_required": min_data_points,
            "quality": quality,
            "oldest_year": oldest_year,
            "newest_year": newest_year,
            "warnings": warnings,
        }

        return time_series, validation_info
