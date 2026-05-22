"""Abstract data source interface for YouGov and Nielsen data.

This interface allows the Stage1Orchestrator to work with either:
- Database queries (current implementation)
- Future Nielsen/YouGov APIs (when API access is provided)
"""

from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional


class DataSourceInterface(ABC):
    """Abstract interface for data sources (DB or API).

    All methods are async to support both database queries and HTTP API calls.
    """

    # =========================================================================
    # SECTOR QUERIES (for AI resolution)
    # =========================================================================

    @abstractmethod
    async def get_yougov_sectors(self) -> List[str]:
        """Get all distinct YouGov sector labels.

        Used by AI Call #1 for industry resolution.
        """
        pass

    @abstractmethod
    async def get_nielsen_sectors(self) -> List[str]:
        """Get all distinct Nielsen Wirtschaftsgruppe values.

        Used by AI Call #1 for industry resolution.
        """
        pass

    # =========================================================================
    # BRAND QUERIES (for AI resolution)
    # =========================================================================

    @abstractmethod
    async def get_yougov_brands(self, sectors: List[str]) -> List[str]:
        """Get distinct YouGov brand labels within given sectors.

        Used by AI Call #2 for brand resolution.
        """
        pass

    @abstractmethod
    async def get_nielsen_brands(self, wirtschaftsgruppen: List[str]) -> List[str]:
        """Get distinct Nielsen Marke values within given sectors.

        Used by AI Call #2 for brand resolution.
        """
        pass

    # =========================================================================
    # KPI DATA QUERIES
    # =========================================================================

    @abstractmethod
    async def get_brand_kpi_scores(
        self,
        sectors: List[str],
        metrics: Optional[List[str]] = None,
        date_from: str = "2023-01-01",
        date_to: str = "2025-12-31",
    ) -> List[Dict[str, Any]]:
        """Get brand KPI scores from YouGov for competitor retrieval.

        Returns list of dicts with brand_label, metric, avg_score.
        """
        pass

    @abstractmethod
    async def get_brand_latest_kpi(self, brand_label: str) -> Dict[str, Any]:
        """Get latest KPI scores for a specific brand.

        Returns dict mapping metric name to score/date.
        """
        pass

    # =========================================================================
    # SPEND DATA QUERIES
    # =========================================================================

    @abstractmethod
    async def get_brand_spend_data(
        self,
        brand: str,
        years: Optional[List[int]] = None,
    ) -> List[Dict[str, Any]]:
        """Get Nielsen spend data for a specific brand.

        Returns list of dicts with mediengruppe, total_spend, jahr, monat.
        """
        pass

    @abstractmethod
    async def get_brand_total_spend(
        self,
        brand: str,
        years: Optional[List[int]] = None,
    ) -> float:
        """Get total spend across all channels for a brand.

        Returns total in T-EUR.
        """
        pass

    @abstractmethod
    async def get_brand_spend_by_channel(
        self,
        brand: str,
        years: Optional[List[int]] = None,
        limit: int = 5,
    ) -> List[Dict[str, Any]]:
        """Get top channels by spend for a brand.

        Returns list of dicts with mediengruppe, total_spend.
        """
        pass

    # =========================================================================
    # COMPETITOR QUERIES
    # =========================================================================

    @abstractmethod
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
        """Get competitors ranked by KPI score proximity.

        Returns list of dicts with brand_label, metric, avg_score.
        """
        pass

    @abstractmethod
    async def get_nielsen_competitors(
        self,
        wirtschaftsgruppen: List[str],
        exclude_brand: str,
        years: Optional[List[int]] = None,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """Get competitors ranked by spend.

        Returns list of dicts with marke, total_spend.
        """
        pass
