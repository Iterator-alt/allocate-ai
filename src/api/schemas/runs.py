"""Schemas for run-related endpoints."""

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional, List, Dict, Any

from pydantic import BaseModel, Field, field_validator

from src.api.schemas.common import BaseSchema, TimestampMixin


class RunStatus(str, Enum):
    """Run status enumeration."""

    PENDING = "pending"
    MATCHING = "matching"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    GENERATING = "generating"
    PARSING = "parsing"
    FEEDBACK = "feedback"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class CreateRunRequest(BaseModel):
    """Request to create a new generation run."""

    customer_name: str = Field(..., min_length=1, max_length=255, description="Customer/brand name")
    industry: str = Field(..., min_length=1, max_length=255, description="Industry (Wirtschaftsgruppe)")
    brand_kpi: str = Field(..., pattern="^(adaware|aided|consider)$", description="KPI metric to optimize")
    total_budget: Optional[Decimal] = Field(None, ge=0, description="Total budget in EUR")
    time_period_start: Optional[datetime] = Field(None, description="Analysis period start")
    time_period_end: Optional[datetime] = Field(None, description="Analysis period end")
    channels: Optional[List[str]] = Field(None, description="Specific channels to include")
    goal_text: Optional[str] = Field(None, max_length=1000, description="User's optimization goal")
    direction: Optional[str] = Field(None, pattern="^(increase|maintain|decrease)$", description="KPI direction flag")

    @field_validator("brand_kpi")
    @classmethod
    def validate_kpi(cls, v: str) -> str:
        """Ensure KPI is lowercase."""
        return v.lower()


class RunResponse(BaseSchema, TimestampMixin):
    """Response for a single run."""

    id: int
    session_token: str
    customer_name: str
    industry: str
    brand_kpi: str
    total_budget: Optional[Decimal] = None
    time_period_start: Optional[datetime] = None
    time_period_end: Optional[datetime] = None
    status: RunStatus
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    error_message: Optional[str] = None


class RunStatusResponse(BaseModel):
    """Response for run status polling."""

    id: int
    status: RunStatus
    stage: Optional[str] = Field(None, description="Current processing stage (S1, S1.5, S2, S3, S4)")
    progress_pct: Optional[int] = Field(None, ge=0, le=100, description="Progress percentage (0-100)")
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    error_message: Optional[str] = None
    progress: Optional[str] = Field(None, description="Human-readable progress message")
    queue_position: Optional[int] = Field(None, description="Position in queue if waiting")
    eta_seconds: Optional[int] = Field(None, description="Estimated time to completion")


class StopRunRequest(BaseModel):
    """Request to stop a run."""

    reason: Optional[str] = Field(None, max_length=500, description="Optional cancellation reason")


class StopRunResponse(BaseModel):
    """Response for stopping a run."""

    id: int
    status: RunStatus
    stopped_at: datetime
    message: str


class RunListResponse(BaseModel):
    """Response for listing runs."""

    runs: List[RunResponse]
    total: int


class StartRunRequest(BaseModel):
    """Request to start an existing run (Manager's Spec v2).

    The run must already exist in DB (created by JS Backend).
    This endpoint triggers Stage 1 (competitor matching) in background.
    """

    run_id: int = Field(..., description="ID of the existing run to start")
    action: str = Field(..., pattern="^start$", description="Must be 'start'")


class StartRunResponse(BaseModel):
    """Response for starting a run (Manager's Spec v2)."""

    run_id: int
    status: str = Field(..., description="'started' or 'error'")
    error_message: Optional[str] = None
