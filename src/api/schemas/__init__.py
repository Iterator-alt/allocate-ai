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
    StartRunRequest,
    StartRunResponse,
)
from src.api.schemas.competitors import (
    CompetitorBrand,
    BrandInfo,
    CompetitorSetResponse,
    ConfirmCompetitorsRequest,
    ConfirmCompetitorsResponse,
    ConfirmCompetitorsRequestV2,
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
from src.api.schemas.chat_agent import (
    ToolType,
    ChatMessageRequest,
    ChatMessageResponse,
    PendingChange,
    IntentEntity,
    IntentClassificationResult as IntentClassificationResultSchema,
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
    "StartRunRequest",
    "StartRunResponse",
    # Competitors
    "CompetitorBrand",
    "BrandInfo",
    "CompetitorSetResponse",
    "ConfirmCompetitorsRequest",
    "ConfirmCompetitorsResponse",
    "ConfirmCompetitorsRequestV2",
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
    # Chat Agent
    "ToolType",
    "ChatMessageRequest",
    "ChatMessageResponse",
    "PendingChange",
    "IntentEntity",
    "IntentClassificationResultSchema",
]
