"""Stage 1: Competitor Matching Service.

This module implements the competitor matching pipeline:
1. Industry Lookup: Wirtschaftsgruppe → sector_label
2. YouGov Brand Query: Get brands in sector with KPI data
3. Nielsen Brand Resolution: Map YouGov brands to Nielsen entries
4. Competitor Set Assembly: Combine data sources into final competitor set
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import List, Optional, Dict, Any, Tuple
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from src.repositories import (
    IndustryMapRepository,
    BrandMapRepository,
    NielsenRepository,
    YouGovRepository,
)


@dataclass
class CompetitorBrandInfo:
    """Information about a single competitor brand."""

    nielsen_brand: str
    yougov_brand_label: Optional[str] = None
    wirtschaftsgruppe: str = ""
    has_nielsen_data: bool = False
    has_yougov_data: bool = False
    total_spend_eur: Optional[Decimal] = None
    latest_kpi_value: Optional[Decimal] = None
    match_confidence: Optional[float] = None
    data_months: int = 0  # Number of months with data


@dataclass
class CompetitorSetResult:
    """Result of competitor matching process."""

    industry: str
    sector_label: str
    competitors: List[CompetitorBrandInfo]
    total_competitors: int = 0
    competitors_with_full_data: int = 0
    warnings: List[str] = field(default_factory=list)
    suggestions: List[str] = field(default_factory=list)
    is_feasible: bool = True
    error_message: Optional[str] = None


class IndustryLookupService:
    """Service for industry classification lookup.

    Maps Nielsen Wirtschaftsgruppe to YouGov sector_label.
    """

    def __init__(self, session: AsyncSession):
        self.session = session
        self.industry_repo = IndustryMapRepository(session)

    async def lookup_sector(self, wirtschaftsgruppe: str) -> Optional[str]:
        """Look up the YouGov sector label for a Wirtschaftsgruppe.

        Args:
            wirtschaftsgruppe: Nielsen industry classification

        Returns:
            YouGov sector_label or None if not found
        """
        return await self.industry_repo.get_sector_label(wirtschaftsgruppe)

    async def get_all_industries(self) -> List[str]:
        """Get all available Wirtschaftsgruppen with mappings."""
        return await self.industry_repo.get_all_wirtschaftsgruppen()

    async def find_similar_industries(
        self, wirtschaftsgruppe: str, limit: int = 5
    ) -> List[str]:
        """Find similar industry names for suggestions.

        Uses simple substring matching for MVP.
        More sophisticated fuzzy matching can be added later.
        """
        all_industries = await self.get_all_industries()

        # Simple matching: check if input is substring or vice versa
        wirtschaftsgruppe_lower = wirtschaftsgruppe.lower()
        matches = []

        for industry in all_industries:
            industry_lower = industry.lower()
            if (wirtschaftsgruppe_lower in industry_lower or
                industry_lower in wirtschaftsgruppe_lower):
                matches.append(industry)

        # Also check word overlap
        input_words = set(wirtschaftsgruppe_lower.split())
        for industry in all_industries:
            if industry in matches:
                continue
            industry_words = set(industry.lower().split())
            if input_words & industry_words:  # Any word overlap
                matches.append(industry)

        return matches[:limit]


class YouGovBrandQueryService:
    """Service for querying YouGov brand data."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.yougov_repo = YouGovRepository(session)

    async def get_brands_in_sector(
        self,
        sector: str,
        kpi_name: str = "adaware",
        min_data_months: int = 3,
    ) -> List[Dict[str, Any]]:
        """Get brands in a sector with KPI data.

        Args:
            sector: YouGov sector label
            kpi_name: KPI metric to check (adaware, aided, consider)
            min_data_months: Minimum months of data required

        Returns:
            List of brand info dicts with data availability
        """
        brands = await self.yougov_repo.get_brands_in_sector(sector)
        brand_info = []

        for brand_label in brands:
            # Get time series to check data coverage
            time_series = await self.yougov_repo.get_kpi_time_series(
                brand_label, kpi_name
            )

            if len(time_series) >= min_data_months:
                latest = await self.yougov_repo.get_latest_kpi(brand_label, kpi_name)
                brand_info.append({
                    "brand_label": brand_label,
                    "sector": sector,
                    "data_months": len(time_series),
                    "latest_value": latest["value"] if latest else None,
                    "latest_date": f"{latest['year']}-{latest['month']:02d}" if latest else None,
                })

        return brand_info

    async def get_available_sectors(self) -> List[str]:
        """Get all available sectors in YouGov data."""
        return await self.yougov_repo.get_sectors()

    async def check_kpi_availability(
        self, brand_label: str, kpi_name: str
    ) -> bool:
        """Check if KPI data exists for a brand."""
        try:
            latest = await self.yougov_repo.get_latest_kpi(brand_label, kpi_name)
            return latest is not None
        except ValueError:
            return False


class NielsenBrandResolutionService:
    """Service for resolving YouGov brands to Nielsen data."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.brand_repo = BrandMapRepository(session)
        self.nielsen_repo = NielsenRepository(session)

    async def resolve_brand(
        self,
        yougov_brand_label: str,
        wirtschaftsgruppe: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Resolve a YouGov brand to Nielsen data.

        Args:
            yougov_brand_label: YouGov brand label
            wirtschaftsgruppe: Optional industry context for disambiguation

        Returns:
            Dict with Nielsen brand info or None if no mapping
        """
        # Get brand mappings that map to this YouGov label
        mappings = await self.brand_repo.get_by_yougov_label(yougov_brand_label)

        if not mappings:
            return None

        # Filter by wirtschaftsgruppe if provided
        if wirtschaftsgruppe:
            matching = [m for m in mappings if m.wirtschaftsgruppe == wirtschaftsgruppe]
            if matching:
                mappings = matching

        # Take the highest confidence mapping
        best_mapping = max(mappings, key=lambda m: m.confidence or 0)

        # Get Nielsen spend data
        spend_data = await self.nielsen_repo.get_by_brand(best_mapping.nielsen_brand)

        total_spend = Decimal("0")
        data_months = 0
        if spend_data:
            for record in spend_data:
                total_spend += record.spend_eur
            data_months = len(set((r.year, r.month) for r in spend_data))

        return {
            "nielsen_brand": best_mapping.nielsen_brand,
            "yougov_brand_label": yougov_brand_label,
            "wirtschaftsgruppe": best_mapping.wirtschaftsgruppe,
            "match_confidence": best_mapping.confidence,
            "has_nielsen_data": len(spend_data) > 0,
            "total_spend_eur": total_spend if spend_data else None,
            "data_months": data_months,
        }

    async def get_nielsen_brands_in_industry(
        self, wirtschaftsgruppe: str
    ) -> List[str]:
        """Get all Nielsen brands in an industry."""
        return await self.nielsen_repo.get_brands_in_industry(wirtschaftsgruppe)

    async def get_available_channels(self) -> List[str]:
        """Get all available advertising channels in Nielsen data."""
        return await self.nielsen_repo.get_channels()


class CompetitorSetAssemblyService:
    """Orchestrates the full Stage 1 competitor matching pipeline.

    Pipeline:
    1. User industry (Wirtschaftsgruppe) → sector_label lookup
    2. sector_label → YouGov brands in sector
    3. YouGov brands → Nielsen brand resolution via brand_map
    4. Assemble competitor set with data coverage info
    """

    def __init__(self, session: AsyncSession):
        self.session = session
        self.industry_service = IndustryLookupService(session)
        self.yougov_service = YouGovBrandQueryService(session)
        self.nielsen_service = NielsenBrandResolutionService(session)

    async def build_competitor_set(
        self,
        wirtschaftsgruppe: str,
        brand_kpi: str = "adaware",
        exclude_brands: Optional[List[str]] = None,
    ) -> CompetitorSetResult:
        """Build the complete competitor set for an industry.

        Args:
            wirtschaftsgruppe: User's industry classification
            brand_kpi: KPI metric to optimize (adaware, aided, consider)
            exclude_brands: Optional list of brands to exclude (e.g., the user's own brand)

        Returns:
            CompetitorSetResult with matched competitors and metadata
        """
        exclude_brands = exclude_brands or []
        warnings = []
        suggestions = []

        # Step 1: Industry → Sector lookup
        sector_label = await self.industry_service.lookup_sector(wirtschaftsgruppe)

        if not sector_label:
            # Try to find similar industries for suggestions
            similar = await self.industry_service.find_similar_industries(wirtschaftsgruppe)
            return CompetitorSetResult(
                industry=wirtschaftsgruppe,
                sector_label="",
                competitors=[],
                is_feasible=False,
                error_message=f"Industry '{wirtschaftsgruppe}' not found in mapping table",
                suggestions=similar if similar else ["Check the industry_map table for available industries"],
            )

        # Step 2: Get YouGov brands in sector
        yougov_brands = await self.yougov_service.get_brands_in_sector(
            sector_label, brand_kpi
        )

        if not yougov_brands:
            warnings.append(f"No brands with {brand_kpi} data found in sector '{sector_label}'")

        # Step 3: Resolve each YouGov brand to Nielsen
        competitors = []
        brands_without_nielsen = []

        for brand_info in yougov_brands:
            brand_label = brand_info["brand_label"]

            if brand_label in exclude_brands:
                continue

            nielsen_info = await self.nielsen_service.resolve_brand(
                brand_label, wirtschaftsgruppe
            )

            if nielsen_info:
                competitor = CompetitorBrandInfo(
                    nielsen_brand=nielsen_info["nielsen_brand"],
                    yougov_brand_label=brand_label,
                    wirtschaftsgruppe=nielsen_info["wirtschaftsgruppe"] or wirtschaftsgruppe,
                    has_nielsen_data=nielsen_info["has_nielsen_data"],
                    has_yougov_data=True,
                    total_spend_eur=nielsen_info["total_spend_eur"],
                    latest_kpi_value=brand_info.get("latest_value"),
                    match_confidence=nielsen_info["match_confidence"],
                    data_months=max(nielsen_info["data_months"], brand_info.get("data_months", 0)),
                )
                competitors.append(competitor)
            else:
                brands_without_nielsen.append(brand_label)

        # Step 4: Also include Nielsen-only brands (no YouGov match)
        nielsen_brands = await self.nielsen_service.get_nielsen_brands_in_industry(
            wirtschaftsgruppe
        )

        matched_nielsen = {c.nielsen_brand for c in competitors}
        for nielsen_brand in nielsen_brands:
            if nielsen_brand in matched_nielsen or nielsen_brand in exclude_brands:
                continue

            # Get spend info
            total_spend = await NielsenRepository(self.session).get_total_spend(
                nielsen_brand, datetime.now().year - 1  # Last year
            )

            if total_spend and total_spend > 0:
                competitor = CompetitorBrandInfo(
                    nielsen_brand=nielsen_brand,
                    yougov_brand_label=None,
                    wirtschaftsgruppe=wirtschaftsgruppe,
                    has_nielsen_data=True,
                    has_yougov_data=False,
                    total_spend_eur=total_spend,
                    match_confidence=None,
                )
                competitors.append(competitor)

        # Generate warnings
        if brands_without_nielsen:
            warnings.append(
                f"{len(brands_without_nielsen)} YouGov brands have no Nielsen mapping: "
                f"{', '.join(brands_without_nielsen[:3])}"
                + (f" and {len(brands_without_nielsen) - 3} more" if len(brands_without_nielsen) > 3 else "")
            )

        nielsen_only = [c for c in competitors if not c.has_yougov_data]
        if nielsen_only:
            warnings.append(
                f"{len(nielsen_only)} brands have Nielsen data but no YouGov {brand_kpi} data"
            )

        # Sort by total spend (descending)
        competitors.sort(
            key=lambda c: c.total_spend_eur or Decimal("0"),
            reverse=True
        )

        full_data_count = sum(
            1 for c in competitors if c.has_nielsen_data and c.has_yougov_data
        )

        return CompetitorSetResult(
            industry=wirtschaftsgruppe,
            sector_label=sector_label,
            competitors=competitors,
            total_competitors=len(competitors),
            competitors_with_full_data=full_data_count,
            warnings=warnings,
            suggestions=suggestions,
            is_feasible=len(competitors) > 0,
            error_message=None if competitors else "No competitors found with available data",
        )

    async def get_competitor_brands_for_run(
        self,
        wirtschaftsgruppe: str,
        brand_kpi: str,
        customer_name: Optional[str] = None,
    ) -> CompetitorSetResult:
        """Get competitor set for a generation run.

        Convenience method that excludes the customer's own brand.
        """
        exclude = [customer_name] if customer_name else []
        return await self.build_competitor_set(
            wirtschaftsgruppe=wirtschaftsgruppe,
            brand_kpi=brand_kpi,
            exclude_brands=exclude,
        )
