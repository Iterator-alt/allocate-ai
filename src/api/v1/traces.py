"""Prompt trace API endpoints (Owner only).

Endpoints:
- GET /runs/{id}/trace - Get prompt traces for a run (Owner only)
"""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.dependencies import get_db
from src.api.schemas import (
    PromptTraceResponse,
    PromptTraceListResponse,
    ErrorResponse,
)
from src.api.middleware import get_session_context, SessionContext, require_owner
from src.repositories import RunRepository, PromptTraceRepository

router = APIRouter(prefix="/runs", tags=["traces"])


@router.get(
    "/{run_id}/trace",
    response_model=PromptTraceListResponse,
    responses={
        200: {"description": "Prompt traces retrieved"},
        401: {"model": ErrorResponse, "description": "Unauthorized"},
        403: {"model": ErrorResponse, "description": "Forbidden - Owner access required"},
        404: {"model": ErrorResponse, "description": "Run not found"},
    },
)
async def get_prompt_traces(
    request: Request,
    run_id: int,
    db: AsyncSession = Depends(get_db),
) -> PromptTraceListResponse:
    """Get prompt traces for a run (Owner only).

    Returns all LLM API calls made during the run, including:
    - Full prompts sent to the LLM
    - Raw responses received
    - Token usage (prompt, completion, total)
    - Latency in milliseconds
    - Success/failure status

    This endpoint is restricted to owners for security and debugging purposes.
    The traces contain sensitive prompt content that should not be exposed
    to regular users.
    """
    # Require owner access
    session: SessionContext = require_owner(request)

    run_repo = RunRepository(db)
    trace_repo = PromptTraceRepository(db)

    # Verify run exists
    run = await run_repo.get(run_id)
    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run {run_id} not found",
        )

    # Get all traces for the run
    traces = await trace_repo.get_by_run(run_id)

    # Calculate aggregate statistics
    total_tokens = sum(t.total_tokens or 0 for t in traces)
    total_latency_ms = sum(t.latency_ms or 0 for t in traces)
    successful_calls = sum(1 for t in traces if t.status == "success")
    success_rate = successful_calls / len(traces) if traces else 0.0

    # Convert to response format
    trace_responses = [
        PromptTraceResponse(
            id=trace.id,
            run_id=trace.run_id,
            called_at=trace.called_at,
            model=trace.model,
            prompt=trace.prompt,
            response=trace.response,
            prompt_tokens=trace.prompt_tokens,
            completion_tokens=trace.completion_tokens,
            total_tokens=trace.total_tokens,
            latency_ms=trace.latency_ms,
            status=trace.status,
            error_message=trace.error_message,
        )
        for trace in traces
    ]

    return PromptTraceListResponse(
        run_id=run_id,
        traces=trace_responses,
        total_traces=len(traces),
        total_tokens=total_tokens,
        total_latency_ms=total_latency_ms,
        success_rate=success_rate,
    )
