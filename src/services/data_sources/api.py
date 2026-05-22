"""API implementation of the data source interface (stub).

This is a stub for future Nielsen/YouGov API integration.
Implement these methods when API access is provided.
"""

from typing import List, Dict, Any, Optional

from src.services.data_sources.base import DataSourceInterface


class APIDataSource(DataSourceInterface):
    """API-backed data source for Nielsen/YouGov.

    This is a stub implementation - all methods raise NotImplementedError.
    Implement when API access is provided.
    """

    def __init__(
        self,
        nielsen_client: Any = None,
        yougov_client: Any = None,
        nielsen_api_key: Optional[str] = None,
        yougov_api_key: Optional[str] = None,
    ):
        """Initialize with API clients.

        Args:
            nielsen_client: Nielsen API client (future).
            yougov_client: YouGov API client (future).
            nielsen_api_key: Nielsen API key (alternative to client).
            yougov_api_key: YouGov API key (alternative to client).
        """
        self.nielsen = nielsen_client
        self.yougov = yougov_client
        self.nielsen_api_key = nielsen_api_key
        self.yougov_api_key = yougov_api_key

    def _not_implemented(self, method_name: str) -> None:
        """Raise NotImplementedError with helpful message."""
        raise NotImplementedError(
            f"APIDataSource.{method_name}() not implemented. "
            "API integration pending - waiting for Nielsen/YouGov API access."
        )

    # =========================================================================
    # SECTOR QUERIES
    # =========================================================================

    async def get_yougov_sectors(self) -> List[str]:
        """Get all distinct YouGov sector labels."""
        self._not_implemented("get_yougov_sectors")
        # TODO: Implement when YouGov API access provided
        # return await self.yougov.get_sectors()

    async def get_nielsen_sectors(self) -> List[str]:
        """Get all distinct Nielsen Wirtschaftsgruppe values."""
        self._not_implemented("get_nielsen_sectors")
        # TODO: Implement when Nielsen API access provided
        # return await self.nielsen.get_sectors()

    # =========================================================================
    # BRAND QUERIES
    # =========================================================================

    async def get_yougov_brands(self, sectors: List[str]) -> List[str]:
        """Get distinct YouGov brand labels within given sectors."""
        self._not_implemented("get_yougov_brands")
        # TODO: Implement when YouGov API access provided
        # return await self.yougov.get_brands(sectors=sectors)

    async def get_nielsen_brands(self, wirtschaftsgruppen: List[str]) -> List[str]:
        """Get distinct Nielsen Marke values within given sectors."""
        self._not_implemented("get_nielsen_brands")
        # TODO: Implement when Nielsen API access provided
        # return await self.nielsen.get_brands(wirtschaftsgruppen=wirtschaftsgruppen)

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
        self._not_implemented("get_brand_kpi_scores")
        # TODO: Implement when YouGov API access provided
        # return await self.yougov.get_kpi_scores(
        #     sectors=sectors,
        #     metrics=metrics,
        #     date_from=date_from,
        #     date_to=date_to,
        # )

    async def get_brand_latest_kpi(self, brand_label: str) -> Dict[str, Any]:
        """Get latest KPI scores for a specific brand."""
        self._not_implemented("get_brand_latest_kpi")
        # TODO: Implement when YouGov API access provided
        # return await self.yougov.get_latest_kpi(brand_label=brand_label)

    # =========================================================================
    # SPEND DATA QUERIES
    # =========================================================================

    async def get_brand_spend_data(
        self,
        brand: str,
        years: Optional[List[int]] = None,
    ) -> List[Dict[str, Any]]:
        """Get Nielsen spend data for a specific brand."""
        self._not_implemented("get_brand_spend_data")
        # TODO: Implement when Nielsen API access provided
        # return await self.nielsen.get_spend_data(brand=brand, years=years)

    async def get_brand_total_spend(
        self,
        brand: str,
        years: Optional[List[int]] = None,
    ) -> float:
        """Get total spend across all channels for a brand."""
        self._not_implemented("get_brand_total_spend")
        # TODO: Implement when Nielsen API access provided
        # return await self.nielsen.get_total_spend(brand=brand, years=years)

    async def get_brand_spend_by_channel(
        self,
        brand: str,
        years: Optional[List[int]] = None,
        limit: int = 5,
    ) -> List[Dict[str, Any]]:
        """Get top channels by spend for a brand."""
        self._not_implemented("get_brand_spend_by_channel")
        # TODO: Implement when Nielsen API access provided
        # return await self.nielsen.get_spend_by_channel(
        #     brand=brand, years=years, limit=limit
        # )

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
        self._not_implemented("get_yougov_competitors")
        # TODO: Implement when YouGov API access provided
        # return await self.yougov.get_competitors(
        #     sectors=sectors,
        #     exclude_brand=exclude_brand,
        #     primary_kpi=primary_kpi,
        #     target_score=target_score,
        #     limit=limit,
        # )

    async def get_nielsen_competitors(
        self,
        wirtschaftsgruppen: List[str],
        exclude_brand: str,
        years: Optional[List[int]] = None,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """Get competitors ranked by spend."""
        self._not_implemented("get_nielsen_competitors")
        # TODO: Implement when Nielsen API access provided
        # return await self.nielsen.get_competitors(
        #     wirtschaftsgruppen=wirtschaftsgruppen,
        #     exclude_brand=exclude_brand,
        #     years=years,
        #     limit=limit,
        # )
