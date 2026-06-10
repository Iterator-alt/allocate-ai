"""Competitor confirmation API endpoint - Prisma-only version.

Endpoints:
- POST /runs/{id}/competitors/confirm - Confirm or dismiss competitor set
- GET /runs/{id}/competitors/search - Search for a competitor brand

NOTE: Frontend reads competitorSnapshot directly from DB and saves
confirmed selection to confirmedCompetitors before calling this endpoint.
"""

import logging
from datetime import datetime
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status, BackgroundTasks
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from src.dependencies import get_db
from src.api.schemas import ErrorResponse, ConfirmCompetitorsRequestV2, ConfirmCompetitorsResponse
from src.db.models.prisma_tables import PrismaProjectVersionAiRun
from src.db.models.data import YouGov, Nielsen
from src.config import get_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/runs", tags=["competitors"])


# =============================================================================
# Response Schemas
# =============================================================================

class CompetitorSearchResult(BaseModel):
    """A single competitor search result."""
    source: str = Field(..., description="Where found: 'snapshot' or 'database'")
    brand: str = Field(..., description="Canonical brand name")
    has_yougov_data: bool = Field(False, description="Whether YouGov data exists")
    has_nielsen_data: bool = Field(False, description="Whether Nielsen data exists")
    warning: Optional[str] = Field(None, description="Warning message if any")


class CompetitorSearchResponse(BaseModel):
    """Response from competitor search."""
    found: bool = Field(..., description="Whether any brands were found")
    results: List[CompetitorSearchResult] = Field(default_factory=list, description="List of matching brands")
    warning: Optional[str] = Field(None, description="Warning message if not found")


# =============================================================================
# Helper Functions
# =============================================================================

def strip_umlauts(text: str) -> str:
    """Convert German umlauts to ASCII equivalents.

    Handles both single-char (ü→u) and German convention (ü→ue) forms.
    Returns the single-char version for consistency.
    """
    # First normalize ue/oe/ae back to umlauts, then strip
    text = text.replace('ue', 'ü').replace('oe', 'ö').replace('ae', 'ä')
    replacements = {
        'ü': 'u', 'Ü': 'U',
        'ö': 'o', 'Ö': 'O',
        'ä': 'a', 'Ä': 'A',
        'ß': 'ss',
    }
    for umlaut, replacement in replacements.items():
        text = text.replace(umlaut, replacement)
    return text


def _search_in_snapshot(
    query: str,
    competitors: List[dict],
) -> List[dict]:
    """Search for a brand in the competitorSnapshot.

    Uses case-insensitive and umlaut-tolerant matching.
    Returns ALL matching competitor dicts (exact matches first).
    """
    query_lower = query.lower()
    query_ascii = strip_umlauts(query_lower)

    matches = []
    for comp in competitors:
        brand_label = comp.get("yougov_brand_label", "")
        if not brand_label:
            continue

        brand_lower = brand_label.lower()
        brand_ascii = strip_umlauts(brand_lower)

        # Exact match (case-insensitive), ASCII-normalized exact match,
        # or partial match (query contained in brand)
        if (
            brand_lower == query_lower
            or brand_ascii == query_ascii
            or query_lower in brand_lower
            or query_ascii in brand_ascii
        ):
            matches.append(comp)

    # Exact matches first
    matches.sort(key=lambda c: c.get("yougov_brand_label", "").lower() != query_lower)
    return matches


async def _search_yougov(
    db: AsyncSession,
    query: str,
) -> List[str]:
    """Search for a brand in YouGov database.

    Returns ALL matching canonical brand_labels (exact matches first).
    """
    query_lower = query.lower()
    query_ascii = strip_umlauts(query_lower)

    matches: List[str] = []

    # Strategy 1: LIKE match (covers exact and partial, case-insensitive)
    stmt = (
        select(YouGov.brand_label)
        .where(func.lower(YouGov.brand_label).like(f"%{query_lower}%"))
        .distinct()
    )
    result = await db.execute(stmt)
    matches.extend(row[0] for row in result.all() if row[0])

    # Strategy 2: ASCII-normalized match (for umlauts) - add any not already found
    stmt = (
        select(YouGov.brand_label)
        .distinct()
        .limit(500)
    )
    result = await db.execute(stmt)
    for row in result.all():
        candidate = row[0]
        if candidate and candidate not in matches:
            candidate_ascii = strip_umlauts(candidate.lower())
            if candidate_ascii == query_ascii or query_ascii in candidate_ascii:
                matches.append(candidate)

    # Exact matches first, then alphabetical
    matches.sort(key=lambda b: (b.lower() != query_lower, b.lower()))
    return matches


async def _search_nielsen(
    db: AsyncSession,
    query: str,
) -> List[str]:
    """Search for a brand in Nielsen database.

    Returns ALL matching canonical markes (exact matches first).
    """
    query_lower = query.lower()
    query_ascii = strip_umlauts(query_lower)

    matches: List[str] = []

    # Strategy 1: LIKE match (covers exact and partial, case-insensitive)
    stmt = (
        select(Nielsen.marke)
        .where(func.lower(Nielsen.marke).like(f"%{query_lower}%"))
        .distinct()
    )
    result = await db.execute(stmt)
    matches.extend(row[0] for row in result.all() if row[0])

    # Strategy 2: ASCII-normalized match - add any not already found
    stmt = (
        select(Nielsen.marke)
        .distinct()
        .limit(500)
    )
    result = await db.execute(stmt)
    for row in result.all():
        candidate = row[0]
        if candidate and candidate not in matches:
            candidate_ascii = strip_umlauts(candidate.lower())
            if candidate_ascii == query_ascii or query_ascii in candidate_ascii:
                matches.append(candidate)

    # Exact matches first, then alphabetical
    matches.sort(key=lambda b: (b.lower() != query_lower, b.lower()))
    return matches


async def get_ai_run_by_external_id(db: AsyncSession, external_run_id: int) -> Optional[PrismaProjectVersionAiRun]:
    """Look up ProjectVersionAiRun by externalRunId."""
    query = select(PrismaProjectVersionAiRun).where(
        PrismaProjectVersionAiRun.externalRunId == external_run_id
    )
    result = await db.execute(query)
    return result.scalar_one_or_none()


# =============================================================================
# API Endpoints
# =============================================================================

@router.get(
    "/{run_id}/competitors/search",
    response_model=CompetitorSearchResponse,
    responses={
        200: {"description": "Search results returned"},
        404: {"model": ErrorResponse, "description": "Run not found"},
    },
)
async def search_competitor(
    request: Request,
    run_id: int,
    q: str = Query(..., min_length=1, max_length=100, description="Brand name to search"),
    db: AsyncSession = Depends(get_db),
) -> CompetitorSearchResponse:
    """Search for a competitor brand.

    Search order:
    1. Check competitorSnapshot first (already matched in Stage 1)
    2. Search YouGov database
    3. Search Nielsen database

    Returns the best match with availability info.
    """
    # Get the run
    ai_run = await get_ai_run_by_external_id(db, run_id)

    if not ai_run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run with externalRunId {run_id} not found",
        )

    # Step 1: Search in competitorSnapshot first
    snapshot = ai_run.competitorSnapshot or {}
    snapshot_competitors = snapshot.get("competitors", [])

    snapshot_matches = _search_in_snapshot(q, snapshot_competitors)
    if snapshot_matches:
        return CompetitorSearchResponse(
            found=True,
            results=[
                CompetitorSearchResult(
                    source="snapshot",
                    brand=match.get("yougov_brand_label"),
                    has_yougov_data=match.get("has_yougov_data", True),
                    has_nielsen_data=match.get("has_nielsen_data", False),
                    warning=None,
                )
                for match in snapshot_matches
            ],
            warning=None,
        )

    # Step 2: Search YouGov database (all matches)
    yougov_brands = await _search_yougov(db, q)

    # Step 3: Search Nielsen database (all matches)
    nielsen_brands = await _search_nielsen(db, q)

    # Step 4: Return all matches found
    if yougov_brands:
        # YouGov labels are canonical. Nielsen availability is query-level:
        # if the query matched any Nielsen marke, spend data exists for the brand family.
        has_nielsen = bool(nielsen_brands)
        return CompetitorSearchResponse(
            found=True,
            results=[
                CompetitorSearchResult(
                    source="database",
                    brand=brand,
                    has_yougov_data=True,
                    has_nielsen_data=has_nielsen,
                    warning=None if has_nielsen else "No Nielsen spend data available for this brand.",
                )
                for brand in yougov_brands
            ],
            warning=None,
        )
    elif nielsen_brands:
        # Nielsen only (rare case)
        return CompetitorSearchResponse(
            found=True,
            results=[
                CompetitorSearchResult(
                    source="database",
                    brand=brand,
                    has_yougov_data=False,
                    has_nielsen_data=True,
                    warning="No YouGov perception data available for this brand.",
                )
                for brand in nielsen_brands
            ],
            warning=None,
        )
    else:
        # Not found anywhere
        return CompetitorSearchResponse(
            found=False,
            results=[],
            warning="Brand not found in our database.",
        )


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

    # Validate competitorSnapshot exists - if empty, Stage 1 failed
    snapshot = ai_run.competitorSnapshot or {}
    snapshot_competitors = snapshot.get("competitors", [])
    if not snapshot_competitors:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Stage 1 failed: No competitors found. Please rerun with different brand/industry.",
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
    )

    return ConfirmCompetitorsResponse(
        run_id=run_id,
        status="approved",
        confirmed_competitors=confirmed_brands,
        message="Competitors confirmed, proceeding to allocation generation",
    )
