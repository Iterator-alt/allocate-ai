"""Competitor confirmation API endpoint - Prisma-only version.

Endpoints:
- POST /runs/{id}/competitors/confirm - Confirm or dismiss competitor set

NOTE: Frontend reads competitorSnapshot directly from DB and saves
confirmed selection to confirmedCompetitors before calling this endpoint.
"""

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status, BackgroundTasks
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.dependencies import get_db
from src.api.schemas import ErrorResponse, ConfirmCompetitorsRequestV2, ConfirmCompetitorsResponse
from src.db.models.prisma_tables import PrismaProjectVersionAiRun
from src.config import get_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/runs", tags=["competitors"])


async def get_ai_run_by_external_id(db: AsyncSession, external_run_id: int) -> Optional[PrismaProjectVersionAiRun]:
    """Look up ProjectVersionAiRun by externalRunId."""
    query = select(PrismaProjectVersionAiRun).where(
        PrismaProjectVersionAiRun.externalRunId == external_run_id
    )
    result = await db.execute(query)
    return result.scalar_one_or_none()


@router.post(
    "/{run_id}/competitors/confirm",
    response_model=ConfirmCompetitorsResponse,
    responses={
        200: {"description": "Competitors confirmed or run dismissed"},
        400: {"model": ErrorResponse, "description": "Invalid request or run state"},
        404: {"model": ErrorResponse, "description": "Run not found"},
    },
)
async def confirm_competitors(
    request: Request,
    run_id: int,
    confirm_request: ConfirmCompetitorsRequestV2,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """Confirm or dismiss the competitor set for a run.

    After Stage 1 completes, the run waits in 'awaiting_confirmation' status.
    Frontend reads competitorSnapshot from DB, displays to user, and saves
    the confirmed selection to confirmedCompetitors in DB.

    Then frontend calls this endpoint with:
    - action: "approved" - Proceed with Stage 2 using confirmedCompetitors from DB
    - action: "dismissed" - Cancel the run

    NOTE: Frontend must save confirmedCompetitors to DB before calling this endpoint.
    """
    # Validate run_id in body matches URL
    if confirm_request.run_id != run_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"run_id in body ({confirm_request.run_id}) does not match URL ({run_id})",
        )

    ai_run = await get_ai_run_by_external_id(db, run_id)

    if not ai_run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run with externalRunId {run_id} not found",
        )

    # Validate run is in awaiting_confirmation status
    if ai_run.status != "awaiting_confirmation":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Run is not awaiting confirmation. Current status: {ai_run.status}",
        )

    if confirm_request.action == "dismissed":
        # Cancel the run
        ai_run.status = "cancelled"
        ai_run.updatedAt = datetime.utcnow()
        await db.commit()

        logger.info(f"[ExternalRunId {run_id}] Run dismissed by user")

        return ConfirmCompetitorsResponse(
            run_id=run_id,
            status="cancelled",
            confirmed_competitors=None,
            message="Run cancelled",
        )

    # action == "approved"
    # Read confirmedCompetitors from DB (already set by frontend)
    confirmed_brands = ai_run.confirmedCompetitors

    # Validate - frontend must have saved the selection first
    if not confirmed_brands:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No confirmed competitors found. Frontend must save competitor selection to confirmedCompetitors before calling confirm.",
        )

    logger.info(f"[ExternalRunId {run_id}] Competitors confirmed: {confirmed_brands}")

    # Update timestamp
    ai_run.updatedAt = datetime.utcnow()
    await db.commit()

    # Trigger Stage 2-4 pipeline in background
    from src.api.v1.runs import _run_stages_2_to_4_pipeline

    background_tasks.add_task(
        _run_stages_2_to_4_pipeline,
        prisma_ai_run_id=ai_run.id,
        external_run_id=run_id,
        db_url=str(get_settings().database_url),
    )

    return ConfirmCompetitorsResponse(
        run_id=run_id,
        status="approved",
        confirmed_competitors=confirmed_brands,
        message="Competitors confirmed, proceeding to allocation generation",
    )
