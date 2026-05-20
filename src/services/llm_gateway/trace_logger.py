"""Prompt trace logging service.

Logs all LLM API calls to the prompt_traces table for observability
and debugging. Integrates with the OpenAI client to automatically
capture prompts, responses, and metrics.
"""

import logging
from datetime import datetime
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import PromptTrace
from src.repositories.prompt import PromptTraceRepository
from src.services.llm_gateway.client import LLMResponse

logger = logging.getLogger(__name__)


class PromptTraceLogger:
    """Service for logging LLM API calls to the database.

    Usage:
        logger = PromptTraceLogger(db_session)
        trace = await logger.start_trace(run_id, model, prompt)
        try:
            response = await llm_client.generate(...)
            await logger.complete_trace(trace.id, response)
        except Exception as e:
            await logger.fail_trace(trace.id, str(e))
    """

    def __init__(self, session: AsyncSession):
        """Initialize the trace logger.

        Args:
            session: Database session for persistence
        """
        self.session = session
        self.trace_repo = PromptTraceRepository(session)

    async def start_trace(
        self,
        run_id: int,
        model: str,
        prompt: str,
    ) -> PromptTrace:
        """Start a new trace for an LLM call.

        Call this before making the LLM API request to record the prompt
        and start timing.

        Args:
            run_id: ID of the run making this call
            model: Model name (e.g., "gpt-4o")
            prompt: Full assembled prompt being sent

        Returns:
            Created PromptTrace record
        """
        trace = PromptTrace(
            run_id=run_id,
            called_at=datetime.utcnow(),
            model=model,
            prompt=prompt,
            status="pending",
        )
        self.session.add(trace)
        await self.session.flush()
        return trace

    async def complete_trace(
        self,
        trace_id: int,
        response: LLMResponse,
    ) -> Optional[PromptTrace]:
        """Complete a trace with successful response.

        Call this after receiving a successful LLM response.

        Args:
            trace_id: ID of the trace to complete
            response: LLMResponse from the client

        Returns:
            Updated PromptTrace or None if not found
        """
        trace = await self.trace_repo.get(trace_id)
        if not trace:
            logger.warning(f"Trace {trace_id} not found for completion")
            return None

        trace.response = response.content
        trace.prompt_tokens = response.prompt_tokens
        trace.completion_tokens = response.completion_tokens
        trace.total_tokens = response.total_tokens
        trace.latency_ms = response.latency_ms
        trace.status = "success"
        trace.error_message = None

        await self.session.flush()
        return trace

    async def fail_trace(
        self,
        trace_id: int,
        error_message: str,
        status: str = "error",
        latency_ms: Optional[int] = None,
    ) -> Optional[PromptTrace]:
        """Record a failed trace.

        Call this when the LLM call fails.

        Args:
            trace_id: ID of the trace to update
            error_message: Error description
            status: Status (error, timeout, cancelled)
            latency_ms: Optional latency if available

        Returns:
            Updated PromptTrace or None if not found
        """
        trace = await self.trace_repo.get(trace_id)
        if not trace:
            logger.warning(f"Trace {trace_id} not found for failure")
            return None

        trace.status = status
        trace.error_message = error_message
        if latency_ms is not None:
            trace.latency_ms = latency_ms

        await self.session.flush()
        return trace

    async def log_complete_call(
        self,
        run_id: int,
        model: str,
        prompt: str,
        response: Optional[LLMResponse] = None,
        error_message: Optional[str] = None,
        status: str = "success",
    ) -> PromptTrace:
        """Log a complete LLM call in one operation.

        Convenience method for logging a call that has already completed.
        Use start_trace/complete_trace for real-time logging.

        Args:
            run_id: ID of the run
            model: Model name
            prompt: Full prompt sent
            response: Optional LLMResponse if successful
            error_message: Optional error if failed
            status: Call status

        Returns:
            Created PromptTrace record
        """
        trace = PromptTrace(
            run_id=run_id,
            called_at=datetime.utcnow(),
            model=model,
            prompt=prompt,
            status=status,
            error_message=error_message,
        )

        if response:
            trace.response = response.content
            trace.prompt_tokens = response.prompt_tokens
            trace.completion_tokens = response.completion_tokens
            trace.total_tokens = response.total_tokens
            trace.latency_ms = response.latency_ms
            trace.status = "success"

        self.session.add(trace)
        await self.session.flush()
        return trace

    async def get_run_statistics(self, run_id: int) -> dict:
        """Get trace statistics for a run.

        Args:
            run_id: ID of the run

        Returns:
            Dictionary with usage statistics
        """
        return await self.trace_repo.get_usage_stats(run_id)
