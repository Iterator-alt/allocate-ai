"""Rerun tool for chat agent.

Tool 3: Trigger reruns with change validation.
- Only allows rerun if pending changes exist
- Handles STATE A (no results) and STATE B (results exist) differently
- Creates new run with incremented version (v1, v2, v3...)
"""

import logging
from dataclasses import dataclass
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone

from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import BackgroundTasks

from src.db.models import Run
from src.db.models.run import RunStatus
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
    new_version_name: Optional[str] = None  # e.g., "v2", "v3"
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
    - Either creates new run or resets existing run based on config

    STATE B (results exist + has_pending_changes=False):
    - Response: "No changes to apply. The current allocation is up to date."
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
            run_id: Run to rerun
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

        # Check config flag for rerun behavior
        create_new_run = getattr(self.settings, 'chat_rerun_creates_new', True)

        if create_new_run:
            # Create a new run with updated inputs and incremented version
            new_run, version_name = await self._create_new_run(run_id, context)
            new_run_id = new_run.id

            # Start processing in background if we have background_tasks
            if self.background_tasks:
                await self._trigger_background_processing(new_run_id)

            return RerunResult(
                success=True,
                message=f"Applying changes: {changes_summary}\n\n⟳ Creating {version_name} with updated inputs...",
                rerun_triggered=True,
                new_run_id=new_run_id,
                new_version_name=version_name,
                changes_applied=context.pending_changes,
            )
        else:
            # Reset existing run to PENDING
            await self._reset_run(run_id)

            # Start processing in background
            if self.background_tasks:
                await self._trigger_background_processing(run_id)

            return RerunResult(
                success=True,
                message=f"Applying changes: {changes_summary}\n\n⟳ Running allocation with updated inputs...",
                rerun_triggered=True,
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
                    summaries.append(f"budget updated to €{new_val:,.0f}" if isinstance(new_val, (int, float)) else f"budget updated")
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

    async def _create_new_run(
        self,
        source_run_id: int,
        context: ChatContext,
    ) -> tuple[Run, str]:
        """Create a new run with updated inputs from the source run.

        Returns:
            Tuple of (new_run, version_name) e.g., (Run, "v2")
        """
        # Get the source run
        query = select(Run).where(Run.id == source_run_id)
        result = await self.session.execute(query)
        source_run = result.scalar_one_or_none()

        if not source_run:
            raise ValueError(f"Source run {source_run_id} not found")

        # Determine next version number for this project
        next_version = await self._get_next_version_number(source_run.project_id)
        version_name = f"v{next_version}"

        # Copy and update input_parameters with version info
        input_params = source_run.input_parameters.copy() if source_run.input_parameters else {}
        input_params["version_name"] = version_name
        input_params["version_number"] = next_version
        input_params["source_run_id"] = source_run_id  # Track lineage

        # Create new run with same base parameters
        new_run = Run(
            session_token=source_run.session_token,
            user_id=source_run.user_id,
            project_id=source_run.project_id,
            project_version_id=source_run.project_version_id,
            customer_name=source_run.customer_name,
            industry=source_run.industry,
            brand_kpi=source_run.brand_kpi,
            total_budget=source_run.total_budget,
            time_period_start=source_run.time_period_start,
            time_period_end=source_run.time_period_end,
            input_parameters=input_params,
            status=RunStatus.PENDING.value,
            confirmed_competitors=source_run.confirmed_competitors.copy() if source_run.confirmed_competitors else {},
        )

        self.session.add(new_run)
        await self.session.flush()

        logger.info(f"Created new run {new_run.id} ({version_name}) from source run {source_run_id}")
        return new_run, version_name

    async def _get_next_version_number(
        self,
        project_id: Optional[int],
    ) -> int:
        """Get the next version number for a project.

        Finds the highest version_number in input_parameters for runs
        in this project and returns +1.
        """
        if not project_id:
            return 1

        # Get all runs for this project
        query = select(Run).where(Run.project_id == project_id)
        result = await self.session.execute(query)
        runs = result.scalars().all()

        max_version = 0
        for run in runs:
            if run.input_parameters:
                version = run.input_parameters.get("version_number", 0)
                if isinstance(version, int) and version > max_version:
                    max_version = version

        # If no versions found, start at 1 (existing run becomes v1 implicitly)
        # New run will be v2
        return max(max_version + 1, 2) if max_version > 0 else 2

    async def _reset_run(self, run_id: int) -> None:
        """Reset a run to PENDING status for reprocessing."""
        query = select(Run).where(Run.id == run_id)
        result = await self.session.execute(query)
        run = result.scalar_one_or_none()

        if not run:
            raise ValueError(f"Run {run_id} not found")

        run.status = RunStatus.PENDING.value
        run.started_at = None
        run.completed_at = None
        run.error_message = None

        await self.session.flush()
        logger.info(f"Reset run {run_id} to PENDING status")

    async def _trigger_background_processing(self, run_id: int) -> None:
        """Trigger background processing for a run.

        This imports and calls the background task from runs.py
        """
        if not self.background_tasks:
            logger.warning(f"No background_tasks available for run {run_id}")
            return

        # Import here to avoid circular imports
        from src.api.v1.runs import run_stage1_background

        self.background_tasks.add_task(
            run_stage1_background,
            run_id=run_id,
            db_url=self.settings.database_url,
        )
        logger.info(f"Triggered background processing for run {run_id}")
