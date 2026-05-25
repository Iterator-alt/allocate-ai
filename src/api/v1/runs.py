"""Run management API endpoints.

Endpoints:
- POST /runs - Create a new generation run (auto-triggers Stage 1)
- GET /runs/{id}/status - Poll run state
- POST /runs/{id}/stop - Cancel in-flight or queued run

Flow:
1. POST /runs → creates run, auto-triggers Stage 1 (competitor matching)
2. Poll status until "awaiting_confirmation"
3. GET /competitors → review competitor list
4. POST /competitors/confirm → triggers Stage 2-4 (AI generation)
5. Poll status until "completed"
6. GET /result → allocation results
7. GET /chat → feedback cards
"""

import logging
import asyncio
from datetime import datetime, timezone
from typing import Optional
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request, status, BackgroundTasks
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
    StartRunRequest,
    StartRunResponse,
)
from src.api.middleware import (
    get_session_context,
    SessionContext,
    limiter,
)
from src.repositories import RunRepository
from src.db.models.run import RunStatus as DBRunStatus, AllocationResult, ChatHistory

# Stage 1 imports
from src.services.stage1 import (
    Stage1Orchestrator,
    UserCampaignInput,
    Stage1Result,
    Stage1Status,
)

# Stage 2-4 AI Pipeline imports
from src.services.llm_gateway.client import OpenAIClient
from src.services.llm_gateway.trace_logger import PromptTraceLogger
from src.services.mediamix.prompt_assembly import PromptAssemblyService, PromptAssemblyInput
from src.services.mediamix.output_parsing import OutputParsingService
from src.services.mediamix.feedback_generation import FeedbackGenerationService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/runs", tags=["runs"])


# =============================================================================
# Background Task: Stage 1 Processing
# =============================================================================

async def run_stage1_background(run_id: int, db_url: str):
    """Background task to run Stage 1 (competitor matching).

    This runs asynchronously after POST /runs returns.
    """
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    from sqlalchemy.orm import sessionmaker

    engine = create_async_engine(db_url)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as session:
        try:
            run_repo = RunRepository(session)
            run = await run_repo.get(run_id)

            if not run:
                logger.error(f"Run {run_id} not found for Stage 1 processing")
                return

            # Update status to matching
            await run_repo.update_status(run_id, DBRunStatus.MATCHING)
            await session.commit()

            # Execute Stage 1
            orchestrator = Stage1Orchestrator(session=session)

            user_input = UserCampaignInput(
                brand_name=run.customer_name,
                industry=run.industry,
                brand_kpi=run.brand_kpi,
                media_channels=run.input_parameters.get("channels", []) if run.input_parameters else [],
                goal_direction=run.input_parameters.get("direction", "budget_to_impact") if run.input_parameters else "budget_to_impact",
            )

            # Pass run_id for debug logging (enabled via STAGE1_DEBUG_MODE env var)
            result = await orchestrator.process(user_input, run_id=str(run_id))

            if result.status == Stage1Status.COMPLETED:
                # Store Stage 1 results in run
                run.confirmed_competitors = {
                    "stage1_result": {
                        "yougov_sectors": result.yougov_sectors,
                        "nielsen_sectors": result.nielsen_sectors,
                        "confirmed_brand": {
                            "yougov_brand": result.confirmed_brand.yougov_brand,
                            "nielsen_brand": result.confirmed_brand.nielsen_brand,
                            "match_type": result.confirmed_brand.match_type.value,
                            "confidence": result.confirmed_brand.confidence,
                        } if result.confirmed_brand else None,
                        "competitors": [
                            {
                                "brand_label": c.brand_label,
                                "nielsen_brand": c.nielsen_brand,
                                "avg_kpi_score": c.avg_kpi_score,
                                "total_spend_teuro": c.total_spend_teuro,
                            }
                            for c in result.competitors
                        ],
                        "brand_data": {
                            "brand_label": result.brand_data.brand_label,
                            "sector_label": result.brand_data.sector_label,
                            "adaware_score": result.brand_data.adaware_score,
                            "aware_score": result.brand_data.aware_score,
                            "consider_score": result.brand_data.consider_score,
                            "total_spend_teuro": result.brand_data.total_spend_teuro,
                            "channel_spend": result.brand_data.channel_spend,
                        } if result.brand_data else None,
                    },
                    "pending_confirmation": True,
                }

                # Update status to awaiting confirmation
                await run_repo.update_status(run_id, DBRunStatus.AWAITING_CONFIRMATION)
                await session.commit()
                logger.info(f"Stage 1 completed for run {run_id}, awaiting competitor confirmation")

            elif result.status == Stage1Status.FAILED:
                await run_repo.update_status(
                    run_id,
                    DBRunStatus.FAILED,
                    error_message="; ".join(result.errors) if result.errors else "Stage 1 failed"
                )
                await session.commit()
                logger.error(f"Stage 1 failed for run {run_id}: {result.errors}")

        except Exception as e:
            logger.error(f"Stage 1 background task failed for run {run_id}: {str(e)}")
            try:
                await run_repo.update_status(run_id, DBRunStatus.FAILED, error_message=str(e))
                await session.commit()
            except:
                pass


# =============================================================================
# Background Task: Stage 2-4 Processing (AI Generation)
# =============================================================================

async def run_ai_generation_background(run_id: int, db_url: str):
    """Background task to run Stage 2-4 (AI generation).

    This runs asynchronously after POST /competitors/confirm returns.
    """
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    from sqlalchemy.orm import sessionmaker

    engine = create_async_engine(db_url)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as session:
        try:
            run_repo = RunRepository(session)
            run = await run_repo.get(run_id)

            if not run:
                logger.error(f"Run {run_id} not found for AI generation")
                return

            # Initialize services
            prompt_service = PromptAssemblyService(session)
            output_service = OutputParsingService(session)
            feedback_service = FeedbackGenerationService(session)
            trace_logger = PromptTraceLogger(session)
            llm_client = OpenAIClient()

            # Build prompt assembly input
            total_budget = Decimal(str(run.total_budget)) if run.total_budget else None
            channels = run.input_parameters.get("channels") if run.input_parameters else None

            prompt_input = PromptAssemblyInput(
                customer_name=run.customer_name,
                industry=run.industry,
                brand_kpi=run.brand_kpi,
                total_budget=total_budget,
                time_period_start=run.time_period_start,
                time_period_end=run.time_period_end,
                channels=channels,
                nielsen_brands=[run.customer_name],
                yougov_brands=[run.customer_name],
                additional_context=run.input_parameters.get("goal_text") if run.input_parameters else None,
            )

            # Assemble the prompt
            logger.info(f"[Run {run_id}] Assembling prompt...")
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
            logger.info(f"[Run {run_id}] Calling OpenAI...")
            try:
                llm_response = await llm_client.generate(
                    system_prompt=assembled_prompt.system_prompt,
                    user_prompt=assembled_prompt.user_prompt,
                    temperature=0.7,
                    max_tokens=4096,
                    json_mode=True,
                )
                await trace_logger.complete_trace(trace.id, llm_response)
                logger.info(f"[Run {run_id}] OpenAI response: {llm_response.total_tokens} tokens")

            except Exception as e:
                await trace_logger.fail_trace(trace.id, str(e))
                raise

            # Update status to parsing
            await run_repo.update_status(run_id, DBRunStatus.PARSING)
            await session.commit()

            # Parse and validate the response
            logger.info(f"[Run {run_id}] Parsing LLM response...")
            parsed_result = await output_service.parse_and_store(
                run_id=run_id,
                llm_response=llm_response,
                total_budget=total_budget,
            )

            if not parsed_result.is_valid:
                await run_repo.update_status(
                    run_id,
                    DBRunStatus.FAILED,
                    error_message="LLM response failed validation"
                )
                await session.commit()
                return

            # Update status to feedback
            await run_repo.update_status(run_id, DBRunStatus.FEEDBACK)
            await session.commit()

            # Generate feedback cards
            logger.info(f"[Run {run_id}] Generating feedback...")
            await feedback_service.generate_and_store(
                run_id=run_id,
                parsed_result=parsed_result,
                run=run,
            )

            # Mark as completed
            run.status = DBRunStatus.COMPLETED.value
            run.completed_at = datetime.now(timezone.utc)
            await session.commit()

            logger.info(f"[Run {run_id}] AI generation completed successfully")

        except Exception as e:
            logger.error(f"AI generation failed for run {run_id}: {str(e)}")
            try:
                run_repo = RunRepository(session)
                await run_repo.update_status(run_id, DBRunStatus.FAILED, error_message=str(e))
                await session.commit()
            except:
                pass


# =============================================================================
# API Endpoints
# =============================================================================

@router.post(
    "",
    response_model=RunResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        201: {"description": "Run created and Stage 1 started"},
        400: {"model": ErrorResponse, "description": "Invalid request"},
        401: {"model": ErrorResponse, "description": "Unauthorized"},
        429: {"model": ErrorResponse, "description": "Rate limit exceeded"},
    },
)
@limiter.limit("20/hour")
async def create_run(
    request: Request,
    run_request: CreateRunRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> RunResponse:
    """Create a new generation run.

    Creates a new budget allocation run and automatically starts Stage 1
    (competitor matching) in the background.

    Multiple runs can be created per session - no session lock.

    Rate limit: 20 generations per user per hour.

    After creation:
    1. Poll GET /runs/{id}/status until status = "awaiting_confirmation"
    2. GET /runs/{id}/competitors to see matched competitors
    3. POST /runs/{id}/competitors/confirm to proceed
    """
    from src.config import get_settings
    settings = get_settings()

    session: SessionContext = await get_session_context(request)
    run_repo = RunRepository(db)

    # Create the run (NO session lock - allow multiple runs)
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

    # Start Stage 1 in background
    background_tasks.add_task(
        run_stage1_background,
        run_id=run.id,
        db_url=settings.database_url,
    )

    logger.info(f"Run {run.id} created, Stage 1 starting in background")

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
    "/start",
    response_model=StartRunResponse,
    responses={
        200: {"description": "Run started successfully"},
        400: {"model": ErrorResponse, "description": "Run cannot be started"},
        404: {"model": ErrorResponse, "description": "Run not found"},
    },
)
async def start_run(
    request: Request,
    start_request: StartRunRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> StartRunResponse:
    """Start processing for an existing run (Manager's Spec v2).

    The run must already exist in DB (created by JS Backend).
    This endpoint triggers Stage 1 (competitor matching) in background.

    This is different from POST /runs which creates AND starts a run.
    Use this endpoint when the JS Backend has already created the run.

    After starting:
    1. Poll GET /runs/{id}/status until status = "awaiting_confirmation"
    2. GET /runs/{id}/competitors to see matched competitors
    3. POST /runs/competitors/confirm to proceed
    """
    from src.config import get_settings
    settings = get_settings()

    run_repo = RunRepository(db)
    run = await run_repo.get(start_request.run_id)

    if not run:
        return StartRunResponse(
            run_id=start_request.run_id,
            status="error",
            error_message=f"Run {start_request.run_id} not found"
        )

    if run.status != DBRunStatus.PENDING.value:
        return StartRunResponse(
            run_id=run.id,
            status="error",
            error_message=f"Run already started (status: {run.status})"
        )

    # Trigger Stage 1 in background
    background_tasks.add_task(
        run_stage1_background,
        run_id=run.id,
        db_url=settings.database_url,
    )

    logger.info(f"Run {run.id} started via /start endpoint, Stage 1 starting in background")

    return StartRunResponse(
        run_id=run.id,
        status="started",
        error_message=None
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

    Use this endpoint to poll for run completion.

    Status progression:
    - pending → matching (Stage 1) → awaiting_confirmation (Stage 1.5)
    - [after confirm] → generating (Stage 2) → parsing (Stage 3) → feedback (Stage 4) → completed
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

    # Map status to stage
    stage_map = {
        DBRunStatus.PENDING.value: None,
        DBRunStatus.MATCHING.value: "S1",
        DBRunStatus.AWAITING_CONFIRMATION.value: "S1.5",
        DBRunStatus.GENERATING.value: "S2",
        DBRunStatus.PARSING.value: "S3",
        DBRunStatus.FEEDBACK.value: "S4",
        DBRunStatus.COMPLETED.value: None,
        DBRunStatus.FAILED.value: None,
        DBRunStatus.CANCELLED.value: None,
    }

    # Generate human-readable progress message
    progress_messages = {
        DBRunStatus.PENDING.value: "Queued for processing",
        DBRunStatus.MATCHING.value: "Finding competitor brands (Stage 1)...",
        DBRunStatus.AWAITING_CONFIRMATION.value: "Waiting for competitor confirmation (Stage 1.5)",
        DBRunStatus.GENERATING.value: "Generating allocation with AI (Stage 2)...",
        DBRunStatus.PARSING.value: "Processing results (Stage 3)...",
        DBRunStatus.FEEDBACK.value: "Generating feedback cards (Stage 4)...",
        DBRunStatus.COMPLETED.value: "Completed",
        DBRunStatus.FAILED.value: "Failed",
        DBRunStatus.CANCELLED.value: "Cancelled",
    }

    # Estimate progress percentage
    progress_pct_map = {
        DBRunStatus.PENDING.value: 0,
        DBRunStatus.MATCHING.value: 20,
        DBRunStatus.AWAITING_CONFIRMATION.value: 30,
        DBRunStatus.GENERATING.value: 50,
        DBRunStatus.PARSING.value: 75,
        DBRunStatus.FEEDBACK.value: 90,
        DBRunStatus.COMPLETED.value: 100,
        DBRunStatus.FAILED.value: 0,
        DBRunStatus.CANCELLED.value: 0,
    }

    return RunStatusResponse(
        id=run.id,
        status=RunStatus(run.status),
        stage=stage_map.get(run.status),
        progress_pct=progress_pct_map.get(run.status, 0),
        started_at=run.started_at,
        completed_at=run.completed_at,
        error_message=run.error_message,
        progress=progress_messages.get(run.status, "Processing..."),
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
        stopped_at=run.completed_at or datetime.now(timezone.utc),
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
