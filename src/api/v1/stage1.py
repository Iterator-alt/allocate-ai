"""Stage 1 Search Filter API endpoints.

Endpoints:
- POST /stage1/process - Execute Stage 1 pipeline to resolve brand and competitors
- GET /stage1/cache/clear - Clear Stage 1 caches

Stage 1 resolves user input to confirmed competitor set:
1. Industry Resolution (AI Call #1) - Always
2. Brand Resolution (AI Call #2) - Always
3. Web Enrichment (AI Call #3) - Fallback only
4. Proxy Scoring (AI Call #4) - Fallback only
5. Nielsen Data Fetch
6. Competitor Discovery
"""

import logging
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.dependencies import get_db
from src.api.schemas import ErrorResponse
from src.api.middleware import get_session_context, SessionContext
from src.services.stage1 import (
    Stage1Orchestrator,
    Stage1Status,
    Stage1Result,
    UserCampaignInput,
    ConfirmedBrand,
    BrandDataPoints,
    CompetitorInfo,
    MatchType,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/stage1", tags=["stage1"])


# ============================================================================
# Request/Response Schemas
# ============================================================================

class Stage1ProcessRequest(BaseModel):
    """Request to process Stage 1 pipeline."""

    brand_name: str = Field(..., description="User's brand name (e.g., 'Nike')")
    industry: str = Field(..., description="Industry in any language (e.g., 'Sportbekleidung')")
    brand_kpi: str = Field(
        ...,
        description="Primary KPI to optimize",
        pattern="^(adaware|aware|consider)$",
    )
    media_channels: List[str] = Field(
        default_factory=list,
        description="Selected media channels (optional)",
    )
    goal_direction: str = Field(
        default="budget_to_impact",
        description="Goal direction: 'budget_to_impact' or 'goal_to_budget'",
    )
    budget_or_target: Optional[str] = Field(
        default=None,
        description="Budget (e.g., '500000 EUR') or target (e.g., '+5pp adaware')",
    )


class ConfirmedBrandResponse(BaseModel):
    """Response for confirmed brand information."""

    yougov_brand: str
    nielsen_brand: str
    match_type: str
    confidence: float
    is_proxy: bool = False
    proxy_reasoning: Optional[str] = None


class BrandDataPointsResponse(BaseModel):
    """Response for brand data points (12+ required)."""

    # YouGov data
    brand_label: str
    sector_label: str
    adaware_score: Optional[float] = None
    aware_score: Optional[float] = None
    consider_score: Optional[float] = None
    latest_date: Optional[str] = None

    # Nielsen data
    total_spend_teuro: float = 0.0
    channel_spend: dict = Field(default_factory=dict)


class CompetitorInfoResponse(BaseModel):
    """Response for competitor information."""

    brand_label: str
    nielsen_brand: Optional[str] = None
    avg_kpi_score: float = 0.0
    total_spend_teuro: float = 0.0
    kpi_proximity: float = 0.0


class ProxyCandidateResponse(BaseModel):
    """Response for proxy candidate (fallback path)."""

    brand_label: str
    score: float
    reasoning: str


class Stage1ProcessResponse(BaseModel):
    """Response from Stage 1 processing."""

    status: str
    latency_ms: int
    ai_calls_count: int
    web_searches_count: int

    # Confirmed brand (or top proxy if fallback)
    confirmed_brand: Optional[ConfirmedBrandResponse] = None
    proxy_candidates: List[ProxyCandidateResponse] = Field(default_factory=list)

    # Resolved sectors
    yougov_sectors: List[str] = Field(default_factory=list)
    nielsen_sectors: List[str] = Field(default_factory=list)

    # Data points for confirmed brand
    brand_data: Optional[BrandDataPointsResponse] = None

    # Competitor list (top 5-10)
    competitors: List[CompetitorInfoResponse] = Field(default_factory=list)

    # Warnings and errors
    warnings: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)


# ============================================================================
# Endpoints
# ============================================================================

@router.post(
    "/process",
    response_model=Stage1ProcessResponse,
    responses={
        200: {"description": "Stage 1 processing completed"},
        400: {"model": ErrorResponse, "description": "Invalid request"},
        401: {"model": ErrorResponse, "description": "Unauthorized"},
        500: {"model": ErrorResponse, "description": "Processing failed"},
    },
)
async def process_stage1(
    request: Request,
    process_request: Stage1ProcessRequest,
    db: AsyncSession = Depends(get_db),
) -> Stage1ProcessResponse:
    """Execute Stage 1 Search Filter pipeline.

    This endpoint resolves user input to a confirmed competitor set:

    1. **Industry Resolution** (AI Call #1) - Matches user's industry to
       YouGov sector_label and Nielsen Wirtschaftsgruppe

    2. **Brand Resolution** (AI Call #2) - Matches user's brand to
       YouGov brand_label and Nielsen Marke

    3. **Web Enrichment** (AI Call #3, fallback only) - If brand not found,
       searches web for company profile and market positioning

    4. **Proxy Scoring** (AI Call #4, fallback only) - Scores proxy candidates
       based on web profile similarity

    5. **Nielsen Data Fetch** - Gets spend data for confirmed brand

    6. **Competitor Discovery** - Finds top 5-10 competitors by KPI proximity

    **Latency Targets:**
    - Happy path (brand found): ~500-800ms
    - Fallback path (brand missing): ~3-5s

    **Search Order:** YouGov FIRST, then Nielsen
    """
    session: SessionContext = await get_session_context(request)

    try:
        # Create orchestrator
        orchestrator = Stage1Orchestrator(
            session=db,
            web_search_provider="duckduckgo",  # Default, can be configured
        )

        # Build input
        user_input = UserCampaignInput(
            brand_name=process_request.brand_name,
            industry=process_request.industry,
            brand_kpi=process_request.brand_kpi,
            media_channels=process_request.media_channels,
            goal_direction=process_request.goal_direction,
            budget_or_target=process_request.budget_or_target,
        )

        # Execute pipeline
        logger.info(
            f"Starting Stage 1 processing for brand={process_request.brand_name}, "
            f"industry={process_request.industry}, kpi={process_request.brand_kpi}"
        )

        result: Stage1Result = await orchestrator.process(user_input)

        logger.info(
            f"Stage 1 completed: status={result.status.value}, "
            f"latency_ms={result.latency_ms}, ai_calls={result.ai_calls_count}"
        )

        # Build response
        confirmed_brand_response = None
        if result.confirmed_brand:
            confirmed_brand_response = ConfirmedBrandResponse(
                yougov_brand=result.confirmed_brand.yougov_brand,
                nielsen_brand=result.confirmed_brand.nielsen_brand,
                match_type=result.confirmed_brand.match_type.value,
                confidence=result.confirmed_brand.confidence,
                is_proxy=result.confirmed_brand.is_proxy,
                proxy_reasoning=result.confirmed_brand.proxy_reasoning,
            )

        brand_data_response = None
        if result.brand_data:
            brand_data_response = BrandDataPointsResponse(
                brand_label=result.brand_data.brand_label,
                sector_label=result.brand_data.sector_label,
                adaware_score=result.brand_data.adaware_score,
                aware_score=result.brand_data.aware_score,
                consider_score=result.brand_data.consider_score,
                latest_date=str(result.brand_data.latest_date) if result.brand_data.latest_date else None,
                total_spend_teuro=result.brand_data.total_spend_teuro,
                channel_spend=result.brand_data.channel_spend,
            )

        proxy_candidates_response = [
            ProxyCandidateResponse(
                brand_label=p.brand_label,
                score=p.score,
                reasoning=p.reasoning,
            )
            for p in result.proxy_candidates
        ]

        competitors_response = [
            CompetitorInfoResponse(
                brand_label=c.brand_label,
                nielsen_brand=c.nielsen_brand,
                avg_kpi_score=c.avg_kpi_score,
                total_spend_teuro=c.total_spend_teuro,
                kpi_proximity=c.kpi_proximity,
            )
            for c in result.competitors
        ]

        return Stage1ProcessResponse(
            status=result.status.value,
            latency_ms=result.latency_ms,
            ai_calls_count=result.ai_calls_count,
            web_searches_count=result.web_searches_count,
            confirmed_brand=confirmed_brand_response,
            proxy_candidates=proxy_candidates_response,
            yougov_sectors=result.yougov_sectors,
            nielsen_sectors=result.nielsen_sectors,
            brand_data=brand_data_response,
            competitors=competitors_response,
            warnings=result.warnings,
            errors=result.errors,
        )

    except Exception as e:
        logger.error(f"Stage 1 processing failed: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Stage 1 processing failed: {str(e)}",
        )


@router.post(
    "/cache/clear",
    responses={
        200: {"description": "Cache cleared successfully"},
        401: {"model": ErrorResponse, "description": "Unauthorized"},
    },
)
async def clear_stage1_cache(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Clear Stage 1 caches.

    Clears all cached DISTINCT value lists:
    - YouGov sectors
    - Nielsen sectors
    - YouGov brands per sector
    - Nielsen brands per Wirtschaftsgruppe

    Cache has 24hr TTL by default. Use this endpoint to force refresh
    after data ingestion or updates.
    """
    session: SessionContext = await get_session_context(request)

    orchestrator = Stage1Orchestrator(session=db)
    await orchestrator.clear_cache()

    return {"message": "Stage 1 cache cleared successfully"}


@router.get(
    "/health",
    responses={
        200: {"description": "Stage 1 service healthy"},
    },
)
async def stage1_health():
    """Check Stage 1 service health.

    Returns service status and configuration.
    """
    return {
        "status": "healthy",
        "service": "stage1",
        "description": "Search Filter Service",
        "search_order": ["YouGov (primary)", "Nielsen (secondary)"],
        "ai_calls": {
            "industry_resolution": "AI Call #1 (always)",
            "brand_resolution": "AI Call #2 (always)",
            "web_enrichment": "AI Call #3 (fallback only)",
            "proxy_scoring": "AI Call #4 (fallback only)",
        },
        "cache_ttl_hours": 24,
    }
