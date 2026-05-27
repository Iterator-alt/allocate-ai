"""Allocation result API endpoints - Prisma-only version.

This module is kept for backward compatibility but the main result endpoint
is now in runs.py as GET /runs/{id}/result.

The result is read from ProjectVersionAiRun.allocationResult (Prisma table).
"""

from fastapi import APIRouter

router = APIRouter(prefix="/results", tags=["results"])

# Results are now served from runs.py GET /runs/{id}/result
# This router is kept empty for compatibility
