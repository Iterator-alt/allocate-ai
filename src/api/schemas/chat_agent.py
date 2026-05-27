"""Schemas for chat agent endpoints."""

from enum import Enum
from typing import Optional, List, Dict, Any

from pydantic import BaseModel, Field


class ToolType(str, Enum):
    """Types of tools used by the chat agent."""

    COMPETITOR_ADD = "competitor_add"
    COMPETITOR_REMOVE = "competitor_remove"
    EDITING = "editing"
    RERUN = "rerun"
    UNKNOWN = "unknown"


class ChatMessageRequest(BaseModel):
    """Request to send a message to the chat agent."""

    project_id: int = Field(..., description="Project ID")
    run_id: int = Field(..., description="Run ID")
    message: str = Field(..., min_length=1, max_length=2000, description="User message")
    version_id: Optional[int] = Field(None, description="Optional project version ID")


class PendingChange(BaseModel):
    """A pending change that hasn't been applied via rerun yet."""

    type: str = Field(..., description="Type of change: competitor_add, competitor_remove, edit")
    brand: Optional[str] = Field(None, description="Brand name for competitor changes")
    field: Optional[str] = Field(None, description="Field name for edits")
    old_value: Optional[Any] = Field(None, description="Old value for edits")
    new_value: Optional[Any] = Field(None, description="New value for edits")


class ChatMessageResponse(BaseModel):
    """Response from the chat agent."""

    agent_response: str = Field(..., description="Agent's response text")
    tool_used: Optional[List[str]] = Field(
        None, description="Tools that were used (e.g., ['competitor_add'])"
    )
    updated_competitor_set: Optional[List[str]] = Field(
        None, description="Updated competitor list if changed"
    )
    updated_inputs: Optional[Dict[str, Any]] = Field(
        None, description="Updated input values if changed"
    )
    rerun_triggered: bool = Field(
        False, description="Whether a rerun was triggered"
    )
    rerun_blocked_reason: Optional[str] = Field(
        None, description="Reason why rerun was blocked (if requested but blocked)"
    )
    chat_message_id: int = Field(..., description="ID of the stored chat message")
    new_run_id: Optional[int] = Field(
        None, description="New run ID if rerun created a new run"
    )
    new_version_name: Optional[str] = Field(
        None, description="Version name of new run (e.g., 'v2', 'v3')"
    )
    pending_changes: Optional[List[PendingChange]] = Field(
        None, description="Current uncommitted changes"
    )


class IntentEntity(BaseModel):
    """Entity extracted from user intent."""

    type: str = Field(..., description="Entity type (e.g., 'brand', 'field', 'value')")
    value: Any = Field(..., description="Entity value")
    confidence: float = Field(1.0, ge=0, le=1, description="Confidence score")


class IntentClassificationResult(BaseModel):
    """Result from intent classification."""

    primary_intent: str = Field(..., description="Primary detected intent")
    entities: List[IntentEntity] = Field(
        default_factory=list, description="Extracted entities"
    )
    confidence: float = Field(1.0, ge=0, le=1, description="Classification confidence")
    raw_response: Optional[str] = Field(
        None, description="Raw LLM response for debugging"
    )
