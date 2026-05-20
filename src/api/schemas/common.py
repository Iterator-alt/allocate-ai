"""Common schema types used across API endpoints."""

from datetime import datetime
from typing import Optional, Any

from pydantic import BaseModel, ConfigDict


class BaseSchema(BaseModel):
    """Base schema with common configuration."""

    model_config = ConfigDict(
        from_attributes=True,
        populate_by_name=True,
    )


class TimestampMixin(BaseModel):
    """Mixin for created_at and updated_at timestamps."""

    created_at: datetime
    updated_at: datetime


class ErrorResponse(BaseModel):
    """Standard error response."""

    error: str
    detail: Optional[str] = None
    code: Optional[str] = None


class SuccessResponse(BaseModel):
    """Standard success response."""

    success: bool = True
    message: Optional[str] = None


class PaginatedResponse(BaseModel):
    """Base for paginated responses."""

    total: int
    page: int
    page_size: int
    has_more: bool
