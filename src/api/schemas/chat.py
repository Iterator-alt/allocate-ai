"""Schemas for chat history / feedback cards endpoints."""

from datetime import datetime
from typing import List, Optional, Dict, Any
from enum import Enum

from pydantic import BaseModel, Field

from src.api.schemas.common import BaseSchema, TimestampMixin


class MessageType(str, Enum):
    """Types of chat messages."""

    WARNING = "warning"
    ALERT = "alert"
    SUMMARY = "summary"
    RECOMMENDATION = "recommendation"
    INFO = "info"


class Severity(str, Enum):
    """Message severity levels."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class ChatMessage(BaseSchema, TimestampMixin):
    """A single feedback card / chat message."""

    id: int
    run_id: int
    message_type: MessageType
    severity: Severity
    title: str
    content: str
    metadata: Optional[Dict[str, Any]] = None
    display_order: int


class ChatHistoryResponse(BaseModel):
    """Response containing chat history for a run."""

    run_id: int
    messages: List[ChatMessage]
    total_messages: int
    has_warnings: bool = Field(..., description="Whether any warning-level messages exist")
    has_alerts: bool = Field(..., description="Whether any error-level messages exist")


class FeedbackCard(BaseModel):
    """Simplified feedback card for frontend display."""

    type: MessageType
    severity: Severity
    title: str
    content: str
    icon: Optional[str] = Field(None, description="Icon identifier for frontend")
    action_url: Optional[str] = Field(None, description="Optional action link")
