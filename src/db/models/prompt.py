"""Prompt management tables: Expert knowledge, guardrails, and traces."""

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, Boolean, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base, TimestampMixin


class ExpertKnowledge(Base, TimestampMixin):
    """Versioned expert knowledge for media planning heuristics.

    Contains domain expertise and heuristics that guide the LLM in making
    budget allocation recommendations. Versioned to track changes over time.
    """

    __tablename__ = "expert_knowledge"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    # Version control
    version: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    # Knowledge category (e.g., "channel_heuristics", "budget_rules", "seasonality")
    category: Mapped[str] = mapped_column(String(100), nullable=False, index=True)

    # The actual knowledge content
    content: Mapped[str] = mapped_column(Text, nullable=False)

    # Structured content (optional JSON for machine-readable rules)
    structured_content: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # Active version flag
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Description of changes in this version
    change_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class PromptGuardrails(Base, TimestampMixin):
    """Versioned output constraints for LLM responses.

    Defines constraints and formatting rules that the LLM must follow
    when generating budget allocation recommendations.
    """

    __tablename__ = "prompt_guardrails"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    # Version control
    version: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    # Guardrail type (e.g., "output_format", "value_constraints", "validation_rules")
    guardrail_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)

    # The guardrail content
    content: Mapped[str] = mapped_column(Text, nullable=False)

    # Structured rules (optional JSON for machine-readable constraints)
    structured_rules: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # Active version flag
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Description of changes
    change_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class PromptTrace(Base):
    """Per-LLM-call observability and debugging.

    Records every LLM API call for debugging, auditing, and performance
    analysis. Owner-only access for reviewing prompts and responses.
    """

    __tablename__ = "prompt_traces"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    # Link to the run that triggered this call
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"), nullable=False, index=True)

    # Timestamp of the call
    called_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Model used
    model: Mapped[str] = mapped_column(String(100), nullable=False)

    # The full prompt sent to the LLM
    prompt: Mapped[str] = mapped_column(Text, nullable=False)

    # The raw response from the LLM
    response: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Token counts
    prompt_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Latency in milliseconds
    latency_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Status (success, error, timeout)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="pending")

    # Error message if failed
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Relationship
    run: Mapped["Run"] = relationship("Run", back_populates="prompt_traces")


# Import Run for type hints (avoid circular import at runtime)
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from src.db.models.run import Run
