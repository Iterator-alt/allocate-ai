"""Stage 1: Competitor Matching Service.

IMPORTANT: Search order is YouGov FIRST, then Nielsen.

This module implements the competitor matching pipeline:
1. Industry Lookup: Wirtschaftsgruppe → sector_label
2. YouGov Brand Query (PRIMARY): Get brands in sector with KPI data
3. Nielsen Brand Resolution (SECONDARY): Map YouGov brands to Nielsen entries
4. Competitor Set Assembly: Combine data sources into final competitor set

Data Validation Rules:
- Minimum 12 data points required
- 2-3 years old: IDEAL quality
- 4-5 years old: ACCEPTABLE quality
- >5 years old: REJECTED (not used)
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import List, Optional, Dict, Any, Tuple
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from src.repositories import (
    NielsenRepository,
    YouGovRepository,
)

# Data validation constants
MIN_DATA_POINTS = 12          # Minimum required data points
MAX_DATA_AGE_YEARS = 5        # Data older than 5 years is rejected
IDEAL_DATA_AGE_YEARS = 3      # 2-3 years is ideal


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
    yougov_data_points: int = 0  # YouGov data point count
    nielsen_data_points: int = 0  # Nielsen data point count
    data_quality: str = "unknown"  # ideal, acceptable, insufficient
    oldest_data_year: Optional[int] = None
    newest_data_year: Optional[int] = None


@dataclass
class CompetitorSetResult:
    """Result of competitor matching process."""

    industry: str
    sector_label: str
    competitors: List[CompetitorBrandInfo]
    total_competitors: int = 0
    competitors_with_full_data: int = 0
    competitors_with_sufficient_data: int = 0  # Have >=12 data points
    warnings: List[str] = field(default_factory=list)
    suggestions: List[str] = field(default_factory=list)
    is_feasible: bool = True
    error_message: Optional[str] = None
    data_quality_summary: str = ""  # Overall data quality assessment


class IndustryLookupService:
    """Service for industry classification lookup.

    Maps Nielsen Wirtschaftsgruppe to YouGov sector_label.

    PRISMA-ONLY MODE: The industry_map table doesn't exist.
    Returns the wirtschaftsgruppe as-is for sector lookups.
    """

    def __init__(self, session: AsyncSession):
        self.session = session
        self.nielsen_repo = NielsenRepository(session)

    async def lookup_sector(self, wirtschaftsgruppe: str) -> Optional[str]:
        """Look up the YouGov sector label for a Wirtschaftsgruppe.

        PRISMA-ONLY MODE: Returns wirtschaftsgruppe as-is (no mapping table).
        """
        return wirtschaftsgruppe

    async def get_all_industries(self) -> List[str]:
        """Get all available Wirtschaftsgruppen.

        PRISMA-ONLY MODE: Gets from Nielsen table directly.
        """
        return await self.nielsen_repo.get_wirtschaftsgruppen()

    async def find_similar_industries(
        self, wirtschaftsgruppe: str, limit: int = 5
    ) -> List[str]:
        """Find similar industry names for suggestions.

        Uses simple substring matching for MVP.
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
    """Service for querying YouGov brand data.

    IMPORTANT: This is the PRIMARY data source. Search YouGov FIRST.
    """

    def __init__(self, session: AsyncSession):
        self.session = session
        self.yougov_repo = YouGovRepository(session)
        self._current_year = datetime.now().year

    async def get_brands_in_sector(
        self,
        sector: str,
        kpi_name: str = "adaware",
        min_data_points: int = MIN_DATA_POINTS,
    ) -> List[Dict[str, Any]]:
        """Get brands in a sector with sufficient KPI data.

        IMPORTANT: Only returns brands with at least 12 data points
        within the last 5 years.

        Args:
            sector: YouGov sector label
            kpi_name: KPI metric to check (adaware, aided, consider)
            min_data_points: Minimum data points required (default 12)

        Returns:
            List of brand info dicts with data availability and quality
        """
        # Use the new method that filters by data points
        brands_with_stats = await self.yougov_repo.get_brands_in_sector_with_stats(
            sector, min_data_points
        )

        brand_info = []
        for brand_stat in brands_with_stats:
            brand_label = brand_stat["brand_label"]

            # Get validated time series
            time_series, validation = await self.yougov_repo.get_kpi_time_series_validated(
                brand_label, kpi_name, min_data_points
            )

            if validation["is_valid"]:
                latest = await self.yougov_repo.get_latest_kpi(brand_label, kpi_name)
                brand_info.append({
                    "brand_label": brand_label,
                    "sector": sector,
                    "data_points": validation["data_points"],
                    "data_quality": validation["quality"],
                    "oldest_year": validation["oldest_year"],
                    "newest_year": validation["newest_year"],
                    "latest_value": latest["value"] if latest else None,
                    "latest_date": f"{latest['year']}-{latest['month']:02d}" if latest else None,
                    "warnings": validation["warnings"],
                })

        # Sort by data quality (ideal first) then by data points
        brand_info.sort(
            key=lambda x: (
                0 if x["data_quality"] == "ideal" else 1,
                -x["data_points"]
            )
        )

        return brand_info

    async def get_available_sectors(self) -> List[str]:
        """Get all available sectors in YouGov data."""
        return await self.yougov_repo.get_sectors()

    async def check_kpi_availability(
        self,
        brand_label: str,
        kpi_name: str,
        min_data_points: int = MIN_DATA_POINTS,
    ) -> Tuple[bool, Dict[str, Any]]:
        """Check if sufficient KPI data exists for a brand.

        Args:
            brand_label: Brand to check
            kpi_name: KPI metric name
            min_data_points: Minimum required points

        Returns:
            Tuple of (has_sufficient_data, validation_info)
        """
        try:
            _, validation = await self.yougov_repo.get_kpi_time_series_validated(
                brand_label, kpi_name, min_data_points
            )
            return validation["is_valid"], validation
        except ValueError:
            return False, {"is_valid": False, "error": "Invalid KPI name"}


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
                total_spend += Decimal(str(record.teuro or 0))
            data_months = len(set((r.jahr, r.monat) for r in spend_data))

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

    IMPORTANT: Search order is YouGov FIRST, then Nielsen.

    Pipeline:
    1. User industry (Wirtschaftsgruppe) → sector_label lookup
    2. YouGov brands in sector (PRIMARY - search first)
    3. Nielsen brand resolution (SECONDARY - search after YouGov)
    4. Assemble competitor set with data coverage info

    Data Requirements:
    - Minimum 12 data points required
    - Data must be within last 5 years
    - 2-3 years old is ideal, 4-5 years is acceptable
    """

    def __init__(self, session: AsyncSession):
        self.session = session
        self.industry_service = IndustryLookupService(session)
        self.yougov_service = YouGovBrandQueryService(session)
        self.nielsen_service = NielsenBrandResolutionService(session)
        self._current_year = datetime.now().year

    async def build_competitor_set(
        self,
        wirtschaftsgruppe: str,
        brand_kpi: str = "adaware",
        exclude_brands: Optional[List[str]] = None,
        min_data_points: int = MIN_DATA_POINTS,
    ) -> CompetitorSetResult:
        """Build the complete competitor set for an industry.

        SEARCH ORDER: YouGov FIRST, then Nielsen.

        Args:
            wirtschaftsgruppe: User's industry classification
            brand_kpi: KPI metric to optimize (adaware, aided, consider)
            exclude_brands: Optional list of brands to exclude
            min_data_points: Minimum data points required (default 12)

        Returns:
            CompetitorSetResult with matched competitors and metadata
        """
        exclude_brands = exclude_brands or []
        warnings = []
        suggestions = []

        # Step 1: Industry → Sector lookup
        sector_label = await self.industry_service.lookup_sector(wirtschaftsgruppe)

        if not sector_label:
            similar = await self.industry_service.find_similar_industries(wirtschaftsgruppe)
            return CompetitorSetResult(
                industry=wirtschaftsgruppe,
                sector_label="",
                competitors=[],
                is_feasible=False,
                error_message=f"Industry '{wirtschaftsgruppe}' not found in mapping table",
                suggestions=similar if similar else ["Check the industry_map table for available industries"],
            )

        # ============================================================
        # Step 2: YOUGOV FIRST - Get brands with sufficient data
        # ============================================================
        yougov_brands = await self.yougov_service.get_brands_in_sector(
            sector_label, brand_kpi, min_data_points
        )

        if not yougov_brands:
            warnings.append(
                f"No brands with sufficient {brand_kpi} data (min {min_data_points} points) "
                f"found in sector '{sector_label}'"
            )

        # ============================================================
        # Step 3: NIELSEN SECOND - Resolve YouGov brands to Nielsen
        # ============================================================
        competitors = []
        brands_without_nielsen = []

        for brand_info in yougov_brands:
            brand_label = brand_info["brand_label"]

            if brand_label in exclude_brands:
                continue

            # Resolve to Nielsen
            nielsen_info = await self.nielsen_service.resolve_brand(
                brand_label, wirtschaftsgruppe
            )

            # Check Nielsen data sufficiency
            nielsen_data_points = 0
            nielsen_has_sufficient = False
            if nielsen_info and nielsen_info["nielsen_brand"]:
                nielsen_repo = NielsenRepository(self.session)
                nielsen_has_sufficient = await nielsen_repo.has_sufficient_data(
                    nielsen_info["nielsen_brand"], min_data_points
                )
                if nielsen_has_sufficient:
                    nielsen_data_points = await nielsen_repo.count_valid_data_points(
                        nielsen_info["nielsen_brand"]
                    )

            if nielsen_info:
                # Determine overall data quality
                yougov_quality = brand_info.get("data_quality", "unknown")
                if yougov_quality == "ideal" and nielsen_has_sufficient:
                    overall_quality = "ideal"
                elif nielsen_has_sufficient:
                    overall_quality = "acceptable"
                else:
                    overall_quality = "yougov_only"

                competitor = CompetitorBrandInfo(
                    nielsen_brand=nielsen_info["nielsen_brand"],
                    yougov_brand_label=brand_label,
                    wirtschaftsgruppe=nielsen_info["wirtschaftsgruppe"] or wirtschaftsgruppe,
                    has_nielsen_data=nielsen_has_sufficient,
                    has_yougov_data=True,
                    total_spend_eur=nielsen_info["total_spend_eur"],
                    latest_kpi_value=brand_info.get("latest_value"),
                    match_confidence=nielsen_info["match_confidence"],
                    data_months=max(nielsen_info["data_months"], brand_info.get("data_points", 0)),
                    yougov_data_points=brand_info.get("data_points", 0),
                    nielsen_data_points=nielsen_data_points,
                    data_quality=overall_quality,
                    oldest_data_year=brand_info.get("oldest_year"),
                    newest_data_year=brand_info.get("newest_year"),
                )
                competitors.append(competitor)
            else:
                # YouGov brand without Nielsen mapping - still include
                competitor = CompetitorBrandInfo(
                    nielsen_brand=brand_label,  # Use YouGov label as placeholder
                    yougov_brand_label=brand_label,
                    wirtschaftsgruppe=wirtschaftsgruppe,
                    has_nielsen_data=False,
                    has_yougov_data=True,
                    latest_kpi_value=brand_info.get("latest_value"),
                    yougov_data_points=brand_info.get("data_points", 0),
                    data_quality="yougov_only",
                    oldest_data_year=brand_info.get("oldest_year"),
                    newest_data_year=brand_info.get("newest_year"),
                )
                competitors.append(competitor)
                brands_without_nielsen.append(brand_label)

        # ============================================================
        # Step 4: Check for Nielsen-only brands (no YouGov match)
        # Only include if they have sufficient data
        # ============================================================
        nielsen_repo = NielsenRepository(self.session)
        nielsen_brands_with_stats = await nielsen_repo.get_brands_in_industry_with_stats(
            wirtschaftsgruppe, min_data_points
        )

        matched_nielsen = {c.nielsen_brand for c in competitors}
        nielsen_only_count = 0

        for nielsen_stat in nielsen_brands_with_stats:
            nielsen_brand = nielsen_stat["brand_name"]
            if nielsen_brand in matched_nielsen or nielsen_brand in exclude_brands:
                continue

            competitor = CompetitorBrandInfo(
                nielsen_brand=nielsen_brand,
                yougov_brand_label=None,
                wirtschaftsgruppe=wirtschaftsgruppe,
                has_nielsen_data=True,
                has_yougov_data=False,
                total_spend_eur=nielsen_stat["total_spend_eur"],
                nielsen_data_points=nielsen_stat["data_points"],
                data_quality="nielsen_only",
                oldest_data_year=nielsen_stat["oldest_year"],
                newest_data_year=nielsen_stat["newest_year"],
            )
            competitors.append(competitor)
            nielsen_only_count += 1

        # ============================================================
        # Generate warnings and quality summary
        # ============================================================
        if brands_without_nielsen:
            warnings.append(
                f"{len(brands_without_nielsen)} YouGov brands have no Nielsen mapping: "
                f"{', '.join(brands_without_nielsen[:3])}"
                + (f" and {len(brands_without_nielsen) - 3} more" if len(brands_without_nielsen) > 3 else "")
            )

        if nielsen_only_count > 0:
            warnings.append(
                f"{nielsen_only_count} brands have Nielsen data but no YouGov {brand_kpi} data"
            )

        # Check for data age warnings
        aging_brands = [c for c in competitors if c.oldest_data_year and c.oldest_data_year < (self._current_year - IDEAL_DATA_AGE_YEARS)]
        if aging_brands:
            warnings.append(
                f"{len(aging_brands)} brands have data older than {IDEAL_DATA_AGE_YEARS} years (still usable)"
            )

        # Sort: prioritize brands with both data sources and ideal quality
        competitors.sort(
            key=lambda c: (
                0 if c.data_quality == "ideal" else (1 if c.data_quality == "acceptable" else 2),
                0 if (c.has_yougov_data and c.has_nielsen_data) else 1,
                -(c.total_spend_eur or Decimal("0")),
            )
        )

        # Calculate statistics
        full_data_count = sum(1 for c in competitors if c.has_nielsen_data and c.has_yougov_data)
        sufficient_data_count = sum(
            1 for c in competitors
            if c.yougov_data_points >= min_data_points or c.nielsen_data_points >= min_data_points
        )

        # Generate quality summary
        ideal_count = sum(1 for c in competitors if c.data_quality == "ideal")
        acceptable_count = sum(1 for c in competitors if c.data_quality == "acceptable")
        data_quality_summary = (
            f"{ideal_count} ideal quality, {acceptable_count} acceptable quality, "
            f"{len(competitors) - ideal_count - acceptable_count} partial data"
        )

        return CompetitorSetResult(
            industry=wirtschaftsgruppe,
            sector_label=sector_label,
            competitors=competitors,
            total_competitors=len(competitors),
            competitors_with_full_data=full_data_count,
            competitors_with_sufficient_data=sufficient_data_count,
            warnings=warnings,
            suggestions=suggestions,
            is_feasible=len(competitors) > 0,
            error_message=None if competitors else "No competitors found with sufficient data (min 12 points within 5 years)",
            data_quality_summary=data_quality_summary,
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
