"""Stage 1: Search Filter Service.

Complete pipeline for resolving user input to confirmed competitor set.
Search order: YouGov FIRST, Nielsen SECOND.
No static mapping tables - all resolution via AI at runtime.

Components:
- cache: 24hr TTL cache for DISTINCT value lists
- repository: Database queries for YouGov and Nielsen
- ai_resolution: AI-powered industry and brand resolution
- orchestrator: Main pipeline coordinator

Usage:
    from src.services.stage1 import Stage1Orchestrator, UserCampaignInput

    orchestrator = Stage1Orchestrator(session)
    result = await orchestrator.process(UserCampaignInput(
        brand_name="Nike",
        industry="Sportbekleidung",
        brand_kpi="adaware",
    ))
"""

from src.services.stage1.cache import Stage1Cache, stage1_cache
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
)
from src.services.stage1.orchestrator import (
    Stage1Orchestrator,
    Stage1Status,
    Stage1Result,
    UserCampaignInput,
    ConfirmedBrand,
    BrandDataPoints,
    CompetitorInfo,
)

__all__ = [
    # Cache
    "Stage1Cache",
    "stage1_cache",
    # Repository
    "Stage1Repository",
    # AI Resolution
    "AIResolutionService",
    "AIWithWebSearchService",
    "IndustryResolutionResult",
    "BrandResolutionResult",
    "WebEnrichmentResult",
    "ProxyScoringResult",
    "ProxyCandidate",
    "MatchType",
    # Orchestrator
    "Stage1Orchestrator",
    "Stage1Status",
    "Stage1Result",
    "UserCampaignInput",
    "ConfirmedBrand",
    "BrandDataPoints",
    "CompetitorInfo",
]
