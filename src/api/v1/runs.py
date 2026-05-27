"""Run management API endpoints - Prisma-only version.

Endpoints:
- POST /runs - Start a new generation run (accepts {run_id, action: "start"})
- GET /runs/{id}/status - Poll run state from ProjectVersionAiRun
- GET /runs/{id}/result - Get allocation result from ProjectVersionAiRun

Flow:
1. POST /runs with {run_id, action: "start"} where run_id = externalRunId from ProjectVersionAiRun
2. Python reads inputs from ProjectVersion, runs Stage 1-4 pipeline
3. Results are stored in ProjectVersionAiRun.allocationResult
4. Poll status until "completed", then GET /result

NO PYTHON TABLES REQUIRED - all state is stored in Prisma tables.
"""

import logging
import re
import json
from datetime import datetime
from typing import Optional, List
from decimal import Decimal
from dataclasses import dataclass

from fastapi import APIRouter, Depends, HTTPException, Request, status, BackgroundTasks
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.dependencies import get_db
from src.api.schemas import (
    RunStatusResponse,
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
from src.db.models.prisma_tables import PrismaProjectVersion, PrismaProjectVersionAiRun

# Stage 1 imports
from src.services.stage1 import (
    Stage1Orchestrator,
    UserCampaignInput,
    Stage1Result,
    Stage1Status,
)

# Stage 2-4 AI Pipeline imports
from src.services.llm_gateway.client import OpenAIClient
from src.services.mediamix.prompt_assembly import PromptAssemblyService, PromptAssemblyInput

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/runs", tags=["runs"])


# =============================================================================
# Data Classes for In-Memory State
# =============================================================================

@dataclass
class CampaignInputs:
    """Campaign inputs extracted from ProjectVersion."""
    customer_name: str
    industry: str
    brand_kpi: str
    media_channels: List[str]
    goal_mode: str
    goal_text: str
    total_budget: Optional[float]
    direction: str


# =============================================================================
# Helper Functions
# =============================================================================

def extract_budget_from_goal_text(goal_text: str) -> Optional[float]:
    """Extract budget amount from goal_text using regex.

    Supports formats like:
    - "€2M budget" -> 2000000
    - "2M EUR" -> 2000000
    - "€500K" -> 500000
    - "500000 euros" -> 500000
    - "budget of 1,000,000" -> 1000000
    """
    if not goal_text:
        return None

    # Normalize text
    text = goal_text.lower().replace(",", "").replace(" ", "")

    # Pattern 1: €2M, €500K, 2M€, etc.
    pattern_m = r'[€$]?(\d+(?:\.\d+)?)\s*m(?:illion)?|(\d+(?:\.\d+)?)\s*m(?:illion)?\s*[€$]?'
    match = re.search(pattern_m, text, re.IGNORECASE)
    if match:
        value = float(match.group(1) or match.group(2))
        return value * 1_000_000

    # Pattern 2: €500K, 500K€, etc.
    pattern_k = r'[€$]?(\d+(?:\.\d+)?)\s*k|(\d+(?:\.\d+)?)\s*k\s*[€$]?'
    match = re.search(pattern_k, text, re.IGNORECASE)
    if match:
        value = float(match.group(1) or match.group(2))
        return value * 1_000

    # Pattern 3: Plain numbers with currency indicators
    pattern_plain = r'[€$]?\s*(\d{5,})\s*(?:eur(?:o)?s?)?|budget\s*(?:of)?\s*[€$]?\s*(\d{5,})'
    match = re.search(pattern_plain, text, re.IGNORECASE)
    if match:
        value = match.group(1) or match.group(2)
        return float(value)

    return None


def map_goal_mode_to_direction(goal_mode: str) -> str:
    """Map Prisma goalMode to our direction format."""
    return "increase" if goal_mode == "goal" else "budget_to_impact"


async def get_ai_run_by_external_id(db: AsyncSession, external_run_id: int) -> Optional[PrismaProjectVersionAiRun]:
    """Look up ProjectVersionAiRun by externalRunId."""
    query = select(PrismaProjectVersionAiRun).where(
        PrismaProjectVersionAiRun.externalRunId == external_run_id
    )
    result = await db.execute(query)
    return result.scalar_one_or_none()


async def get_project_version(db: AsyncSession, project_version_id: str) -> Optional[PrismaProjectVersion]:
    """Look up ProjectVersion by ID."""
    query = select(PrismaProjectVersion).where(
        PrismaProjectVersion.id == project_version_id
    )
    result = await db.execute(query)
    return result.scalar_one_or_none()


def extract_campaign_inputs(project_version: PrismaProjectVersion) -> CampaignInputs:
    """Extract campaign inputs from ProjectVersion."""
    total_budget = extract_budget_from_goal_text(project_version.goalText)
    direction = map_goal_mode_to_direction(project_version.goalMode)

    return CampaignInputs(
        customer_name=project_version.customer,
        industry=project_version.industry,
        brand_kpi=project_version.brandKpi,
        media_channels=list(project_version.mediaChannels) if project_version.mediaChannels else [],
        goal_mode=project_version.goalMode,
        goal_text=project_version.goalText,
        total_budget=total_budget,
        direction=direction,
    )


# =============================================================================
# Background Task: Full Pipeline Processing
# =============================================================================

async def run_full_pipeline_background(
    external_run_id: int,
    prisma_ai_run_id: str,
    db_url: str,
):
    """Background task to run the full Stage 1-4 pipeline.

    This runs asynchronously after POST /runs returns.
    All state is stored in ProjectVersionAiRun.
    """
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    from sqlalchemy.orm import sessionmaker

    engine = create_async_engine(db_url)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as session:
        try:
            # Get the AI run record
            query = select(PrismaProjectVersionAiRun).where(
                PrismaProjectVersionAiRun.id == prisma_ai_run_id
            )
            result = await session.execute(query)
            ai_run = result.scalar_one_or_none()

            if not ai_run:
                logger.error(f"ProjectVersionAiRun {prisma_ai_run_id} not found")
                return

            # Get ProjectVersion for inputs
            project_version = await get_project_version(session, ai_run.projectVersionId)
            if not project_version:
                logger.error(f"ProjectVersion {ai_run.projectVersionId} not found")
                await _update_ai_run_status(session, ai_run, "failed", error="ProjectVersion not found")
                return

            # Extract campaign inputs
            inputs = extract_campaign_inputs(project_version)
            logger.info(f"[ExternalRunId {external_run_id}] Starting pipeline for {inputs.customer_name}")

            # Update status to matching (Stage 1)
            await _update_ai_run_status(session, ai_run, "matching", stage="S1", progress_pct=10)

            # =================================================================
            # Stage 1: Competitor Matching
            # =================================================================
            orchestrator = Stage1Orchestrator(session=session)

            user_input = UserCampaignInput(
                brand_name=inputs.customer_name,
                industry=inputs.industry,
                brand_kpi=inputs.brand_kpi,
                media_channels=inputs.media_channels,
                goal_direction=inputs.direction,
            )

            stage1_result = await orchestrator.process(user_input, run_id=str(external_run_id))

            if stage1_result.status == Stage1Status.FAILED:
                error_msg = "; ".join(stage1_result.errors) if stage1_result.errors else "Stage 1 failed"
                await _update_ai_run_status(session, ai_run, "failed", error=error_msg)
                logger.error(f"[ExternalRunId {external_run_id}] Stage 1 failed: {error_msg}")
                return

            # Store competitor data
            confirmed_names = [c.brand_label for c in stage1_result.competitors]
            competitor_snapshot = _build_competitor_snapshot(stage1_result, inputs.industry)

            ai_run.confirmedCompetitors = confirmed_names
            ai_run.competitorSnapshot = competitor_snapshot
            await session.commit()

            logger.info(f"[ExternalRunId {external_run_id}] Stage 1 completed, {len(confirmed_names)} competitors found")

            # =================================================================
            # Stage 2: AI Allocation Generation
            # =================================================================
            await _update_ai_run_status(session, ai_run, "generating", stage="S2", progress_pct=40)

            llm_client = OpenAIClient()
            prompt_service = PromptAssemblyService(session)

            # Build prompt
            total_budget = Decimal(str(inputs.total_budget)) if inputs.total_budget else None

            prompt_input = PromptAssemblyInput(
                customer_name=inputs.customer_name,
                industry=inputs.industry,
                brand_kpi=inputs.brand_kpi,
                total_budget=total_budget,
                time_period_start=None,
                time_period_end=None,
                channels=inputs.media_channels,
                nielsen_brands=[inputs.customer_name] + confirmed_names[:5],
                yougov_brands=[inputs.customer_name] + confirmed_names[:5],
                additional_context=inputs.goal_text,
            )

            assembled_prompt = await prompt_service.assemble_prompt(
                input_params=prompt_input,
                wirtschaftsgruppe=inputs.industry,
            )

            logger.info(f"[ExternalRunId {external_run_id}] Calling OpenAI...")

            llm_response = await llm_client.generate(
                system_prompt=assembled_prompt.system_prompt,
                user_prompt=assembled_prompt.user_prompt,
                temperature=0.7,
                max_tokens=4096,
                json_mode=True,
            )

            logger.info(f"[ExternalRunId {external_run_id}] OpenAI response: {llm_response.total_tokens} tokens")

            # =================================================================
            # Stage 3: Parse Response
            # =================================================================
            await _update_ai_run_status(session, ai_run, "parsing", stage="S3", progress_pct=70)

            # Parse LLM response
            try:
                parsed_allocation = json.loads(llm_response.content)
            except json.JSONDecodeError as e:
                await _update_ai_run_status(session, ai_run, "failed", error=f"Failed to parse LLM response: {e}")
                return

            # Build allocation result
            allocations = []
            channels_data = parsed_allocation.get("channels", parsed_allocation.get("allocations", []))

            # Try to extract total budget from LLM response if not in inputs
            total_budget = inputs.total_budget
            if not total_budget:
                # Check if LLM returned a total budget
                llm_total = parsed_allocation.get("totalBudgetEur", parsed_allocation.get("total_budget_eur"))
                if llm_total:
                    total_budget = float(llm_total)

            for channel in channels_data:
                # Get share percentage - handle various field names
                share_pct = float(
                    channel.get("percentage") or
                    channel.get("share_pct") or
                    channel.get("sharePct") or
                    0
                )

                # Get budget amount - handle various field names from LLM
                budget_value = (
                    channel.get("amount") or
                    channel.get("budget") or
                    channel.get("budgetGrossEur") or  # camelCase from LLM
                    channel.get("budget_gross_eur") or  # snake_case
                    None
                )

                # If no explicit budget but we have total_budget and share_pct, calculate it
                if budget_value:
                    budget_gross_eur = float(budget_value)
                elif total_budget and share_pct > 0:
                    budget_gross_eur = round(total_budget * share_pct / 100, 2)
                else:
                    budget_gross_eur = None

                allocations.append({
                    "channel": channel.get("name", channel.get("channel", "Unknown")),
                    "share_pct": share_pct,
                    "budget_gross_eur": budget_gross_eur,
                    "reasoning": channel.get("rationale", channel.get("reasoning", "")),
                })

            # =================================================================
            # Stage 4: Store Results
            # =================================================================
            await _update_ai_run_status(session, ai_run, "completing", stage="S4", progress_pct=90)

            # Calculate total budget from allocations if not provided
            if not total_budget:
                budget_sum = sum(a["budget_gross_eur"] or 0 for a in allocations)
                if budget_sum > 0:
                    total_budget = budget_sum

            allocation_result = {
                "run_id": external_run_id,
                "allocations": allocations,
                "total_budget_eur": total_budget,
                "kpi_projection": None,
                "reasoning_summary": parsed_allocation.get("summary", parsed_allocation.get("reasoning_summary", "")),
                "confidence_score": parsed_allocation.get("confidence", parsed_allocation.get("confidence_score", 0.85)),
                "warnings": [],
                "is_cached": False,
                "created_at": datetime.utcnow().isoformat(),
                "updated_at": datetime.utcnow().isoformat(),
            }

            # Store in ProjectVersionAiRun
            ai_run.allocationResult = allocation_result
            ai_run.status = "completed"
            ai_run.completedAt = datetime.utcnow()
            ai_run.updatedAt = datetime.utcnow()
            ai_run.progressPct = 100
            ai_run.stage = None
            await session.commit()

            logger.info(f"[ExternalRunId {external_run_id}] Pipeline completed successfully")

        except Exception as e:
            logger.error(f"[ExternalRunId {external_run_id}] Pipeline failed: {str(e)}", exc_info=True)
            try:
                await session.rollback()
                # Try to update status to failed
                query = select(PrismaProjectVersionAiRun).where(
                    PrismaProjectVersionAiRun.id == prisma_ai_run_id
                )
                result = await session.execute(query)
                ai_run = result.scalar_one_or_none()
                if ai_run:
                    await _update_ai_run_status(session, ai_run, "failed", error=str(e))
            except Exception as e2:
                logger.error(f"Failed to update status after error: {e2}")


async def _update_ai_run_status(
    session: AsyncSession,
    ai_run: PrismaProjectVersionAiRun,
    status: str,
    stage: Optional[str] = None,
    progress_pct: Optional[int] = None,
    error: Optional[str] = None,
):
    """Update ProjectVersionAiRun status fields."""
    ai_run.status = status
    ai_run.updatedAt = datetime.utcnow()

    if stage is not None:
        ai_run.stage = stage
    if progress_pct is not None:
        ai_run.progressPct = progress_pct
    if error is not None:
        ai_run.errorMessage = error
    if status == "matching":
        ai_run.startedAt = datetime.utcnow()

    await session.commit()


def _build_competitor_snapshot(result: Stage1Result, industry: str) -> dict:
    """Build the competitor snapshot JSON for ProjectVersionAiRun.competitorSnapshot."""
    competitors = []
    for c in result.competitors:
        competitors.append({
            "nielsen_brand": c.nielsen_brand,
            "yougov_brand_label": c.brand_label,
            "wirtschaftsgruppe": industry,
            "has_nielsen_data": c.nielsen_brand is not None,
            "has_yougov_data": True,
            "total_spend_eur": c.total_spend_teuro * 1000 if c.total_spend_teuro else None,
            "match_confidence": 1.0,
            "avg_kpi_score": c.avg_kpi_score,
        })

    return {
        "competitors": competitors,
        "brand_info": {
            "brand_label": result.confirmed_brand.yougov_brand if result.confirmed_brand else None,
            "nielsen_brand": result.confirmed_brand.nielsen_brand if result.confirmed_brand else None,
            "match_type": result.confirmed_brand.match_type.value if result.confirmed_brand else None,
            "confidence": result.confirmed_brand.confidence if result.confirmed_brand else None,
        } if result.confirmed_brand else None,
        "yougov_sectors": result.yougov_sectors,
        "nielsen_sectors": result.nielsen_sectors,
    }


# =============================================================================
# API Endpoints
# =============================================================================

@router.post(
    "",
    response_model=StartRunResponse,
    status_code=status.HTTP_200_OK,
    responses={
        200: {"description": "Run started successfully"},
        400: {"model": ErrorResponse, "description": "Invalid request"},
        404: {"model": ErrorResponse, "description": "ProjectVersionAiRun not found"},
        429: {"model": ErrorResponse, "description": "Rate limit exceeded"},
    },
)
@limiter.limit("20/hour")
async def create_run(
    request: Request,
    run_request: StartRunRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> StartRunResponse:
    """Start a new generation run.

    Request body: {run_id, action: "start"}

    The run_id is the externalRunId from ProjectVersionAiRun (set by JS Backend).

    This endpoint:
    1. Looks up ProjectVersionAiRun by externalRunId
    2. Gets ProjectVersion and extracts campaign inputs
    3. Starts the full Stage 1-4 pipeline in background
    4. Stores results in ProjectVersionAiRun.allocationResult

    After starting:
    1. Poll GET /runs/{run_id}/status until "completed"
    2. GET /runs/{run_id}/result for allocation
    """
    from src.config import get_settings
    settings = get_settings()

    external_run_id = run_request.run_id

    # Look up ProjectVersionAiRun by externalRunId
    ai_run = await get_ai_run_by_external_id(db, external_run_id)

    if not ai_run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"ProjectVersionAiRun with externalRunId {external_run_id} not found",
        )

    # Get ProjectVersion for campaign inputs
    project_version = await get_project_version(db, ai_run.projectVersionId)

    if not project_version:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"ProjectVersion {ai_run.projectVersionId} not found",
        )

    # Update ProjectVersionAiRun to pending status
    ai_run.status = "pending"
    ai_run.progressPct = 0
    ai_run.stage = None
    ai_run.errorMessage = None
    ai_run.updatedAt = datetime.utcnow()
    await db.commit()

    # Start full pipeline in background
    background_tasks.add_task(
        run_full_pipeline_background,
        external_run_id=external_run_id,
        prisma_ai_run_id=ai_run.id,
        db_url=settings.database_url,
    )

    logger.info(f"Run started for externalRunId={external_run_id}, ProjectVersionAiRun={ai_run.id}")

    return StartRunResponse(
        run_id=external_run_id,
        status="started",
        error_message=None
    )


@router.get(
    "/{run_id}/status",
    response_model=RunStatusResponse,
    responses={
        200: {"description": "Run status retrieved"},
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
    The run_id is the externalRunId from ProjectVersionAiRun.

    Status progression:
    - pending → matching (S1) → generating (S2) → parsing (S3) → completing (S4) → completed
    - Any stage can transition to failed
    """
    ai_run = await get_ai_run_by_external_id(db, run_id)

    if not ai_run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run with externalRunId {run_id} not found",
        )

    # Map status to our RunStatus enum
    status_map = {
        "pending": RunStatus.PENDING,
        "matching": RunStatus.MATCHING,
        "generating": RunStatus.GENERATING,
        "parsing": RunStatus.PARSING,
        "completing": RunStatus.FEEDBACK,
        "completed": RunStatus.COMPLETED,
        "failed": RunStatus.FAILED,
        "cancelled": RunStatus.CANCELLED,
    }

    # Generate human-readable progress message
    progress_messages = {
        "pending": "Queued for processing",
        "matching": "Finding competitor brands (Stage 1)...",
        "generating": "Generating allocation with AI (Stage 2)...",
        "parsing": "Processing results (Stage 3)...",
        "completing": "Finalizing results (Stage 4)...",
        "completed": "Completed",
        "failed": "Failed",
        "cancelled": "Cancelled",
    }

    return RunStatusResponse(
        id=run_id,
        status=status_map.get(ai_run.status, RunStatus.PENDING),
        stage=ai_run.stage,
        progress_pct=ai_run.progressPct or 0,
        started_at=ai_run.startedAt,
        completed_at=ai_run.completedAt,
        error_message=ai_run.errorMessage,
        progress=progress_messages.get(ai_run.status, "Processing..."),
    )


@router.get(
    "/{run_id}/result",
    responses={
        200: {"description": "Allocation result retrieved"},
        404: {"model": ErrorResponse, "description": "Run not found"},
        400: {"model": ErrorResponse, "description": "Run not completed"},
    },
)
async def get_run_result(
    request: Request,
    run_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Get the allocation result for a completed run.

    The run_id is the externalRunId from ProjectVersionAiRun.
    Returns the allocationResult JSON stored in ProjectVersionAiRun.
    """
    ai_run = await get_ai_run_by_external_id(db, run_id)

    if not ai_run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run with externalRunId {run_id} not found",
        )

    if ai_run.status != "completed":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Run is not completed. Current status: {ai_run.status}",
        )

    if not ai_run.allocationResult:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No allocation result found for this run",
        )

    return ai_run.allocationResult


@router.get(
    "/{run_id}/competitors",
    responses={
        200: {"description": "Competitor data retrieved"},
        404: {"model": ErrorResponse, "description": "Run not found"},
    },
)
async def get_run_competitors(
    request: Request,
    run_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Get the competitor data for a run.

    The run_id is the externalRunId from ProjectVersionAiRun.
    Returns confirmedCompetitors and competitorSnapshot from ProjectVersionAiRun.
    """
    ai_run = await get_ai_run_by_external_id(db, run_id)

    if not ai_run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run with externalRunId {run_id} not found",
        )

    return {
        "run_id": run_id,
        "confirmed_competitors": ai_run.confirmedCompetitors or [],
        "competitor_snapshot": ai_run.competitorSnapshot,
    }
