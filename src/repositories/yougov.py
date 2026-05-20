"""YouGov KPI data repository."""

from decimal import Decimal
from typing import List, Optional, Dict, Any

from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import YouGovKPI
from src.repositories.base import BaseRepository


class YouGovRepository(BaseRepository[YouGovKPI]):
    """Repository for YouGov brand KPI data."""

    def __init__(self, session: AsyncSession):
        super().__init__(session, YouGovKPI)

    async def get_by_brand(
        self,
        brand_label: str,
        year: Optional[int] = None,
        month: Optional[int] = None,
    ) -> List[YouGovKPI]:
        """Get KPI data for a specific brand."""
        query = select(YouGovKPI).where(YouGovKPI.brand_label == brand_label)

        if year is not None:
            query = query.where(YouGovKPI.year == year)
        if month is not None:
            query = query.where(YouGovKPI.month == month)

        query = query.order_by(YouGovKPI.year, YouGovKPI.month)
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def get_by_sector(
        self,
        sector: str,
        year: Optional[int] = None,
        limit: int = 1000,
    ) -> List[YouGovKPI]:
        """Get KPI data for a sector."""
        query = select(YouGovKPI).where(YouGovKPI.sector == sector)

        if year is not None:
            query = query.where(YouGovKPI.year == year)

        query = query.order_by(YouGovKPI.brand_label, YouGovKPI.year, YouGovKPI.month)
        query = query.limit(limit)

        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def get_brands_in_sector(self, sector: str) -> List[str]:
        """Get distinct brand labels in a sector."""
        query = (
            select(YouGovKPI.brand_label)
            .where(YouGovKPI.sector == sector)
            .distinct()
            .order_by(YouGovKPI.brand_label)
        )
        result = await self.session.execute(query)
        return [row[0] for row in result.all()]

    async def get_sectors(self) -> List[str]:
        """Get all distinct sectors."""
        query = select(YouGovKPI.sector).distinct().order_by(YouGovKPI.sector)
        result = await self.session.execute(query)
        return [row[0] for row in result.all()]

    async def get_kpi_time_series(
        self,
        brand_label: str,
        kpi_name: str,
        year: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Get time series for a specific KPI metric.

        Args:
            brand_label: Brand to query
            kpi_name: One of 'adaware', 'aided', 'consider'
            year: Optional year filter

        Returns:
            List of dicts with year, month, and value
        """
        if kpi_name not in ["adaware", "aided", "consider"]:
            raise ValueError(f"Invalid KPI name: {kpi_name}")

        kpi_column = getattr(YouGovKPI, kpi_name)

        query = select(
            YouGovKPI.year,
            YouGovKPI.month,
            kpi_column.label("value"),
        ).where(
            and_(
                YouGovKPI.brand_label == brand_label,
                kpi_column.isnot(None),
            )
        )

        if year is not None:
            query = query.where(YouGovKPI.year == year)

        query = query.order_by(YouGovKPI.year, YouGovKPI.month)
        result = await self.session.execute(query)

        return [
            {"year": row.year, "month": row.month, "value": row.value}
            for row in result.all()
        ]

    async def get_latest_kpi(
        self,
        brand_label: str,
        kpi_name: str,
    ) -> Optional[Dict[str, Any]]:
        """Get the most recent KPI value for a brand."""
        if kpi_name not in ["adaware", "aided", "consider"]:
            raise ValueError(f"Invalid KPI name: {kpi_name}")

        kpi_column = getattr(YouGovKPI, kpi_name)

        query = (
            select(
                YouGovKPI.year,
                YouGovKPI.month,
                kpi_column.label("value"),
            )
            .where(
                and_(
                    YouGovKPI.brand_label == brand_label,
                    kpi_column.isnot(None),
                )
            )
            .order_by(YouGovKPI.year.desc(), YouGovKPI.month.desc())
            .limit(1)
        )

        result = await self.session.execute(query)
        row = result.first()

        if row:
            return {"year": row.year, "month": row.month, "value": row.value}
        return None

    async def get_sector_average(
        self,
        sector: str,
        kpi_name: str,
        year: int,
        month: Optional[int] = None,
    ) -> Optional[Decimal]:
        """Get sector average for a KPI metric."""
        if kpi_name not in ["adaware", "aided", "consider"]:
            raise ValueError(f"Invalid KPI name: {kpi_name}")

        kpi_column = getattr(YouGovKPI, kpi_name)

        query = select(func.avg(kpi_column)).where(
            and_(
                YouGovKPI.sector == sector,
                YouGovKPI.year == year,
                kpi_column.isnot(None),
            )
        )

        if month is not None:
            query = query.where(YouGovKPI.month == month)

        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def get_kpi_matrix(
        self,
        brand_labels: List[str],
        year: int,
    ) -> List[Dict[str, Any]]:
        """Get KPI matrix for multiple brands.

        Returns a list of dicts with brand, month, and all KPI values.
        """
        query = (
            select(
                YouGovKPI.brand_label,
                YouGovKPI.month,
                YouGovKPI.adaware,
                YouGovKPI.aided,
                YouGovKPI.consider,
            )
            .where(
                and_(
                    YouGovKPI.brand_label.in_(brand_labels),
                    YouGovKPI.year == year,
                )
            )
            .order_by(YouGovKPI.brand_label, YouGovKPI.month)
        )

        result = await self.session.execute(query)
        return [
            {
                "brand_label": row.brand_label,
                "month": row.month,
                "adaware": row.adaware,
                "aided": row.aided,
                "consider": row.consider,
            }
            for row in result.all()
        ]

    async def get_year_range(self) -> tuple[Optional[int], Optional[int]]:
        """Get the range of years available in the data."""
        query = select(func.min(YouGovKPI.year), func.max(YouGovKPI.year))
        result = await self.session.execute(query)
        row = result.one()
        return row[0], row[1]
