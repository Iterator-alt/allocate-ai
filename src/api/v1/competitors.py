"""Competitor matching API endpoints.

Endpoints:
- GET /runs/{id}/competitors - Return matched competitors (Stage 1)
- POST /runs/{id}/competitors/confirm - Approve/Cancel competitor set (Stage 1.5)
"""

from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.dependencies import get_db
from src.api.schemas import (
    CompetitorBrand,
    CompetitorSetResponse,
    ConfirmCompetitorsRequest,
    ConfirmCompetitorsResponse,
    ErrorResponse,
)
from src.api.middleware import get_session_context, SessionContext
from src.repositories import RunRepository
from src.db.models.run import RunStatus
from src.services.mediamix import CompetitorSetAssemblyService
from src.services.guards import DataFeasibilityGuard

router = APIRouter(prefix="/runs", tags=["competitors"])


@router.get(
    "/{run_id}/competitors",
    response_model=CompetitorSetResponse,
    responses={
        200: {"description": "Competitor set returned"},
        400: {"model": ErrorResponse, "description": "Feasibility check failed"},
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
    """Get matched competitors for a run (Stage 1).

    This endpoint triggers the competitor matching pipeline:
    1. Look up industry → sector_label mapping
    2. Query YouGov for brands in the sector
    3. Resolve each brand to Nielsen data via brand_map
    4. Return combined competitor set with data coverage info

    The run status will transition to 'matching' then 'awaiting_confirmation'.
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

    # Check if competitors already confirmed
    if run.confirmed_competitors:
        # Return the already confirmed set
        confirmed_brands = run.confirmed_competitors.get("brands", [])
        return CompetitorSetResponse(
            run_id=run.id,
            industry=run.industry,
            sector_label=run.confirmed_competitors.get("sector_label", ""),
            competitors=[
                CompetitorBrand(
                    nielsen_brand=brand,
                    wirtschaftsgruppe=run.industry,
                    has_nielsen_data=True,
                    has_yougov_data=True,
                )
                for brand in confirmed_brands
            ],
            total_competitors=len(confirmed_brands),
            competitors_with_full_data=len(confirmed_brands),
            warnings=["Competitors already confirmed for this run"],
        )

    # Update status to matching
    await run_repo.update_status(run_id, RunStatus.MATCHING)

    # Run feasibility check (Guard #2)
    feasibility_guard = DataFeasibilityGuard(db)
    feasibility_result = await feasibility_guard.check_feasibility(
        industry=run.industry,
        brand_kpi=run.brand_kpi,
    )

    if not feasibility_result.is_feasible:
        # Return error with suggestions
        blocking_issues = feasibility_result.blocking_issues
        error_messages = [issue.message for issue in blocking_issues]
        suggestions = []
        for issue in blocking_issues:
            suggestions.extend(issue.suggestions)

        await run_repo.update_status(
            run_id,
            RunStatus.FAILED,
            error_message="; ".join(error_messages),
        )
        await db.commit()

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "Feasibility check failed",
                "issues": error_messages,
                "suggestions": feasibility_result.suggested_alternatives,
            },
        )

    # Build competitor set
    competitor_service = CompetitorSetAssemblyService(db)
    result = await competitor_service.get_competitor_brands_for_run(
        wirtschaftsgruppe=run.industry,
        brand_kpi=run.brand_kpi,
        customer_name=run.customer_name,
    )

    if not result.is_feasible:
        await run_repo.update_status(
            run_id,
            RunStatus.FAILED,
            error_message=result.error_message,
        )
        await db.commit()

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": result.error_message,
                "suggestions": result.suggestions,
            },
        )

    # Update status to awaiting confirmation
    await run_repo.update_status(run_id, RunStatus.AWAITING_CONFIRMATION)
    await db.commit()

    # Build response
    competitors = [
        CompetitorBrand(
            nielsen_brand=c.nielsen_brand,
            yougov_brand_label=c.yougov_brand_label,
            wirtschaftsgruppe=c.wirtschaftsgruppe,
            has_nielsen_data=c.has_nielsen_data,
            has_yougov_data=c.has_yougov_data,
            total_spend_eur=c.total_spend_eur,
            match_confidence=c.match_confidence,
        )
        for c in result.competitors
    ]

    # Combine warnings from feasibility check and competitor matching
    all_warnings = list(feasibility_result.warnings) + result.warnings

    return CompetitorSetResponse(
        run_id=run.id,
        industry=result.industry,
        sector_label=result.sector_label,
        competitors=competitors,
        total_competitors=result.total_competitors,
        competitors_with_full_data=result.competitors_with_full_data,
        warnings=all_warnings,
    )


@router.post(
    "/{run_id}/competitors/confirm",
    response_model=ConfirmCompetitorsResponse,
    responses={
        200: {"description": "Competitor set confirmed/cancelled"},
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
    db: AsyncSession = Depends(get_db),
) -> ConfirmCompetitorsResponse:
    """Confirm or cancel the competitor set (Stage 1.5).

    Actions:
    - approve: Lock the competitor set and proceed to data filtering (Stage 2)
    - cancel: Cancel the run

    If selected_competitors is provided with approve, only those brands
    will be used (allows user to filter the suggested set).
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
        # Get the competitor set (need to rebuild or cache it)
        # For MVP, we rebuild - in production, consider caching
        competitor_service = CompetitorSetAssemblyService(db)
        result = await competitor_service.get_competitor_brands_for_run(
            wirtschaftsgruppe=run.industry,
            brand_kpi=run.brand_kpi,
            customer_name=run.customer_name,
        )

        # Determine which competitors to use
        if confirm_request.selected_competitors:
            # User selected specific competitors
            available = {c.nielsen_brand for c in result.competitors}
            selected = set(confirm_request.selected_competitors)

            # Validate all selected are in available set
            invalid = selected - available
            if invalid:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Selected competitors not in matched set: {list(invalid)}",
                )

            confirmed = list(selected)
        else:
            # Use all matched competitors
            confirmed = [c.nielsen_brand for c in result.competitors]

        if not confirmed:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No competitors to confirm",
            )

        # Store confirmed competitors and update status
        run.confirmed_competitors = {
            "brands": confirmed,
            "sector_label": result.sector_label,
            "total_matched": result.total_competitors,
            "confirmed_count": len(confirmed),
        }
        run.status = RunStatus.GENERATING.value
        await db.commit()

        return ConfirmCompetitorsResponse(
            run_id=run.id,
            status="approved",
            confirmed_competitors=confirmed,
            message=f"Confirmed {len(confirmed)} competitors. Proceeding to generation.",
        )

    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid action: {action}. Must be 'approve' or 'cancel'.",
        )


@router.get(
    "/{run_id}/feasibility",
    responses={
        200: {"description": "Feasibility check result"},
        401: {"model": ErrorResponse, "description": "Unauthorized"},
        403: {"model": ErrorResponse, "description": "Forbidden"},
        404: {"model": ErrorResponse, "description": "Run not found"},
    },
)
async def check_feasibility(
    request: Request,
    run_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Check data feasibility for a run (Guard #2).

    Returns information about data availability and any issues
    that might affect the allocation quality.
    """
    session: SessionContext = await get_session_context(request)
    run_repo = RunRepository(db)

    run = await run_repo.get(run_id)
    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run {run_id} not found",
        )

    if run.session_token != session.session_token and not session.is_owner:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )

    # Get channels from input parameters
    channels = None
    if run.input_parameters:
        channels = run.input_parameters.get("channels")

    feasibility_guard = DataFeasibilityGuard(db)
    result = await feasibility_guard.check_feasibility(
        industry=run.industry,
        brand_kpi=run.brand_kpi,
        channels=channels,
    )

    return {
        "run_id": run.id,
        "is_feasible": result.is_feasible,
        "issues": [
            {
                "field": issue.field,
                "value": issue.value,
                "type": issue.issue_type,
                "message": issue.message,
                "suggestions": issue.suggestions,
                "is_blocking": issue.is_blocking,
            }
            for issue in result.issues
        ],
        "warnings": result.warnings,
        "suggested_alternatives": result.suggested_alternatives,
    }
