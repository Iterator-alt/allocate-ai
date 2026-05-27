"""Prompt trace API endpoints - Placeholder for Prisma-only mode.

Traces require Python tables which are not available in Prisma-only mode.
"""

import logging
from fastapi import APIRouter, HTTPException, status

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/traces", tags=["traces"])


@router.get("/{run_id}/trace")
async def get_traces(run_id: int):
    """Traces endpoint - not available in Prisma-only mode."""
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Traces are not available in Prisma-only mode.",
    )
