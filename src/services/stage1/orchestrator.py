"""Stage 1 Orchestrator - Complete Search Filter Pipeline.

Implements the full Stage 1 flow from Stage1_SearchFilter_Design.md:

User Input → Industry Resolution → Brand Resolution → [Fallback if needed] →
Nielsen Data Fetch → Competitor Discovery → Stage 1.5 Output

Search Order: YouGov FIRST, Nielsen SECOND.
No static mapping tables - all resolution via AI at runtime.
"""

from dataclasses import dataclass, field
from datetime import date
from typing import List, Optional, Dict, Any, Tuple
from enum import Enum

from sqlalchemy.ext.asyncio import AsyncSession

from src.services.stage1.repository import Stage1Repository
from src.services.stage1.ai_resolution import (
    AIResolutionService,
    AIWithWebSearchService,
    IndustryResolutionResult,
    BrandResolutionResult,
    WebEnrichmentResult,
    ProxyScoringResult,
    ProxyCandidate,
    MatchType,
    CompetitorSuggestionResult,
    ProduktmarkeFilterResult,
)
from src.services.stage1.cache import stage1_cache
from src.services.stage1.debug_output import Stage1DebugLogger, is_debug_mode


class Stage1Status(Enum):
    """Status of Stage 1 processing."""
    PENDING = "pending"
    INDUSTRY_RESOLVED = "industry_resolved"
    BRAND_RESOLVED = "brand_resolved"
    FALLBACK_REQUIRED = "fallback_required"
    FALLBACK_COMPLETE = "fallback_complete"
    DATA_FETCHED = "data_fetched"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class UserCampaignInput:
    """User input for campaign configuration."""
    brand_name: str
    industry: str
    brand_kpi: str  # adaware, aware, consider
    media_channels: List[str] = field(default_factory=list)
    goal_direction: str = "budget_to_impact"  # or "goal_to_budget"
    budget_or_target: Optional[str] = None  # "500000 EUR" or "+5pp adaware"


@dataclass
class ConfirmedBrand:
    """A confirmed brand with resolved identifiers."""
    yougov_brand: str
    nielsen_brand: str
    match_type: MatchType
    confidence: float
    is_proxy: bool = False
    proxy_reasoning: Optional[str] = None


@dataclass
class BrandDataPoints:
    """12+ data points for a brand (from design doc).

    From YouGov (6):
    1. brand_label - confirmed canonical name
    2. sector_label - confirmed industry
    3. score for adaware - latest
    4. score for aware - latest
    5. score for consider - latest
    6. Date of latest data point

    From Nielsen (6):
    7. Total TEuro all channels (12-month rolling)
    8-12. TEuro per top 5 Mediengruppe channels
    """
    # YouGov data
    brand_label: str
    sector_label: str
    adaware_score: Optional[float] = None
    aware_score: Optional[float] = None
    consider_score: Optional[float] = None
    latest_date: Optional[date] = None

    # Nielsen data
    total_spend_teuro: float = 0.0
    channel_spend: Dict[str, float] = field(default_factory=dict)  # Top 5 channels


@dataclass
class CompetitorInfo:
    """Information about a competitor brand."""
    brand_label: str
    nielsen_brand: Optional[str] = None
    avg_kpi_score: float = 0.0
    total_spend_teuro: float = 0.0
    kpi_proximity: float = 0.0  # Distance from target brand


@dataclass
class ProduktmarkeFiltering:
    """Details of Produktmarke filtering for a brand."""
    all_produktmarke: List[str]
    relevant: List[str]
    excluded: List[str]
    reasoning: Dict[str, str]


@dataclass
class Stage1Result:
    """Complete output of Stage 1 to pass to Stage 1.5."""
    status: Stage1Status

    # Confirmed brand (or top 3 proxies if fallback)
    confirmed_brand: Optional[ConfirmedBrand] = None
    proxy_candidates: List[ProxyCandidate] = field(default_factory=list)

    # Resolved sectors
    yougov_sectors: List[str] = field(default_factory=list)
    nielsen_sectors: List[str] = field(default_factory=list)

    # Data points for confirmed brand
    brand_data: Optional[BrandDataPoints] = None

    # Competitor list (top 5-10)
    competitors: List[CompetitorInfo] = field(default_factory=list)

    # Full YouGov KPI data for Stage 2
    yougov_kpi_data: List[Dict[str, Any]] = field(default_factory=list)

    # Full Nielsen spend data for Stage 2
    nielsen_spend_data: List[Dict[str, Any]] = field(default_factory=list)

    # Competitor data for Stage 2
    competitor_data: List[Dict[str, Any]] = field(default_factory=list)

    # Produktmarke filtering details (NEW)
    produktmarke_filtering: Optional[ProduktmarkeFiltering] = None
    competitor_produktmarke_filtering: Dict[str, ProduktmarkeFiltering] = field(default_factory=dict)

    # Competitor suggestion details (NEW)
    competitor_suggestion_reasoning: Optional[str] = None

    # Metadata
    latency_ms: int = 0
    ai_calls_count: int = 0
    web_searches_count: int = 0
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


class Stage1Orchestrator:
    """Orchestrates the complete Stage 1 Search Filter pipeline.

    Flow:
    1. Industry Resolution (AI Call #1) - Always
    2. Brand Resolution (AI Call #2) - Always
    3. Web Enrichment (AI Call #3) - Fallback only
    4. Proxy Scoring (AI Call #4) - Fallback only
    5. Competitor Suggestion (AI Call #5) - Always (NEW)
    6. Produktmarke Filtering (AI Call #6) - Per brand (NEW)
    7. Nielsen Data Fetch (with Produktmarke filter)
    8. Output to Stage 1.5

    Latency Targets:
    - Happy path (6 brands): ~1.9s
    - Fallback path (6 brands): ~4.9s
    """

    def __init__(
        self,
        session: AsyncSession,
        web_search_provider: str = "duckduckgo",
        web_search_api_key: Optional[str] = None,
    ):
        self.session = session
        self.repo = Stage1Repository(session)
        self.ai_service = AIWithWebSearchService()

        # Configure web search
        self.ai_service.configure_web_search(web_search_provider, web_search_api_key)

    async def process(self, input: UserCampaignInput, run_id: Optional[str] = None) -> Stage1Result:
        """Execute the complete Stage 1 pipeline.

        Args:
            input: User's campaign configuration
            run_id: Optional run ID for debug logging

        Returns:
            Stage1Result with confirmed brand, competitors, and filtered data
        """
        import time
        start_time = time.time()

        result = Stage1Result(status=Stage1Status.PENDING)
        ai_calls = 0
        web_searches = 0

        # Initialize debug logger if run_id provided
        debug_logger = Stage1DebugLogger(run_id or "unknown") if run_id else None

        try:
            # ================================================================
            # Step 1: Industry Resolution (AI Call #1) - Always fires
            # ================================================================
            yougov_sectors = await self.repo.get_distinct_yougov_sectors()
            nielsen_sectors = await self.repo.get_distinct_nielsen_sectors()

            industry_result = await self.ai_service.resolve_industry(
                user_industry=input.industry,
                yougov_sectors=yougov_sectors,
                nielsen_sectors=nielsen_sectors,
                debug_logger=debug_logger,
            )
            ai_calls += 1

            if not industry_result.yougov_sectors and not industry_result.nielsen_sectors:
                result.status = Stage1Status.FAILED
                result.errors.append(f"Could not resolve industry: {input.industry}")
                result.latency_ms = int((time.time() - start_time) * 1000)
                result.ai_calls_count = ai_calls
                return result

            result.yougov_sectors = industry_result.yougov_sectors
            result.nielsen_sectors = industry_result.nielsen_sectors
            result.status = Stage1Status.INDUSTRY_RESOLVED

            # DEBUG: Log Y1 Industry Resolution
            if debug_logger:
                debug_logger.log_step_y1_industry_resolution(
                    user_industry=input.industry,
                    available_yougov_sectors=yougov_sectors,
                    available_nielsen_sectors=nielsen_sectors,
                    matched_yougov_sectors=industry_result.yougov_sectors,
                    matched_nielsen_sectors=industry_result.nielsen_sectors,
                    confidence=industry_result.confidence,
                    reasoning=industry_result.reasoning,
                )

            # ================================================================
            # Step 2: Brand Resolution (AI Call #2) - Always fires
            # ================================================================
            yougov_brands = await self.repo.get_distinct_yougov_brands(
                industry_result.yougov_sectors
            )
            nielsen_brands = await self.repo.get_distinct_nielsen_brands(
                industry_result.nielsen_sectors
            )

            brand_result = await self.ai_service.resolve_brand(
                user_brand=input.brand_name,
                yougov_brands=yougov_brands,
                nielsen_brands=nielsen_brands,
            )
            ai_calls += 1

            # ================================================================
            # Step 3 & 4: Fallback Path (if brand not found)
            # ================================================================
            # Only trigger fallback if NO brand was found at all
            # A similarity match (exact=False but yougov_brand present) is still valid
            if not brand_result.yougov_brand:
                result.status = Stage1Status.FALLBACK_REQUIRED

                # Step 3a: Web Enrichment (AI Call #3)
                web_profile = await self.ai_service.enrich_brand_from_web(
                    brand=input.brand_name,
                    industry=input.industry,
                )
                ai_calls += 1
                web_searches += 2  # Max 2 searches

                # Step 3b: Candidate Retrieval
                candidates = await self.repo.get_yougov_brand_kpi_scores(
                    sectors=industry_result.yougov_sectors,
                    metrics=[input.brand_kpi],
                )

                # Step 3c: Proxy Scoring (AI Call #4)
                proxy_result = await self.ai_service.score_proxy_candidates(
                    target_brand=input.brand_name,
                    web_profile=web_profile,
                    candidates=candidates,
                )
                ai_calls += 1

                result.proxy_candidates = proxy_result.candidates
                result.status = Stage1Status.FALLBACK_COMPLETE

                # Use top proxy candidate if available
                if proxy_result.candidates:
                    top_proxy = proxy_result.candidates[0]
                    result.confirmed_brand = ConfirmedBrand(
                        yougov_brand=top_proxy.brand_label,
                        nielsen_brand=top_proxy.brand_label,  # Will resolve below
                        match_type=MatchType.SIMILARITY,
                        confidence=top_proxy.score,
                        is_proxy=True,
                        proxy_reasoning=top_proxy.reasoning,
                    )
                    result.warnings.append(
                        f"Brand '{input.brand_name}' not found. Using proxy: {top_proxy.brand_label}"
                    )
                else:
                    result.status = Stage1Status.FAILED
                    result.errors.append(
                        f"Brand '{input.brand_name}' not found and no suitable proxy found"
                    )
                    result.latency_ms = int((time.time() - start_time) * 1000)
                    result.ai_calls_count = ai_calls
                    result.web_searches_count = web_searches
                    return result
            else:
                result.confirmed_brand = ConfirmedBrand(
                    yougov_brand=brand_result.yougov_brand,
                    nielsen_brand=brand_result.nielsen_brand or brand_result.yougov_brand,
                    match_type=brand_result.match_type,
                    confidence=brand_result.confidence,
                )
                result.status = Stage1Status.BRAND_RESOLVED

            # ================================================================
            # Step 4: Produktmarke Filtering for Customer Brand (AI Call #6)
            # ================================================================
            confirmed_yougov = result.confirmed_brand.yougov_brand
            confirmed_nielsen = result.confirmed_brand.nielsen_brand

            # Get all Produktmarke for the customer's Nielsen brand
            all_produktmarke = await self.repo.get_distinct_produktmarke(confirmed_nielsen)

            # Filter Produktmarke using AI if there are multiple options
            relevant_produktmarke = None
            if all_produktmarke and len(all_produktmarke) > 1:
                produktmarke_result = await self.ai_service.filter_produktmarke(
                    customer_brand=input.brand_name,
                    nielsen_marke=confirmed_nielsen,
                    produktmarke_list=all_produktmarke,
                    debug_logger=debug_logger,
                )
                ai_calls += 1

                relevant_produktmarke = produktmarke_result.relevant if produktmarke_result.relevant else all_produktmarke

                # Store filtering details for transparency
                result.produktmarke_filtering = ProduktmarkeFiltering(
                    all_produktmarke=all_produktmarke,
                    relevant=produktmarke_result.relevant,
                    excluded=produktmarke_result.excluded,
                    reasoning=produktmarke_result.reasoning,
                )
            elif all_produktmarke:
                # Only one Produktmarke, no filtering needed
                relevant_produktmarke = all_produktmarke

            # ================================================================
            # Step 5: Nielsen Data Fetch (with Produktmarke filter)
            # ================================================================
            # Get YouGov data
            yougov_data = await self.repo.get_yougov_brand_data(confirmed_yougov)
            latest_scores = await self.repo.get_yougov_latest_scores(confirmed_yougov)

            # Get Nielsen data WITH Produktmarke filter
            nielsen_spend = await self.repo.get_nielsen_brand_spend(
                marke=confirmed_nielsen,
                produktmarke_filter=relevant_produktmarke,
            )
            total_spend = await self.repo.get_nielsen_brand_total_spend(
                marke=confirmed_nielsen,
                produktmarke_filter=relevant_produktmarke,
            )
            channel_spend = await self.repo.get_nielsen_spend_by_channel(
                marke=confirmed_nielsen,
                produktmarke_filter=relevant_produktmarke,
            )

            # Build 12+ data points
            result.brand_data = BrandDataPoints(
                brand_label=confirmed_yougov,
                sector_label=result.yougov_sectors[0] if result.yougov_sectors else "",
                adaware_score=latest_scores.get("adaware", {}).get("score"),
                aware_score=latest_scores.get("aware", {}).get("score"),
                consider_score=latest_scores.get("consider", {}).get("score"),
                latest_date=latest_scores.get("adaware", {}).get("date"),
                total_spend_teuro=total_spend,
                channel_spend={c["mediengruppe"]: c["total_spend"] for c in channel_spend},
            )

            result.yougov_kpi_data = yougov_data
            result.nielsen_spend_data = nielsen_spend
            result.status = Stage1Status.DATA_FETCHED

            # NOTE: Y3 debug logging moved to AFTER competitor data is fetched

            # ================================================================
            # Step 6: Competitor Suggestion (AI Call #5) - NEW
            # ================================================================
            target_score = latest_scores.get(input.brand_kpi, {}).get("score", 50.0)

            # Get all brands in sector (excluding customer)
            all_sector_brands = await self.repo.get_distinct_yougov_brands_excluding(
                sectors=result.yougov_sectors,
                exclude_brand=confirmed_yougov,
            )

            # AI Call #5: Semantic competitor selection
            # Pass Nielsen brand to help exclude same-parent brands
            competitor_suggestion = await self.ai_service.suggest_competitors(
                customer_brand=input.brand_name,
                customer_industry=input.industry,
                available_brands=all_sector_brands,
                brand_kpi=input.brand_kpi,
                max_competitors=10,
                customer_nielsen_brand=confirmed_nielsen,
                debug_logger=debug_logger,
            )
            ai_calls += 1

            result.competitor_suggestion_reasoning = competitor_suggestion.reasoning

            # DEBUG: Log Y2 Brand and Competitors
            if debug_logger:
                debug_logger.log_step_y2_brand_and_competitors(
                    user_brand=input.brand_name,
                    yougov_sectors=result.yougov_sectors,
                    available_brands=all_sector_brands + [confirmed_yougov],  # Include customer brand
                    matched_customer_brand=confirmed_yougov,
                    brand_match_confidence=result.confirmed_brand.confidence if result.confirmed_brand else 0.0,
                    brand_match_reasoning=result.confirmed_brand.proxy_reasoning if result.confirmed_brand and result.confirmed_brand.is_proxy else None,
                    suggested_competitors=competitor_suggestion.competitors,
                    competitor_suggestion_reasoning=competitor_suggestion.reasoning,
                )

            # Use AI-suggested competitors (validated against DB)
            suggested_competitors = competitor_suggestion.competitors

            # If AI returned no competitors, fall back to KPI proximity algorithm
            if not suggested_competitors:
                result.warnings.append(
                    "AI competitor suggestion returned empty. Falling back to KPI proximity algorithm."
                )
                yougov_competitors = await self.repo.get_yougov_competitors(
                    sectors=result.yougov_sectors,
                    exclude_brand=confirmed_yougov,
                    primary_kpi=input.brand_kpi,
                    target_score=target_score or 50.0,
                    limit=10,
                )
                suggested_competitors = [c["brand_label"] for c in yougov_competitors]

            # ================================================================
            # Step 7: Fetch Competitor Data with Produktmarke Filtering
            # ================================================================
            # Get Nielsen brand mapping for competitors
            nielsen_competitors = await self.repo.get_nielsen_competitors(
                wirtschaftsgruppen=result.nielsen_sectors,
                exclude_brand=confirmed_nielsen,
                limit=50,  # Get more to increase match probability
            )

            # Build multiple lookup strategies for better matching
            # Strategy 1: Exact lowercase match
            nielsen_brand_lookup = {n["marke"].lower(): n["marke"] for n in nielsen_competitors}
            # Strategy 2: First word match (e.g., "Zott Sahne Joghurt" -> "ZOTT")
            # Strategy 3: Contains match (e.g., "Dany Sahne (Danone)" contains "DANONE")

            def strip_umlauts(text: str) -> str:
                """Convert German umlauts to ASCII equivalents.

                Handles both single-char (ü→u) and German convention (ü→ue) forms.
                Returns the single-char version for consistency.
                """
                # First normalize ue/oe/ae back to umlauts, then strip
                text = text.replace('ue', 'ü').replace('oe', 'ö').replace('ae', 'ä')
                replacements = {
                    'ü': 'u', 'Ü': 'U',
                    'ö': 'o', 'Ö': 'O',
                    'ä': 'a', 'Ä': 'A',
                    'ß': 'ss',
                }
                for umlaut, replacement in replacements.items():
                    text = text.replace(umlaut, replacement)
                return text

            def get_first_word(text: str) -> str:
                """Extract first word, handling various separators."""
                import re
                # Split on space, slash, parenthesis, hyphen
                parts = re.split(r'[\s/\(\)\-]+', text)
                return parts[0] if parts else ""

            def find_nielsen_brand(yougov_brand: str) -> Optional[str]:
                """Find matching Nielsen brand using multiple strategies."""
                yg_lower = yougov_brand.lower()
                yg_ascii = strip_umlauts(yg_lower)

                # Strategy 1: Exact match
                if yg_lower in nielsen_brand_lookup:
                    return nielsen_brand_lookup[yg_lower]

                # Strategy 2: First word match (handling / and other separators)
                first_word = get_first_word(yg_lower)
                first_word_ascii = strip_umlauts(first_word)
                if first_word in nielsen_brand_lookup:
                    return nielsen_brand_lookup[first_word]

                # Strategy 3: Check if any Nielsen brand is contained in YouGov name
                # e.g., "Dany Sahne (Danone)" contains "danone"
                for nielsen_lower, nielsen_original in nielsen_brand_lookup.items():
                    if nielsen_lower in yg_lower:
                        return nielsen_original

                # Strategy 4: Check if YouGov first word matches Nielsen brand start
                for nielsen_lower, nielsen_original in nielsen_brand_lookup.items():
                    if nielsen_lower.startswith(first_word) or first_word.startswith(nielsen_lower):
                        return nielsen_original

                # Strategy 5: Umlaut-normalized matching
                # e.g., "Müller" -> "muller" matches "MUELLER" -> "muller" (after ue->ü->u)
                for nielsen_lower, nielsen_original in nielsen_brand_lookup.items():
                    nielsen_ascii = strip_umlauts(nielsen_lower)
                    # Check first word with umlauts normalized
                    if nielsen_ascii == first_word_ascii:
                        return nielsen_original
                    # Check if normalized versions match
                    if nielsen_ascii in yg_ascii or yg_ascii.startswith(nielsen_ascii):
                        return nielsen_original
                    # Also check if first word starts with nielsen brand
                    if first_word_ascii.startswith(nielsen_ascii):
                        return nielsen_original

                return None

            # Build competitor list with KPI data
            competitors = []
            for brand in suggested_competitors:
                # Get KPI scores for this competitor
                comp_kpi = await self.repo.get_yougov_latest_scores(brand)
                avg_score = comp_kpi.get(input.brand_kpi, {}).get("score", 0.0) or 0.0

                # Find matching Nielsen brand using smart matching
                nielsen_brand = find_nielsen_brand(brand)

                competitors.append(CompetitorInfo(
                    brand_label=brand,
                    nielsen_brand=nielsen_brand,
                    avg_kpi_score=avg_score,
                    total_spend_teuro=0.0,  # Will be filled below with Produktmarke filter
                    kpi_proximity=abs(avg_score - (target_score or 50.0)),
                ))

            result.competitors = competitors[:10]

            # Fetch detailed data for top 5 competitors with Produktmarke filtering
            for comp in result.competitors[:5]:
                comp_yougov = await self.repo.get_yougov_brand_data(comp.brand_label)
                comp_nielsen = []
                comp_relevant_pm = None

                if comp.nielsen_brand:
                    # Get Produktmarke for competitor
                    comp_all_pm = await self.repo.get_distinct_produktmarke(comp.nielsen_brand)

                    # Filter Produktmarke for competitor (AI Call #6)
                    if comp_all_pm and len(comp_all_pm) > 1:
                        comp_pm_result = await self.ai_service.filter_produktmarke(
                            customer_brand=comp.brand_label,  # Use competitor name as context
                            nielsen_marke=comp.nielsen_brand,
                            produktmarke_list=comp_all_pm,
                            debug_logger=debug_logger,
                        )
                        ai_calls += 1
                        comp_relevant_pm = comp_pm_result.relevant if comp_pm_result.relevant else comp_all_pm

                        # Store filtering details
                        result.competitor_produktmarke_filtering[comp.brand_label] = ProduktmarkeFiltering(
                            all_produktmarke=comp_all_pm,
                            relevant=comp_pm_result.relevant,
                            excluded=comp_pm_result.excluded,
                            reasoning=comp_pm_result.reasoning,
                        )
                    elif comp_all_pm:
                        comp_relevant_pm = comp_all_pm

                    # Get Nielsen spend with Produktmarke filter
                    comp_nielsen = await self.repo.get_nielsen_brand_spend(
                        marke=comp.nielsen_brand,
                        produktmarke_filter=comp_relevant_pm,
                    )

                    # Update total spend in CompetitorInfo
                    comp_total_spend = await self.repo.get_nielsen_brand_total_spend(
                        marke=comp.nielsen_brand,
                        produktmarke_filter=comp_relevant_pm,
                    )
                    comp.total_spend_teuro = comp_total_spend

                result.competitor_data.append({
                    "brand": comp.brand_label,
                    "yougov": comp_yougov,
                    "nielsen": comp_nielsen,
                    "produktmarke_filter": comp_relevant_pm,
                })

            # DEBUG: Log Y3 YouGov Filtered Data (NOW includes competitor data)
            if debug_logger:
                # Combine customer + competitor YouGov data
                all_yougov_data = list(result.yougov_kpi_data)  # Customer data
                for comp_data in result.competitor_data:
                    all_yougov_data.extend(comp_data.get("yougov", []))

                debug_logger.log_step_y3_yougov_filtered_data(
                    customer_brand=confirmed_yougov,
                    competitors=[c.brand_label for c in result.competitors[:5]],
                    selected_kpi=input.brand_kpi,
                    yougov_data=all_yougov_data,
                )

            # DEBUG: Log N1 Nielsen Produktmarke Filtering
            if debug_logger:
                # Build the brand_produktmarke_details list for ALL brands
                brand_produktmarke_details = []

                # Customer brand
                if result.produktmarke_filtering:
                    brand_produktmarke_details.append({
                        "brand_label": confirmed_yougov,
                        "nielsen_marke": confirmed_nielsen,
                        "nielsen_match_found": True,
                        "produktmarke_filtering_applied": True,
                        "all_produktmarke": result.produktmarke_filtering.all_produktmarke,
                        "relevant_produktmarke": result.produktmarke_filtering.relevant,
                        "excluded_produktmarke": result.produktmarke_filtering.excluded,
                        "reasoning": result.produktmarke_filtering.reasoning,
                    })
                else:
                    brand_produktmarke_details.append({
                        "brand_label": confirmed_yougov,
                        "nielsen_marke": confirmed_nielsen,
                        "nielsen_match_found": True,
                        "produktmarke_filtering_applied": False,
                        "note": "Only 1 Produktmarke or none found - no filtering needed",
                    })

                # ALL competitors (including those without Nielsen match)
                for comp in result.competitors[:5]:  # Top 5 that we process
                    if comp.brand_label in result.competitor_produktmarke_filtering:
                        comp_filtering = result.competitor_produktmarke_filtering[comp.brand_label]
                        brand_produktmarke_details.append({
                            "brand_label": comp.brand_label,
                            "nielsen_marke": comp.nielsen_brand,
                            "nielsen_match_found": True,
                            "produktmarke_filtering_applied": True,
                            "all_produktmarke": comp_filtering.all_produktmarke,
                            "relevant_produktmarke": comp_filtering.relevant,
                            "excluded_produktmarke": comp_filtering.excluded,
                            "reasoning": comp_filtering.reasoning,
                        })
                    elif comp.nielsen_brand:
                        # Nielsen brand found but no filtering (only 1 Produktmarke)
                        brand_produktmarke_details.append({
                            "brand_label": comp.brand_label,
                            "nielsen_marke": comp.nielsen_brand,
                            "nielsen_match_found": True,
                            "produktmarke_filtering_applied": False,
                            "note": "Only 1 Produktmarke or none found - no filtering needed",
                        })
                    else:
                        # No Nielsen brand match found
                        brand_produktmarke_details.append({
                            "brand_label": comp.brand_label,
                            "nielsen_marke": None,
                            "nielsen_match_found": False,
                            "produktmarke_filtering_applied": False,
                            "note": "No matching Nielsen Marke found in database",
                        })

                debug_logger.log_step_n1_nielsen_produktmarke_filtering(
                    brand_produktmarke_details=brand_produktmarke_details,
                )

            result.status = Stage1Status.COMPLETED

            # DEBUG: Log Final Filtered Data for Stage 2
            if debug_logger:
                # Assemble complete YouGov slice (customer + all competitors)
                complete_yougov_slice = list(result.yougov_kpi_data)  # Customer data
                for comp_data in result.competitor_data:
                    complete_yougov_slice.extend(comp_data.get("yougov", []))

                # Assemble complete Nielsen slice (customer + all competitors)
                complete_nielsen_slice = list(result.nielsen_spend_data)  # Customer data
                for comp_data in result.competitor_data:
                    complete_nielsen_slice.extend(comp_data.get("nielsen", []))

                debug_logger.log_final_filtered_data(
                    customer_brand=confirmed_yougov,
                    competitors=[c.brand_label for c in result.competitors],
                    yougov_slice=complete_yougov_slice,
                    nielsen_slice=complete_nielsen_slice,
                    competitor_data=result.competitor_data,
                )

        except Exception as e:
            result.status = Stage1Status.FAILED
            result.errors.append(str(e))

            # DEBUG: Log error
            if debug_logger:
                debug_logger.log_error(
                    step="PIPELINE",
                    error=str(e),
                    context={
                        "input_brand": input.brand_name,
                        "input_industry": input.industry,
                        "status_before_error": result.status.value,
                    },
                )

        # Final timing
        result.latency_ms = int((time.time() - start_time) * 1000)
        result.ai_calls_count = ai_calls
        result.web_searches_count = web_searches

        return result

    async def clear_cache(self) -> None:
        """Clear the Stage 1 cache."""
        await stage1_cache.clear()
