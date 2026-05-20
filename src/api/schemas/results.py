"""Schemas for allocation result endpoints."""

from datetime import datetime
from decimal import Decimal
from typing import List, Optional, Dict, Any

from pydantic import BaseModel, Field

from src.api.schemas.common import BaseSchema, TimestampMixin


class ChannelAllocation(BaseModel):
    """Allocation for a single channel."""

    channel: str = Field(..., description="Channel name (e.g., TV, Digital, Print)")
    share_pct: Decimal = Field(..., ge=0, le=100, description="Percentage share of budget")
    budget_gross_eur: Optional[Decimal] = Field(None, ge=0, description="Absolute budget amount in EUR")
    reasoning: Optional[str] = Field(None, description="Rationale for this allocation")


class KPIProjection(BaseModel):
    """Projected KPI impact."""

    kpi_name: str = Field(..., description="KPI metric name")
    current_value: Optional[Decimal] = Field(None, description="Current KPI value")
    projected_value: Optional[Decimal] = Field(None, description="Projected value after allocation")
    change_pct: Optional[Decimal] = Field(None, description="Percentage change")
    confidence: Optional[Decimal] = Field(None, ge=0, le=1, description="Projection confidence")


class AllocationResultResponse(BaseSchema, TimestampMixin):
    """Full allocation result response."""

    run_id: int
    allocations: List[ChannelAllocation]
    total_budget_eur: Optional[Decimal] = None
    kpi_projection: Optional[KPIProjection] = None
    reasoning_summary: Optional[str] = Field(None, description="Overall allocation rationale")
    confidence_score: Optional[Decimal] = Field(None, ge=0, le=1, description="Model confidence")
    warnings: List[str] = Field(default_factory=list, description="Data quality warnings")
    is_cached: bool = Field(False, description="Whether result was served from cache")


class AllocationSummary(BaseModel):
    """Brief summary of allocation for list views."""

    run_id: int
    status: str
    top_channel: Optional[str] = None
    top_channel_share: Optional[Decimal] = None
    confidence_score: Optional[Decimal] = None
    completed_at: Optional[datetime] = None
