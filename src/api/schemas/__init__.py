"""API schemas package."""

from src.api.schemas.common import (
    BaseSchema,
    TimestampMixin,
    ErrorResponse,
    SuccessResponse,
    PaginatedResponse,
)
from src.api.schemas.runs import (
    RunStatus,
    CreateRunRequest,
    RunResponse,
    RunStatusResponse,
    StopRunRequest,
    StopRunResponse,
    RunListResponse,
)
from src.api.schemas.competitors import (
    CompetitorBrand,
    CompetitorSetResponse,
    ConfirmCompetitorsRequest,
    ConfirmCompetitorsResponse,
)
from src.api.schemas.results import (
    ChannelAllocation,
    KPIProjection,
    AllocationResultResponse,
    AllocationSummary,
)
from src.api.schemas.chat import (
    MessageType,
    Severity,
    ChatMessage,
    ChatHistoryResponse,
    FeedbackCard,
)
from src.api.schemas.traces import (
    PromptTraceResponse,
    PromptTraceListResponse,
    PromptTraceSummary,
    UsageStats,
)

__all__ = [
    # Common
    "BaseSchema",
    "TimestampMixin",
    "ErrorResponse",
    "SuccessResponse",
    "PaginatedResponse",
    # Runs
    "RunStatus",
    "CreateRunRequest",
    "RunResponse",
    "RunStatusResponse",
    "StopRunRequest",
    "StopRunResponse",
    "RunListResponse",
    # Competitors
    "CompetitorBrand",
    "CompetitorSetResponse",
    "ConfirmCompetitorsRequest",
    "ConfirmCompetitorsResponse",
    # Results
    "ChannelAllocation",
    "KPIProjection",
    "AllocationResultResponse",
    "AllocationSummary",
    # Chat
    "MessageType",
    "Severity",
    "ChatMessage",
    "ChatHistoryResponse",
    "FeedbackCard",
    # Traces
    "PromptTraceResponse",
    "PromptTraceListResponse",
    "PromptTraceSummary",
    "UsageStats",
]
