"""Run management API endpoints.

Endpoints:
- POST /runs - Create a new generation run
- GET /runs/{id}/status - Poll run state
- POST /runs/{id}/stop - Cancel in-flight or queued run
- POST /runs/{id}/process - Manually trigger pipeline processing (mock)
- POST /runs/{id}/process-ai - Trigger real AI pipeline processing
"""

import logging
from datetime import datetime, timezone
from typing import Optional
from decimal import Decimal
import json

from fastapi import APIRouter, Depends, HTTPException, Request, status, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.dependencies import get_db
from src.api.schemas import (
    CreateRunRequest,
    RunResponse,
    RunStatusResponse,
    StopRunRequest,
    StopRunResponse,
    RunStatus,
    ErrorResponse,
)
from src.api.middleware import (
    get_session_context,
    SessionContext,
    generation_rate_limit,
    limiter,
)
from src.repositories import RunRepository
from src.db.models.run import RunStatus as DBRunStatus, AllocationResult, ChatHistory

# AI Pipeline imports
from src.services.llm_gateway.client import OpenAIClient
from src.services.llm_gateway.trace_logger import PromptTraceLogger
from src.services.mediamix.prompt_assembly import PromptAssemblyService, PromptAssemblyInput
from src.services.mediamix.output_parsing import OutputParsingService
from src.services.mediamix.feedback_generation import FeedbackGenerationService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/runs", tags=["runs"])


@router.post(
    "",
    response_model=RunResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        201: {"description": "Run created successfully"},
        400: {"model": ErrorResponse, "description": "Invalid request"},
        401: {"model": ErrorResponse, "description": "Unauthorized"},
        409: {"model": ErrorResponse, "description": "Active run already exists"},
        429: {"model": ErrorResponse, "description": "Rate limit exceeded"},
    },
)
@limiter.limit("20/hour")
async def create_run(
    request: Request,
    run_request: CreateRunRequest,
    db: AsyncSession = Depends(get_db),
) -> RunResponse:
    """Create a new generation run.

    Creates a new budget allocation generation run. Only one active run
    is allowed per session at a time.

    Rate limit: 20 generations per user per hour.
    """
    session: SessionContext = await get_session_context(request)
    run_repo = RunRepository(db)

    # Check for existing active run (session lock)
    active_run = await run_repo.get_active_run_for_session(session.session_token)
    if active_run:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Active run already exists (run_id={active_run.id}, status={active_run.status}). "
                   "Please wait for it to complete or cancel it first.",
        )

    # Create the run
    run = await run_repo.create_run(
        session_token=session.session_token,
        customer_name=run_request.customer_name,
        industry=run_request.industry,
        brand_kpi=run_request.brand_kpi,
        user_id=session.user_id,
        total_budget=float(run_request.total_budget) if run_request.total_budget else None,
        time_period_start=run_request.time_period_start,
        time_period_end=run_request.time_period_end,
        input_parameters={
            "channels": run_request.channels,
            "goal_text": run_request.goal_text,
            "direction": run_request.direction,
        } if any([run_request.channels, run_request.goal_text, run_request.direction]) else None,
    )

    await db.commit()

    return RunResponse(
        id=run.id,
        session_token=run.session_token,
        customer_name=run.customer_name,
        industry=run.industry,
        brand_kpi=run.brand_kpi,
        total_budget=run.total_budget,
        time_period_start=run.time_period_start,
        time_period_end=run.time_period_end,
        status=RunStatus(run.status),
        started_at=run.started_at,
        completed_at=run.completed_at,
        error_message=run.error_message,
        created_at=run.created_at,
        updated_at=run.updated_at,
    )


@router.get(
    "/{run_id}/status",
    response_model=RunStatusResponse,
    responses={
        200: {"description": "Run status retrieved"},
        401: {"model": ErrorResponse, "description": "Unauthorized"},
        403: {"model": ErrorResponse, "description": "Forbidden - not your run"},
        404: {"model": ErrorResponse, "description": "Run not found"},
    },
)
async def get_run_status(
    request: Request,
    run_id: int,
    db: AsyncSession = Depends(get_db),
) -> RunStatusResponse:
    """Get the status of a generation run.

    Use this endpoint to poll for run completion. The status field
    indicates the current stage of processing.

    Status progression:
    - pending → matching → awaiting_confirmation → generating → parsing → feedback → completed
    - Any stage can transition to failed or cancelled
    """
    session: SessionContext = await get_session_context(request)
    run_repo = RunRepository(db)

    run = await run_repo.get(run_id)
    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run {run_id} not found",
        )

    # Verify the run belongs to this session (or user is owner)
    if run.session_token != session.session_token and not session.is_owner:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied - this run belongs to a different session",
        )

    # Generate human-readable progress message
    progress_messages = {
        DBRunStatus.PENDING.value: "Queued for processing",
        DBRunStatus.MATCHING.value: "Finding competitor brands...",
        DBRunStatus.AWAITING_CONFIRMATION.value: "Waiting for competitor confirmation",
        DBRunStatus.GENERATING.value: "Generating allocation recommendations...",
        DBRunStatus.PARSING.value: "Processing results...",
        DBRunStatus.FEEDBACK.value: "Generating feedback...",
        DBRunStatus.COMPLETED.value: "Completed",
        DBRunStatus.FAILED.value: "Failed",
        DBRunStatus.CANCELLED.value: "Cancelled",
    }

    return RunStatusResponse(
        id=run.id,
        status=RunStatus(run.status),
        started_at=run.started_at,
        completed_at=run.completed_at,
        error_message=run.error_message,
        progress=progress_messages.get(run.status, "Processing..."),
        queue_position=None,  # TODO: Implement queue tracking
        eta_seconds=None,  # TODO: Implement ETA calculation
    )


@router.post(
    "/{run_id}/stop",
    response_model=StopRunResponse,
    responses={
        200: {"description": "Run stopped successfully"},
        400: {"model": ErrorResponse, "description": "Run cannot be stopped"},
        401: {"model": ErrorResponse, "description": "Unauthorized"},
        403: {"model": ErrorResponse, "description": "Forbidden - not your run"},
        404: {"model": ErrorResponse, "description": "Run not found"},
    },
)
async def stop_run(
    request: Request,
    run_id: int,
    stop_request: Optional[StopRunRequest] = None,
    db: AsyncSession = Depends(get_db),
) -> StopRunResponse:
    """Stop/cancel a generation run.

    Cancels an in-flight or queued run. If the LLM call is in progress,
    the stream will be discarded and a partial prompt trace recorded.

    Already completed, failed, or cancelled runs cannot be stopped.
    """
    session: SessionContext = await get_session_context(request)
    run_repo = RunRepository(db)

    run = await run_repo.get(run_id)
    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run {run_id} not found",
        )

    # Verify the run belongs to this session (or user is owner)
    if run.session_token != session.session_token and not session.is_owner:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied - this run belongs to a different session",
        )

    # Check if run can be stopped
    terminal_statuses = [
        DBRunStatus.COMPLETED.value,
        DBRunStatus.FAILED.value,
        DBRunStatus.CANCELLED.value,
    ]
    if run.status in terminal_statuses:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Run cannot be stopped - already in terminal state: {run.status}",
        )

    # Cancel the run
    reason = stop_request.reason if stop_request else None
    run = await run_repo.mark_cancelled(run_id, reason)
    await db.commit()

    return StopRunResponse(
        id=run.id,
        status=RunStatus(run.status),
        stopped_at=run.completed_at or datetime.utcnow(),
        message="Run cancelled successfully",
    )


@router.get(
    "/{run_id}",
    response_model=RunResponse,
    responses={
        200: {"description": "Run details retrieved"},
        401: {"model": ErrorResponse, "description": "Unauthorized"},
        403: {"model": ErrorResponse, "description": "Forbidden - not your run"},
        404: {"model": ErrorResponse, "description": "Run not found"},
    },
)
async def get_run(
    request: Request,
    run_id: int,
    db: AsyncSession = Depends(get_db),
) -> RunResponse:
    """Get full details of a generation run."""
    session: SessionContext = await get_session_context(request)
    run_repo = RunRepository(db)

    run = await run_repo.get(run_id)
    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run {run_id} not found",
        )

    # Verify the run belongs to this session (or user is owner)
    if run.session_token != session.session_token and not session.is_owner:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied - this run belongs to a different session",
        )

    return RunResponse(
        id=run.id,
        session_token=run.session_token,
        customer_name=run.customer_name,
        industry=run.industry,
        brand_kpi=run.brand_kpi,
        total_budget=run.total_budget,
        time_period_start=run.time_period_start,
        time_period_end=run.time_period_end,
        status=RunStatus(run.status),
        started_at=run.started_at,
        completed_at=run.completed_at,
        error_message=run.error_message,
        created_at=run.created_at,
        updated_at=run.updated_at,
    )


@router.post(
    "/{run_id}/process",
    responses={
        200: {"description": "Run processed successfully"},
        400: {"model": ErrorResponse, "description": "Run cannot be processed"},
        404: {"model": ErrorResponse, "description": "Run not found"},
    },
)
async def process_run(
    request: Request,
    run_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Manually trigger pipeline processing for a run (TESTING ONLY).

    This endpoint simulates the full pipeline:
    1. Updates status to 'completed'
    2. Creates sample allocation result
    3. Creates feedback messages

    In production, this would be handled by a background worker.
    """
    session: SessionContext = await get_session_context(request)
    run_repo = RunRepository(db)

    run = await run_repo.get(run_id)
    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run {run_id} not found",
        )

    # Check if already processed
    if run.status in [DBRunStatus.COMPLETED.value, DBRunStatus.CANCELLED.value, DBRunStatus.FAILED.value]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Run already in terminal state: {run.status}",
        )

    # Get the total budget or use default
    total_budget = float(run.total_budget) if run.total_budget else 4000000.0

    # Create sample allocation based on budget
    allocation_data = {
        "channels": [
            {"name": "TV", "percentage": 37.0, "amount": round(total_budget * 0.37, 2)},
            {"name": "ONLINE", "percentage": 28.0, "amount": round(total_budget * 0.28, 2)},
            {"name": "SOCIAL", "percentage": 22.0, "amount": round(total_budget * 0.22, 2)},
            {"name": "OOH", "percentage": 13.0, "amount": round(total_budget * 0.13, 2)},
        ],
        "total_budget": total_budget,
        "kpi_projection": 3.2,
        "cost_per_kpi_point": round(total_budget / 3.2, 2),
    }

    # Create allocation result
    allocation = AllocationResult(
        run_id=run_id,
        allocations=allocation_data,
        summary=f"Budget allocation for {run.customer_name} in {run.industry} sector. "
                f"TV leads at 37% (EUR {allocation_data['channels'][0]['amount']:,.0f}), "
                f"followed by Online at 28% (EUR {allocation_data['channels'][1]['amount']:,.0f}). "
                f"Total budget: EUR {total_budget:,.0f}.",
        confidence_score=Decimal("0.78"),
        is_valid=True,
    )
    db.add(allocation)

    # Create feedback messages
    messages = [
        ChatHistory(
            run_id=run_id,
            message_type="summary",
            severity="info",
            title="Allocation Complete",
            content=f"TV leads at 37%, followed by Online at 28%. "
                    f"Budget distributed across 4 channels totaling EUR {total_budget:,.0f}.",
            display_order=1,
        ),
        ChatHistory(
            run_id=run_id,
            message_type="warning",
            severity="warning",
            title="Limited Historical Data",
            content=f"Some channels have limited benchmark data for {run.industry} sector. "
                    "Recommendation confidence is moderate.",
            display_order=2,
        ),
        ChatHistory(
            run_id=run_id,
            message_type="alert",
            severity="error",
            title="Competitor Activity Detected",
            content="Competitors have increased Online spend by +15% in the last quarter. "
                    "Consider adjusting digital allocation.",
            display_order=3,
        ),
        ChatHistory(
            run_id=run_id,
            message_type="recommendation",
            severity="info",
            title="Consider Connected TV",
            content="Connected TV is growing in your sector. Consider allocating 5-10% "
                    "of TV budget to CTV for younger demographics.",
            display_order=4,
        ),
    ]
    for msg in messages:
        db.add(msg)

    # Update run status to completed
    run.status = DBRunStatus.COMPLETED.value
    run.started_at = datetime.now(timezone.utc)
    run.completed_at = datetime.now(timezone.utc)

    await db.commit()

    return {
        "id": run_id,
        "status": "completed",
        "message": "Run processed successfully",
        "allocation_summary": {
            "TV": "37%",
            "ONLINE": "28%",
            "SOCIAL": "22%",
            "OOH": "13%",
            "total_budget": f"EUR {total_budget:,.0f}",
        },
        "feedback_cards": 4,
    }


@router.post(
    "/{run_id}/process-ai",
    responses={
        200: {"description": "Run processed with real AI"},
        400: {"model": ErrorResponse, "description": "Run cannot be processed"},
        404: {"model": ErrorResponse, "description": "Run not found"},
        500: {"model": ErrorResponse, "description": "AI processing failed"},
    },
)
async def process_run_with_ai(
    request: Request,
    run_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Process a run using real OpenAI GPT-4o.

    This endpoint triggers the full AI pipeline:
    1. Assembles prompt with competitor data, expert knowledge, guardrails
    2. Calls OpenAI GPT-4o with JSON mode
    3. Parses and validates the LLM response
    4. Generates feedback cards based on the allocation
    5. Logs the prompt trace for observability

    This is a synchronous call - it will wait for the AI to respond.
    Typical response time: 5-15 seconds.
    """
    session: SessionContext = await get_session_context(request)
    run_repo = RunRepository(db)

    # Get the run
    run = await run_repo.get(run_id)
    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run {run_id} not found",
        )

    # Check if already processed
    if run.status in [DBRunStatus.COMPLETED.value, DBRunStatus.CANCELLED.value, DBRunStatus.FAILED.value]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Run already in terminal state: {run.status}",
        )

    # Mark run as started
    run.started_at = datetime.now(timezone.utc)
    run.status = DBRunStatus.GENERATING.value
    await db.commit()

    try:
        # Initialize services
        prompt_service = PromptAssemblyService(db)
        output_service = OutputParsingService(db)
        feedback_service = FeedbackGenerationService(db)
        trace_logger = PromptTraceLogger(db)
        llm_client = OpenAIClient()

        # Build prompt assembly input
        total_budget = Decimal(str(run.total_budget)) if run.total_budget else None

        # Extract channels from input_parameters if available
        channels = None
        if run.input_parameters and isinstance(run.input_parameters, dict):
            channels = run.input_parameters.get("channels")

        prompt_input = PromptAssemblyInput(
            customer_name=run.customer_name,
            industry=run.industry,
            brand_kpi=run.brand_kpi,
            total_budget=total_budget,
            time_period_start=run.time_period_start,
            time_period_end=run.time_period_end,
            channels=channels,
            # For demo, use customer name as the Nielsen brand and industry for YouGov
            nielsen_brands=[run.customer_name],
            yougov_brands=[run.customer_name],
            additional_context=run.input_parameters.get("goal_text") if run.input_parameters else None,
        )

        # Assemble the prompt
        logger.info(f"Assembling prompt for run {run_id}")
        assembled_prompt = await prompt_service.assemble_prompt(
            input_params=prompt_input,
            wirtschaftsgruppe=run.industry,
        )

        # Start trace logging
        full_prompt = f"SYSTEM:\n{assembled_prompt.system_prompt}\n\nUSER:\n{assembled_prompt.user_prompt}"
        trace = await trace_logger.start_trace(
            run_id=run_id,
            model=llm_client.model,
            prompt=full_prompt,
        )

        # Call OpenAI
        logger.info(f"Calling OpenAI for run {run_id}")
        try:
            llm_response = await llm_client.generate(
                system_prompt=assembled_prompt.system_prompt,
                user_prompt=assembled_prompt.user_prompt,
                temperature=0.7,
                max_tokens=4096,
                json_mode=True,
            )

            # Complete trace with success
            await trace_logger.complete_trace(trace.id, llm_response)
            logger.info(f"OpenAI response received for run {run_id}: {llm_response.total_tokens} tokens")

        except Exception as e:
            # Log the failure
            await trace_logger.fail_trace(trace.id, str(e))
            await db.commit()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"OpenAI API call failed: {str(e)}",
            )

        # Parse and validate the response
        logger.info(f"Parsing LLM response for run {run_id}")
        parsed_result = await output_service.parse_and_store(
            run_id=run_id,
            llm_response=llm_response,
            total_budget=total_budget,
        )

        if not parsed_result.is_valid:
            # Mark run as failed
            run.status = DBRunStatus.FAILED.value
            run.completed_at = datetime.now(timezone.utc)
            run.error_message = "LLM response failed validation"
            await db.commit()

            return {
                "id": run_id,
                "status": "failed",
                "message": "AI response failed validation",
                "validation_issues": [
                    {"field": i.field, "message": i.message}
                    for i in parsed_result.validation_issues
                ],
            }

        # Generate feedback cards
        logger.info(f"Generating feedback for run {run_id}")
        feedback_result = await feedback_service.generate_and_store(
            run_id=run_id,
            parsed_result=parsed_result,
            run=run,
        )

        # Commit all changes
        await db.commit()

        # Build response
        allocation_summary = {}
        for alloc in parsed_result.allocations:
            allocation_summary[alloc.channel] = f"{float(alloc.percentage):.1f}%"

        if total_budget:
            allocation_summary["total_budget"] = f"EUR {float(total_budget):,.0f}"

        return {
            "id": run_id,
            "status": "completed",
            "message": "Run processed successfully with AI",
            "ai_powered": True,
            "model": llm_response.model,
            "tokens_used": llm_response.total_tokens,
            "latency_ms": llm_response.latency_ms,
            "confidence": float(parsed_result.confidence) if parsed_result.confidence else None,
            "allocation_summary": allocation_summary,
            "feedback_cards": len(feedback_result.messages),
            "has_warnings": feedback_result.has_warnings,
            "has_alerts": feedback_result.has_alerts,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"AI processing failed for run {run_id}: {str(e)}")

        # Mark run as failed
        run.status = DBRunStatus.FAILED.value
        run.completed_at = datetime.now(timezone.utc)
        run.error_message = str(e)
        await db.commit()

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"AI processing failed: {str(e)}",
        )
