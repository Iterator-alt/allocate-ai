"""Mediamix engine - 4-stage pipeline."""

from src.services.mediamix.competitor_matching import (
    IndustryLookupService,
    YouGovBrandQueryService,
    NielsenBrandResolutionService,
    CompetitorSetAssemblyService,
    CompetitorBrandInfo,
    CompetitorSetResult,
)
from src.services.mediamix.data_filtering import (
    DataFilteringService,
    DataFilteringResult,
    CompetitorSpendProfile,
    CompetitorKPIProfile,
    IndustryBenchmark,
)
from src.services.mediamix.prompt_assembly import (
    PromptAssemblyService,
    PromptAssemblyInput,
    AssembledPrompt,
)
from src.services.mediamix.output_parsing import (
    OutputParsingService,
    ParsedAllocationResult,
    ChannelAllocation,
    ValidationIssue,
)
from src.services.mediamix.feedback_generation import (
    FeedbackGenerationService,
    FeedbackGenerationResult,
    FeedbackMessage,
)

__all__ = [
    # Stage 1: Competitor Matching
    "IndustryLookupService",
    "YouGovBrandQueryService",
    "NielsenBrandResolutionService",
    "CompetitorSetAssemblyService",
    "CompetitorBrandInfo",
    "CompetitorSetResult",
    # Stage 2: Data Filtering
    "DataFilteringService",
    "DataFilteringResult",
    "CompetitorSpendProfile",
    "CompetitorKPIProfile",
    "IndustryBenchmark",
    # Stage 2: Prompt Assembly
    "PromptAssemblyService",
    "PromptAssemblyInput",
    "AssembledPrompt",
    # Stage 3: Output Parsing
    "OutputParsingService",
    "ParsedAllocationResult",
    "ChannelAllocation",
    "ValidationIssue",
    # Stage 4: Feedback Generation
    "FeedbackGenerationService",
    "FeedbackGenerationResult",
    "FeedbackMessage",
]
