"""Competitor matching API endpoints - Prisma-only version.

Endpoints:
- GET /runs/{id}/competitors - Return matched competitors from ProjectVersionAiRun

NOTE: In Prisma-only mode, competitor confirmation is automatic (bypass enabled).
This endpoint is for viewing competitor data only.
"""

from datetime import datetime, timezone
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.dependencies import get_db
from src.api.schemas import ErrorResponse
from src.db.models.prisma_tables import PrismaProjectVersionAiRun

router = APIRouter(prefix="/runs", tags=["competitors"])


async def get_ai_run_by_external_id(db: AsyncSession, external_run_id: int) -> Optional[PrismaProjectVersionAiRun]:
    """Look up ProjectVersionAiRun by externalRunId."""
    query = select(PrismaProjectVersionAiRun).where(
        PrismaProjectVersionAiRun.externalRunId == external_run_id
    )
    result = await db.execute(query)
    return result.scalar_one_or_none()


@router.get(
    "/{run_id}/competitors",
    responses={
        200: {"description": "Competitor data retrieved"},
        404: {"model": ErrorResponse, "description": "Run not found"},
    },
)
async def get_competitors(
    request: Request,
    run_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Get matched competitors for a run.

    The run_id is the externalRunId from ProjectVersionAiRun.
    Returns confirmedCompetitors and competitorSnapshot from ProjectVersionAiRun.
    """
    ai_run = await get_ai_run_by_external_id(db, run_id)

    if not ai_run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run with externalRunId {run_id} not found",
        )

    # Build response from competitorSnapshot
    snapshot = ai_run.competitorSnapshot or {}
    competitors = snapshot.get("competitors", [])
    brand_info = snapshot.get("brand_info")

    return {
        "run_id": run_id,
        "status": ai_run.status,
        "confirmed_competitors": ai_run.confirmedCompetitors or [],
        "total_competitors": len(ai_run.confirmedCompetitors or []),
        "competitors": competitors,
        "brand_info": brand_info,
        "yougov_sectors": snapshot.get("yougov_sectors", []),
        "nielsen_sectors": snapshot.get("nielsen_sectors", []),
    }
