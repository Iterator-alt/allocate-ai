"""Schemas for prompt trace endpoints (Owner only)."""

from datetime import datetime
from typing import List, Optional, Dict, Any

from pydantic import BaseModel, Field

from src.api.schemas.common import BaseSchema


class PromptTraceResponse(BaseSchema):
    """Response for a single prompt trace."""

    id: int
    run_id: int
    called_at: datetime
    model: str
    prompt: str = Field(..., description="Full assembled prompt sent to LLM")
    response: Optional[str] = Field(None, description="Raw LLM response")
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    latency_ms: Optional[int] = Field(None, description="Response latency in milliseconds")
    status: str = Field(..., description="success / error / timeout / cancelled")
    error_message: Optional[str] = None


class PromptTraceListResponse(BaseModel):
    """Response containing all traces for a run."""

    run_id: int
    traces: List[PromptTraceResponse]
    total_traces: int
    total_tokens: int = Field(..., description="Sum of all tokens used")
    total_latency_ms: int = Field(..., description="Sum of all latencies")
    success_rate: float = Field(..., ge=0, le=1, description="Proportion of successful calls")


class PromptTraceSummary(BaseModel):
    """Summary of prompt trace for list views."""

    id: int
    run_id: int
    called_at: datetime
    model: str
    total_tokens: Optional[int] = None
    latency_ms: Optional[int] = None
    status: str


class UsageStats(BaseModel):
    """Token usage statistics."""

    period_start: datetime
    period_end: datetime
    total_runs: int
    total_tokens: int
    total_prompt_tokens: int
    total_completion_tokens: int
    estimated_cost_usd: float
    average_latency_ms: float
    success_rate: float
