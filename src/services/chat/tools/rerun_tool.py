"""Rerun tool for chat agent.

Tool 3: Trigger reruns with change validation.
- Only allows rerun if pending changes exist
- Handles STATE A (no results) and STATE B (results exist) differently
- Resets ProjectVersionAiRun status and triggers pipeline

PRISMA-ONLY MODE: Uses PrismaProjectVersionAiRun instead of Python Run table.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Dict, Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import BackgroundTasks

from src.db.models.prisma_tables import PrismaProjectVersionAiRun
from src.config import get_settings
from src.services.chat.tools.context_loader import ChatContext

logger = logging.getLogger(__name__)


@dataclass
class RerunResult:
    """Result of a rerun operation."""

    success: bool
    message: str
    rerun_triggered: bool = False
    blocked_reason: Optional[str] = None
    new_run_id: Optional[int] = None
    new_version_name: Optional[str] = None
    changes_applied: Optional[List[Dict[str, Any]]] = None


class RerunTool:
    """Handles rerun requests with change validation.

    CRITICAL: Rerun only allowed if changes exist.

    STATE A (no results exist):
    - Response: "Your inputs look good — hit Generate to produce the first allocation."
    - Never trigger rerun automatically

    STATE B (results exist + has_pending_changes=True):
    - Trigger on explicit phrases: "rerun", "regenerate", "apply changes", "run again", "redo"
    - Shows summary of changes being applied
    - Resets ProjectVersionAiRun status and triggers pipeline

    STATE B (results exist + has_pending_changes=False):
    - Response: "No changes to apply. The current allocation is up to date."

    PRISMA-ONLY MODE: Uses PrismaProjectVersionAiRun instead of Run table.
    """

    def __init__(
        self,
        session: AsyncSession,
        background_tasks: Optional[BackgroundTasks] = None,
    ):
        self.session = session
        self.background_tasks = background_tasks
        self.settings = get_settings()

    async def execute(
        self,
        run_id: int,
        context: ChatContext,
    ) -> RerunResult:
        """Execute a rerun request.

        Args:
            run_id: externalRunId from ProjectVersionAiRun
            context: Current chat context with pending changes

        Returns:
            RerunResult with outcome
        """
        # GUARD: Check if any changes were made since last run
        if not context.has_pending_changes:
            if context.has_results:
                return RerunResult(
                    success=False,
                    message="No changes to apply. The current allocation is up to date. Make some changes first (add/remove competitors, edit inputs) before rerunning.",
                    rerun_triggered=False,
                    blocked_reason="no_pending_changes",
                )
            else:
                return RerunResult(
                    success=False,
                    message="Your inputs look good — hit Generate to produce the first allocation.",
                    rerun_triggered=False,
                    blocked_reason="no_results_yet",
                )

        # STATE B with pending changes - proceed with rerun
        changes_summary = self._build_changes_summary(context.pending_changes)

        # Reset the ProjectVersionAiRun and trigger pipeline
        await self._reset_and_trigger_pipeline(run_id, context.ai_run_id)

        return RerunResult(
            success=True,
            message=f"Applying changes: {changes_summary}\n\n[Rerun] Running allocation with updated inputs...",
            rerun_triggered=True,
            new_run_id=run_id,  # Same run_id, just reset
            new_version_name=context.version_name,
            changes_applied=context.pending_changes,
        )

    def _build_changes_summary(self, pending_changes: List[Dict[str, Any]]) -> str:
        """Build a human-readable summary of pending changes."""
        if not pending_changes:
            return "No changes"

        summaries = []
        for change in pending_changes:
            change_type = change.get("type", "unknown")

            if change_type == "competitor_add":
                brand = change.get("brand", "unknown")
                summaries.append(f"{brand} added")
            elif change_type == "competitor_remove":
                brand = change.get("brand", "unknown")
                summaries.append(f"{brand} removed")
            elif change_type == "edit":
                field = change.get("field", "unknown")
                new_val = change.get("new")
                if field == "total_budget":
                    summaries.append(f"budget updated to EUR{new_val:,.0f}" if isinstance(new_val, (int, float)) else f"budget updated")
                elif field == "channels":
                    action = change.get("action", "updated")
                    value = change.get("value", "")
                    if action == "add":
                        summaries.append(f"'{value}' channel added")
                    elif action == "remove":
                        summaries.append(f"'{value}' channel removed")
                    else:
                        summaries.append("channels updated")
                else:
                    summaries.append(f"{field} updated to '{new_val}'")

        return ", ".join(summaries)

    async def _reset_and_trigger_pipeline(
        self,
        run_id: int,
        ai_run_id: str,
    ) -> None:
        """Reset ProjectVersionAiRun status and trigger the pipeline.

        Args:
            run_id: externalRunId
            ai_run_id: Internal Prisma ID for ProjectVersionAiRun
        """
        # Get the AI run
        query = select(PrismaProjectVersionAiRun).where(
            PrismaProjectVersionAiRun.externalRunId == run_id
        )
        result = await self.session.execute(query)
        ai_run = result.scalar_one_or_none()

        if not ai_run:
            raise ValueError(f"ProjectVersionAiRun with externalRunId {run_id} not found")

        # Reset status to pending
        ai_run.status = "pending"
        ai_run.stage = None
        ai_run.progressPct = 0
        ai_run.errorMessage = None
        ai_run.startedAt = None
        ai_run.completedAt = None
        ai_run.updatedAt = datetime.utcnow()

        # Clear previous allocation result (keep competitors and chat)
        ai_run.allocationResult = None

        await self.session.flush()

        logger.info(f"Reset ProjectVersionAiRun {ai_run_id} (externalRunId={run_id}) to pending status")

        # Trigger background processing
        if self.background_tasks:
            await self._trigger_background_processing(run_id, ai_run_id)

    async def _trigger_background_processing(
        self,
        run_id: int,
        ai_run_id: str,
    ) -> None:
        """Trigger background processing for the run.

        Calls run_full_pipeline_background from runs.py.
        """
        if not self.background_tasks:
            logger.warning(f"No background_tasks available for run {run_id}")
            return

        # Import here to avoid circular imports
        from src.api.v1.runs import run_full_pipeline_background

        self.background_tasks.add_task(
            run_full_pipeline_background,
            external_run_id=run_id,
            prisma_ai_run_id=ai_run_id,
            db_url=self.settings.database_url,
        )
        logger.info(f"Triggered background processing for externalRunId={run_id}")
