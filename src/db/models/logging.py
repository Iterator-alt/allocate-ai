"""Logging tables: Usage logs for token and cost tracking."""

from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Numeric, JSON
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


class UsageLog(Base):
    """Token and cost tracking for LLM API usage.

    Records usage metrics for each LLM call to enable cost tracking,
    quota management, and usage analytics.
    """

    __tablename__ = "usage_logs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    # Link to run and trace
    run_id: Mapped[Optional[int]] = mapped_column(ForeignKey("runs.id"), nullable=True, index=True)
    prompt_trace_id: Mapped[Optional[int]] = mapped_column(ForeignKey("prompt_traces.id"), nullable=True)

    # User context
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    session_token: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)

    # Timestamp
    logged_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Model used
    model: Mapped[str] = mapped_column(String(100), nullable=False, index=True)

    # Token counts
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Cost calculation (in USD)
    cost_usd: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 6), nullable=True)

    # Request type (generation, embedding, etc.)
    request_type: Mapped[str] = mapped_column(String(50), nullable=False, default="generation")

    # Status (success, error)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="success")

    # Additional data (renamed from 'metadata' - reserved in SQLAlchemy)
    extra_data: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
