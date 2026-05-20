"""Schemas for competitor-related endpoints."""

from typing import List, Optional, Dict, Any
from decimal import Decimal

from pydantic import BaseModel, Field

from src.api.schemas.common import BaseSchema


class CompetitorBrand(BaseModel):
    """A single competitor brand."""

    nielsen_brand: str = Field(..., description="Brand name from Nielsen data")
    yougov_brand_label: Optional[str] = Field(None, description="Mapped YouGov brand label")
    wirtschaftsgruppe: str = Field(..., description="Industry classification")
    has_nielsen_data: bool = Field(True, description="Whether Nielsen spend data exists")
    has_yougov_data: bool = Field(True, description="Whether YouGov KPI data exists")
    total_spend_eur: Optional[Decimal] = Field(None, description="Total spend in analysis period")
    match_confidence: Optional[float] = Field(None, ge=0, le=1, description="Brand mapping confidence")


class CompetitorSetResponse(BaseModel):
    """Response containing matched competitors for a run."""

    run_id: int
    industry: str
    sector_label: str = Field(..., description="Mapped YouGov sector label")
    competitors: List[CompetitorBrand]
    total_competitors: int
    competitors_with_full_data: int = Field(..., description="Count with both Nielsen and YouGov data")
    warnings: List[str] = Field(default_factory=list, description="Data coverage warnings")


class ConfirmCompetitorsRequest(BaseModel):
    """Request to confirm or cancel competitor set."""

    action: str = Field(..., pattern="^(approve|cancel)$", description="Approve or cancel the competitor set")
    selected_competitors: Optional[List[str]] = Field(
        None,
        description="Optional: subset of competitors to include (if not all)"
    )
    reason: Optional[str] = Field(None, max_length=500, description="Optional reason for cancellation")


class ConfirmCompetitorsResponse(BaseModel):
    """Response after confirming competitors."""

    run_id: int
    status: str
    confirmed_competitors: Optional[List[str]] = None
    message: str
