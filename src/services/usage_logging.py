"""Usage logging service for token and cost tracking.

Logs all LLM API usage to enable cost tracking, quota management,
and usage analytics across users and sessions.
"""

import logging
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional, Dict, Any, List

from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.logging import UsageLog
from src.services.llm_gateway.client import LLMResponse

logger = logging.getLogger(__name__)


# Pricing per 1K tokens (as of 2024, GPT-4o pricing)
# Update these when OpenAI changes pricing
MODEL_PRICING = {
    "gpt-4o": {
        "prompt": Decimal("0.005"),  # $5 per 1M input tokens
        "completion": Decimal("0.015"),  # $15 per 1M output tokens
    },
    "gpt-4o-mini": {
        "prompt": Decimal("0.00015"),  # $0.15 per 1M input tokens
        "completion": Decimal("0.0006"),  # $0.60 per 1M output tokens
    },
    "gpt-4-turbo": {
        "prompt": Decimal("0.01"),
        "completion": Decimal("0.03"),
    },
    "gpt-4": {
        "prompt": Decimal("0.03"),
        "completion": Decimal("0.06"),
    },
    "gpt-3.5-turbo": {
        "prompt": Decimal("0.0005"),
        "completion": Decimal("0.0015"),
    },
}

# Default pricing for unknown models
DEFAULT_PRICING = {
    "prompt": Decimal("0.01"),
    "completion": Decimal("0.03"),
}


class UsageLoggingService:
    """Service for logging and tracking LLM API usage.

    Tracks token usage and calculates costs for billing and
    analytics purposes.
    """

    def __init__(self, session: AsyncSession):
        """Initialize the usage logging service.

        Args:
            session: Database session for persistence
        """
        self.session = session

    async def log_usage(
        self,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        run_id: Optional[int] = None,
        prompt_trace_id: Optional[int] = None,
        user_id: Optional[int] = None,
        session_token: Optional[str] = None,
        request_type: str = "generation",
        status: str = "success",
        extra_data: Optional[Dict[str, Any]] = None,
    ) -> UsageLog:
        """Log a single LLM API usage event.

        Args:
            model: Model used (e.g., "gpt-4o")
            prompt_tokens: Number of input tokens
            completion_tokens: Number of output tokens
            run_id: Optional run ID
            prompt_trace_id: Optional trace ID
            user_id: Optional user ID
            session_token: Optional session token
            request_type: Type of request (generation, embedding)
            status: success or error
            extra_data: Additional metadata

        Returns:
            Created UsageLog record
        """
        total_tokens = prompt_tokens + completion_tokens
        cost = self._calculate_cost(model, prompt_tokens, completion_tokens)

        usage = UsageLog(
            run_id=run_id,
            prompt_trace_id=prompt_trace_id,
            user_id=user_id,
            session_token=session_token,
            logged_at=datetime.utcnow(),
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            cost_usd=cost,
            request_type=request_type,
            status=status,
            extra_data=extra_data,
        )

        self.session.add(usage)
        await self.session.flush()
        return usage

    async def log_from_response(
        self,
        response: LLMResponse,
        run_id: Optional[int] = None,
        prompt_trace_id: Optional[int] = None,
        user_id: Optional[int] = None,
        session_token: Optional[str] = None,
    ) -> UsageLog:
        """Log usage from an LLMResponse object.

        Convenience method to log usage directly from the LLM client response.

        Args:
            response: LLMResponse from OpenAI client
            run_id: Optional run ID
            prompt_trace_id: Optional trace ID
            user_id: Optional user ID
            session_token: Optional session token

        Returns:
            Created UsageLog record
        """
        return await self.log_usage(
            model=response.model,
            prompt_tokens=response.prompt_tokens,
            completion_tokens=response.completion_tokens,
            run_id=run_id,
            prompt_trace_id=prompt_trace_id,
            user_id=user_id,
            session_token=session_token,
            extra_data={
                "latency_ms": response.latency_ms,
                "finish_reason": response.finish_reason,
            },
        )

    async def get_usage_by_session(
        self,
        session_token: str,
        hours: int = 24,
    ) -> Dict[str, Any]:
        """Get usage statistics for a session.

        Args:
            session_token: Session identifier
            hours: Time window in hours

        Returns:
            Usage statistics dictionary
        """
        cutoff = datetime.utcnow() - timedelta(hours=hours)

        query = select(
            func.count(UsageLog.id).label("total_requests"),
            func.sum(UsageLog.prompt_tokens).label("total_prompt_tokens"),
            func.sum(UsageLog.completion_tokens).label("total_completion_tokens"),
            func.sum(UsageLog.total_tokens).label("total_tokens"),
            func.sum(UsageLog.cost_usd).label("total_cost"),
        ).where(
            and_(
                UsageLog.session_token == session_token,
                UsageLog.logged_at >= cutoff,
            )
        )

        result = await self.session.execute(query)
        row = result.one()

        return {
            "session_token": session_token,
            "period_hours": hours,
            "total_requests": row.total_requests or 0,
            "total_prompt_tokens": row.total_prompt_tokens or 0,
            "total_completion_tokens": row.total_completion_tokens or 0,
            "total_tokens": row.total_tokens or 0,
            "total_cost_usd": float(row.total_cost or 0),
        }

    async def get_usage_by_user(
        self,
        user_id: int,
        days: int = 30,
    ) -> Dict[str, Any]:
        """Get usage statistics for a user.

        Args:
            user_id: User ID
            days: Time window in days

        Returns:
            Usage statistics dictionary
        """
        cutoff = datetime.utcnow() - timedelta(days=days)

        query = select(
            func.count(UsageLog.id).label("total_requests"),
            func.sum(UsageLog.prompt_tokens).label("total_prompt_tokens"),
            func.sum(UsageLog.completion_tokens).label("total_completion_tokens"),
            func.sum(UsageLog.total_tokens).label("total_tokens"),
            func.sum(UsageLog.cost_usd).label("total_cost"),
        ).where(
            and_(
                UsageLog.user_id == user_id,
                UsageLog.logged_at >= cutoff,
            )
        )

        result = await self.session.execute(query)
        row = result.one()

        return {
            "user_id": user_id,
            "period_days": days,
            "total_requests": row.total_requests or 0,
            "total_prompt_tokens": row.total_prompt_tokens or 0,
            "total_completion_tokens": row.total_completion_tokens or 0,
            "total_tokens": row.total_tokens or 0,
            "total_cost_usd": float(row.total_cost or 0),
        }

    async def get_usage_by_run(self, run_id: int) -> Dict[str, Any]:
        """Get usage statistics for a specific run.

        Args:
            run_id: Run ID

        Returns:
            Usage statistics dictionary
        """
        query = select(
            func.count(UsageLog.id).label("total_requests"),
            func.sum(UsageLog.prompt_tokens).label("total_prompt_tokens"),
            func.sum(UsageLog.completion_tokens).label("total_completion_tokens"),
            func.sum(UsageLog.total_tokens).label("total_tokens"),
            func.sum(UsageLog.cost_usd).label("total_cost"),
        ).where(UsageLog.run_id == run_id)

        result = await self.session.execute(query)
        row = result.one()

        return {
            "run_id": run_id,
            "total_requests": row.total_requests or 0,
            "total_prompt_tokens": row.total_prompt_tokens or 0,
            "total_completion_tokens": row.total_completion_tokens or 0,
            "total_tokens": row.total_tokens or 0,
            "total_cost_usd": float(row.total_cost or 0),
        }

    async def get_daily_usage(
        self,
        days: int = 7,
    ) -> List[Dict[str, Any]]:
        """Get daily usage breakdown.

        Args:
            days: Number of days to include

        Returns:
            List of daily usage dictionaries
        """
        cutoff = datetime.utcnow() - timedelta(days=days)

        query = select(
            func.date(UsageLog.logged_at).label("date"),
            func.count(UsageLog.id).label("total_requests"),
            func.sum(UsageLog.total_tokens).label("total_tokens"),
            func.sum(UsageLog.cost_usd).label("total_cost"),
        ).where(
            UsageLog.logged_at >= cutoff
        ).group_by(
            func.date(UsageLog.logged_at)
        ).order_by(
            func.date(UsageLog.logged_at).desc()
        )

        result = await self.session.execute(query)
        rows = result.all()

        return [
            {
                "date": str(row.date),
                "total_requests": row.total_requests or 0,
                "total_tokens": row.total_tokens or 0,
                "total_cost_usd": float(row.total_cost or 0),
            }
            for row in rows
        ]

    async def get_model_usage(
        self,
        days: int = 30,
    ) -> List[Dict[str, Any]]:
        """Get usage breakdown by model.

        Args:
            days: Time window in days

        Returns:
            List of usage dictionaries per model
        """
        cutoff = datetime.utcnow() - timedelta(days=days)

        query = select(
            UsageLog.model,
            func.count(UsageLog.id).label("total_requests"),
            func.sum(UsageLog.total_tokens).label("total_tokens"),
            func.sum(UsageLog.cost_usd).label("total_cost"),
        ).where(
            UsageLog.logged_at >= cutoff
        ).group_by(
            UsageLog.model
        ).order_by(
            func.sum(UsageLog.total_tokens).desc()
        )

        result = await self.session.execute(query)
        rows = result.all()

        return [
            {
                "model": row.model,
                "total_requests": row.total_requests or 0,
                "total_tokens": row.total_tokens or 0,
                "total_cost_usd": float(row.total_cost or 0),
            }
            for row in rows
        ]

    def _calculate_cost(
        self,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> Decimal:
        """Calculate cost for token usage.

        Args:
            model: Model name
            prompt_tokens: Number of input tokens
            completion_tokens: Number of output tokens

        Returns:
            Estimated cost in USD
        """
        pricing = MODEL_PRICING.get(model, DEFAULT_PRICING)

        prompt_cost = (Decimal(prompt_tokens) / 1000) * pricing["prompt"]
        completion_cost = (Decimal(completion_tokens) / 1000) * pricing["completion"]

        return prompt_cost + completion_cost

    def estimate_cost(
        self,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> float:
        """Estimate cost without logging (for preview).

        Args:
            model: Model name
            prompt_tokens: Estimated input tokens
            completion_tokens: Estimated output tokens

        Returns:
            Estimated cost in USD
        """
        return float(self._calculate_cost(model, prompt_tokens, completion_tokens))
