"""Competitor matching API endpoints.

Endpoints:
- GET /runs/{id}/competitors - Return matched competitors from Stage 1
- POST /runs/{id}/competitors/confirm - Approve/Cancel and auto-trigger AI generation
"""

from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Request, status, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession

from src.dependencies import get_db
from src.api.schemas import (
    CompetitorBrand,
    BrandInfo,
    CompetitorSetResponse,
    ConfirmCompetitorsRequest,
    ConfirmCompetitorsResponse,
    ConfirmCompetitorsRequestV2,
    ErrorResponse,
)
from src.api.middleware import get_session_context, SessionContext
from src.repositories import RunRepository
from src.db.models.run import RunStatus

# Import background task from runs
from src.api.v1.runs import run_ai_generation_background

router = APIRouter(prefix="/runs", tags=["competitors"])


@router.get(
    "/{run_id}/competitors",
    response_model=CompetitorSetResponse,
    responses={
        200: {"description": "Competitor set returned"},
        400: {"model": ErrorResponse, "description": "Stage 1 not complete"},
        401: {"model": ErrorResponse, "description": "Unauthorized"},
        403: {"model": ErrorResponse, "description": "Forbidden"},
        404: {"model": ErrorResponse, "description": "Run not found"},
    },
)
async def get_competitors(
    request: Request,
    run_id: int,
    db: AsyncSession = Depends(get_db),
) -> CompetitorSetResponse:
    """Get matched competitors for a run.

    Returns the competitor set resolved by Stage 1 (AI-powered resolution).
    Available once status reaches "awaiting_confirmation".

    The competitors list includes:
    - brand_label: YouGov brand name
    - nielsen_brand: Nielsen brand name (may differ)
    - avg_kpi_score: Average KPI score
    - total_spend_teuro: Total advertising spend in thousands EUR
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

    # Verify ownership
    if run.session_token != session.session_token and not session.is_owner:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )

    # Check if Stage 1 is complete
    if run.status == RunStatus.PENDING.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Run is still pending. Stage 1 has not started yet.",
        )

    if run.status == RunStatus.MATCHING.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Stage 1 is still processing. Please poll /status and wait for 'awaiting_confirmation'.",
        )

    if run.status == RunStatus.FAILED.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Run failed: {run.error_message}",
        )

    # Get Stage 1 results from stored data
    stage1_data = run.confirmed_competitors or {}
    stage1_result = stage1_data.get("stage1_result", {})

    if not stage1_result:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Stage 1 results not available. Current status: " + run.status,
        )

    # Extract competitor list
    competitors_data = stage1_result.get("competitors", [])
    confirmed_brand = stage1_result.get("confirmed_brand", {})
    brand_data = stage1_result.get("brand_data", {})

    # Build response
    competitors = [
        CompetitorBrand(
            nielsen_brand=c.get("nielsen_brand") or c.get("brand_label"),
            yougov_brand_label=c.get("brand_label"),
            wirtschaftsgruppe=run.industry,
            has_nielsen_data=c.get("nielsen_brand") is not None,
            has_yougov_data=True,
            total_spend_eur=c.get("total_spend_teuro", 0) * 1000 if c.get("total_spend_teuro") else None,
            match_confidence=1.0,
        )
        for c in competitors_data
    ]

    # Determine sector label
    yougov_sectors = stage1_result.get("yougov_sectors", [])
    sector_label = yougov_sectors[0] if yougov_sectors else run.industry

    # Build warnings
    warnings = []
    if not confirmed_brand:
        warnings.append("Target brand not found in database - using proxy match")
    if len(competitors) < 3:
        warnings.append(f"Only {len(competitors)} competitors found - limited data for comparison")

    # Build brand_info using schema
    brand_info_obj = None
    if confirmed_brand:
        brand_info_obj = BrandInfo(
            brand_label=confirmed_brand.get("yougov_brand"),
            nielsen_brand=confirmed_brand.get("nielsen_brand"),
            match_type=confirmed_brand.get("match_type"),
            confidence=confirmed_brand.get("confidence"),
            kpi_scores={
                "adaware": brand_data.get("adaware_score"),
                "aware": brand_data.get("aware_score"),
                "consider": brand_data.get("consider_score"),
            } if brand_data else None,
            total_spend_teuro=brand_data.get("total_spend_teuro") if brand_data else None,
        )

    return CompetitorSetResponse(
        run_id=run.id,
        industry=run.industry,
        sector_label=sector_label,
        competitors=competitors,
        total_competitors=len(competitors),
        competitors_with_full_data=sum(1 for c in competitors if c.has_nielsen_data and c.has_yougov_data),
        warnings=warnings,
        brand_info=brand_info_obj,
    )


@router.post(
    "/{run_id}/competitors/confirm",
    response_model=ConfirmCompetitorsResponse,
    responses={
        200: {"description": "Competitor set confirmed, AI generation started"},
        400: {"model": ErrorResponse, "description": "Invalid action or state"},
        401: {"model": ErrorResponse, "description": "Unauthorized"},
        403: {"model": ErrorResponse, "description": "Forbidden"},
        404: {"model": ErrorResponse, "description": "Run not found"},
    },
)
async def confirm_competitors(
    request: Request,
    run_id: int,
    confirm_request: ConfirmCompetitorsRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> ConfirmCompetitorsResponse:
    """Confirm or cancel the competitor set (Stage 1.5).

    Actions:
    - approve: Lock competitors and AUTO-START AI generation (Stage 2-4)
    - cancel: Cancel the run

    If selected_competitors is provided with approve, only those brands
    will be used (allows user to filter/modify the suggested set).

    After approval:
    - AI generation starts automatically in background
    - Poll GET /runs/{id}/status until "completed"
    - Then GET /runs/{id}/result for allocation
    """
    from src.config import get_settings
    settings = get_settings()

    session: SessionContext = await get_session_context(request)
    run_repo = RunRepository(db)

    # Get the run
    run = await run_repo.get(run_id)
    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run {run_id} not found",
        )

    # Verify ownership
    if run.session_token != session.session_token and not session.is_owner:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )

    # Verify run is in correct state
    if run.status != RunStatus.AWAITING_CONFIRMATION.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Run is not awaiting confirmation (current status: {run.status})",
        )

    action = confirm_request.action.lower()

    if action == "cancel":
        # Cancel the run
        reason = confirm_request.reason or "Competitors not approved by user"
        await run_repo.mark_cancelled(run_id, reason)
        await db.commit()

        return ConfirmCompetitorsResponse(
            run_id=run.id,
            status="cancelled",
            confirmed_competitors=None,
            message="Run cancelled",
        )

    elif action == "approve":
        # Get current Stage 1 data
        stage1_data = run.confirmed_competitors or {}
        stage1_result = stage1_data.get("stage1_result", {})
        competitors_data = stage1_result.get("competitors", [])

        # Get available competitor brands
        available_brands = [c.get("brand_label") for c in competitors_data]

        # Determine which competitors to use
        if confirm_request.selected_competitors:
            # User selected specific competitors
            selected = set(confirm_request.selected_competitors)
            available_set = set(available_brands)

            # Validate all selected are in available set
            invalid = selected - available_set
            if invalid:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Selected competitors not in matched set: {list(invalid)}. Available: {available_brands}",
                )

            confirmed = list(selected)
        else:
            # Use all matched competitors
            confirmed = available_brands

        if not confirmed:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No competitors to confirm. Stage 1 may have failed to find competitors.",
            )

        # Update Stage 1 data with confirmation
        stage1_data["pending_confirmation"] = False
        stage1_data["confirmed_brands"] = confirmed
        stage1_data["confirmed_count"] = len(confirmed)
        run.confirmed_competitors = stage1_data

        # Update status to generating
        run.status = RunStatus.GENERATING.value
        await db.commit()

        # AUTO-START AI generation in background
        background_tasks.add_task(
            run_ai_generation_background,
            run_id=run.id,
            db_url=settings.database_url,
        )

        return ConfirmCompetitorsResponse(
            run_id=run.id,
            status="approved",
            confirmed_competitors=confirmed,
            message=f"Confirmed {len(confirmed)} competitors. AI generation started automatically. Poll /status for progress.",
        )

    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid action: {action}. Must be 'approve' or 'cancel'.",
        )


@router.post(
    "/competitors/confirm",
    response_model=ConfirmCompetitorsResponse,
    responses={
        200: {"description": "Competitor set confirmed, AI generation started"},
        400: {"model": ErrorResponse, "description": "Invalid action or state"},
        401: {"model": ErrorResponse, "description": "Unauthorized"},
        403: {"model": ErrorResponse, "description": "Forbidden"},
        404: {"model": ErrorResponse, "description": "Run not found"},
    },
)
async def confirm_competitors_v2(
    request: Request,
    confirm_request: ConfirmCompetitorsRequestV2,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> ConfirmCompetitorsResponse:
    """Confirm or cancel the competitor set (Manager's Spec v2 - run_id in body).

    This is an alternative endpoint where run_id is in the request body
    instead of the URL path.

    Actions:
    - approve/approved: Lock competitors and AUTO-START AI generation (Stage 2-4)
    - cancel: Cancel the run

    Note: selected_competitors is NOT passed in request - uses all confirmed
    competitors from Stage 1 stored in DB.

    After approval:
    - AI generation starts automatically in background
    - Poll GET /runs/{id}/status until "completed"
    - Then GET /runs/{id}/result for allocation
    """
    from src.config import get_settings
    settings = get_settings()

    session: SessionContext = await get_session_context(request)
    run_repo = RunRepository(db)

    run_id = confirm_request.run_id

    # Get the run
    run = await run_repo.get(run_id)
    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run {run_id} not found",
        )

    # Verify ownership
    if run.session_token != session.session_token and not session.is_owner:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )

    # Verify run is in correct state
    if run.status != RunStatus.AWAITING_CONFIRMATION.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Run is not awaiting confirmation (current status: {run.status})",
        )

    # Normalize action - accept both "approve" and "approved"
    action = confirm_request.action.lower()
    if action == "approved":
        action = "approve"

    if action == "cancel":
        # Cancel the run
        reason = confirm_request.reason or "Competitors not approved by user"
        await run_repo.mark_cancelled(run_id, reason)
        await db.commit()

        return ConfirmCompetitorsResponse(
            run_id=run.id,
            status="cancelled",
            confirmed_competitors=None,
            message="Run cancelled",
        )

    elif action == "approve":
        # Get current Stage 1 data
        stage1_data = run.confirmed_competitors or {}
        stage1_result = stage1_data.get("stage1_result", {})
        competitors_data = stage1_result.get("competitors", [])

        # Get all competitor brands from Stage 1 (not from request)
        confirmed = [c.get("brand_label") for c in competitors_data]

        if not confirmed:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No competitors to confirm. Stage 1 may have failed to find competitors.",
            )

        # Update Stage 1 data with confirmation
        stage1_data["pending_confirmation"] = False
        stage1_data["confirmed_brands"] = confirmed
        stage1_data["confirmed_count"] = len(confirmed)
        run.confirmed_competitors = stage1_data

        # Update status to generating
        run.status = RunStatus.GENERATING.value
        await db.commit()

        # AUTO-START AI generation in background
        background_tasks.add_task(
            run_ai_generation_background,
            run_id=run.id,
            db_url=settings.database_url,
        )

        return ConfirmCompetitorsResponse(
            run_id=run.id,
            status="approved",
            confirmed_competitors=confirmed,
            message=f"Confirmed {len(confirmed)} competitors. AI generation started automatically. Poll /status for progress.",
        )

    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid action: {action}. Must be 'approve', 'approved', or 'cancel'.",
        )
