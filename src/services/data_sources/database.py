"""Database implementation of the data source interface.

This is the current production implementation that queries PostgreSQL
tables for YouGov and Nielsen data.
"""

from typing import List, Dict, Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from src.services.data_sources.base import DataSourceInterface
from src.services.stage1.repository import Stage1Repository


class DatabaseDataSource(DataSourceInterface):
    """Database-backed data source using existing Stage1Repository.

    Wraps the existing repository queries to conform to the DataSourceInterface.
    """

    def __init__(self, session: AsyncSession):
        """Initialize with database session.

        Args:
            session: SQLAlchemy async session for database queries.
        """
        self.session = session
        self.repo = Stage1Repository(session)

    # =========================================================================
    # SECTOR QUERIES
    # =========================================================================

    async def get_yougov_sectors(self) -> List[str]:
        """Get all distinct YouGov sector labels."""
        return await self.repo.get_distinct_yougov_sectors()

    async def get_nielsen_sectors(self) -> List[str]:
        """Get all distinct Nielsen Wirtschaftsgruppe values."""
        return await self.repo.get_distinct_nielsen_sectors()

    # =========================================================================
    # BRAND QUERIES
    # =========================================================================

    async def get_yougov_brands(self, sectors: List[str]) -> List[str]:
        """Get distinct YouGov brand labels within given sectors."""
        return await self.repo.get_distinct_yougov_brands(sectors)

    async def get_nielsen_brands(self, wirtschaftsgruppen: List[str]) -> List[str]:
        """Get distinct Nielsen Marke values within given sectors."""
        return await self.repo.get_distinct_nielsen_brands(wirtschaftsgruppen)

    # =========================================================================
    # KPI DATA QUERIES
    # =========================================================================

    async def get_brand_kpi_scores(
        self,
        sectors: List[str],
        metrics: Optional[List[str]] = None,
        date_from: str = "2023-01-01",
        date_to: str = "2025-12-31",
    ) -> List[Dict[str, Any]]:
        """Get brand KPI scores from YouGov for competitor retrieval."""
        return await self.repo.get_yougov_brand_kpi_scores(
            sectors=sectors,
            metrics=metrics,
            date_from=date_from,
            date_to=date_to,
        )

    async def get_brand_latest_kpi(self, brand_label: str) -> Dict[str, Any]:
        """Get latest KPI scores for a specific brand."""
        return await self.repo.get_yougov_latest_scores(brand_label)

    # =========================================================================
    # SPEND DATA QUERIES
    # =========================================================================

    async def get_brand_spend_data(
        self,
        brand: str,
        years: Optional[List[int]] = None,
    ) -> List[Dict[str, Any]]:
        """Get Nielsen spend data for a specific brand."""
        return await self.repo.get_nielsen_brand_spend(marke=brand, years=years)

    async def get_brand_total_spend(
        self,
        brand: str,
        years: Optional[List[int]] = None,
    ) -> float:
        """Get total spend across all channels for a brand."""
        return await self.repo.get_nielsen_brand_total_spend(marke=brand, years=years)

    async def get_brand_spend_by_channel(
        self,
        brand: str,
        years: Optional[List[int]] = None,
        limit: int = 5,
    ) -> List[Dict[str, Any]]:
        """Get top channels by spend for a brand."""
        return await self.repo.get_nielsen_spend_by_channel(
            marke=brand, years=years, limit=limit
        )

    # =========================================================================
    # COMPETITOR QUERIES
    # =========================================================================

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
        """Get competitors ranked by KPI score proximity."""
        return await self.repo.get_yougov_competitors(
            sectors=sectors,
            exclude_brand=exclude_brand,
            primary_kpi=primary_kpi,
            target_score=target_score,
            date_from=date_from,
            date_to=date_to,
            limit=limit,
        )

    async def get_nielsen_competitors(
        self,
        wirtschaftsgruppen: List[str],
        exclude_brand: str,
        years: Optional[List[int]] = None,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """Get competitors ranked by spend."""
        return await self.repo.get_nielsen_competitors(
            wirtschaftsgruppen=wirtschaftsgruppen,
            exclude_brand=exclude_brand,
            years=years,
            limit=limit,
        )
