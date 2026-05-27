"""Run repository for CRUD operations on runs table."""

import hashlib
import json
from datetime import datetime
from typing import List, Optional, Dict, Any

from sqlalchemy import select, and_, or_, func, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Run, AllocationResult, ChatHistory
from src.db.models.run import RunStatus
from src.repositories.base import BaseRepository


class RunRepository(BaseRepository[Run]):
    """Repository for run management."""

    def __init__(self, session: AsyncSession):
        super().__init__(session, Run)

    async def create_run(
        self,
        session_token: str,
        customer_name: str,
        industry: str,
        brand_kpi: str,
        user_id: Optional[int] = None,
        project_id: Optional[int] = None,
        project_version_id: Optional[int] = None,
        total_budget: Optional[float] = None,
        time_period_start: Optional[datetime] = None,
        time_period_end: Optional[datetime] = None,
        input_parameters: Optional[Dict[str, Any]] = None,
    ) -> Run:
        """Create a new generation run.

        Args:
            session_token: Session identifier from JS Backend
            customer_name: Customer/brand name
            industry: Industry classification (Wirtschaftsgruppe)
            brand_kpi: KPI to optimize (adaware, aided, consider)
            user_id: Optional user ID
            project_id: Optional project ID
            project_version_id: Optional project version ID
            total_budget: Optional total budget in EUR
            time_period_start: Optional analysis period start
            time_period_end: Optional analysis period end
            input_parameters: Optional additional parameters

        Returns:
            Created Run instance
        """
        # Generate input hash for caching
        input_hash = self._compute_input_hash(
            customer_name=customer_name,
            industry=industry,
            brand_kpi=brand_kpi,
            total_budget=total_budget,
            time_period_start=time_period_start,
            time_period_end=time_period_end,
            input_parameters=input_parameters,
        )

        run = Run(
            session_token=session_token,
            user_id=user_id,
            project_id=project_id,
            project_version_id=project_version_id,
            customer_name=customer_name,
            industry=industry,
            brand_kpi=brand_kpi,
            total_budget=total_budget,
            time_period_start=time_period_start,
            time_period_end=time_period_end,
            input_parameters=input_parameters,
            status=RunStatus.PENDING.value,
            input_hash=input_hash,
        )

        self.session.add(run)
        await self.session.flush()
        return run

    async def get_by_session(
        self,
        session_token: str,
        limit: int = 10,
    ) -> List[Run]:
        """Get runs for a session, ordered by most recent."""
        query = (
            select(Run)
            .where(Run.session_token == session_token)
            .order_by(Run.created_at.desc())
            .limit(limit)
        )
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def get_active_run_for_session(
        self, session_token: str
    ) -> Optional[Run]:
        """Get the active (non-terminal) run for a session.

        Used to enforce one active run per session.
        """
        active_statuses = [
            RunStatus.PENDING.value,
            RunStatus.MATCHING.value,
            RunStatus.AWAITING_CONFIRMATION.value,
            RunStatus.GENERATING.value,
            RunStatus.PARSING.value,
            RunStatus.FEEDBACK.value,
        ]

        query = select(Run).where(
            and_(
                Run.session_token == session_token,
                Run.status.in_(active_statuses),
            )
        )
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def update_status(
        self,
        run_id: int,
        status: RunStatus,
        error_message: Optional[str] = None,
    ) -> Optional[Run]:
        """Update run status.

        Args:
            run_id: Run ID
            status: New status
            error_message: Optional error message (for failed status)

        Returns:
            Updated Run or None if not found
        """
        run = await self.get(run_id)
        if not run:
            return None

        run.status = status.value

        # Set timestamps based on status
        now = datetime.utcnow()
        if status == RunStatus.MATCHING and not run.started_at:
            run.started_at = now
        elif status in (RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED):
            run.completed_at = now

        if error_message:
            run.error_message = error_message

        await self.session.flush()
        return run

    async def mark_cancelled(
        self,
        run_id: int,
        reason: Optional[str] = None,
    ) -> Optional[Run]:
        """Mark a run as cancelled."""
        return await self.update_status(
            run_id,
            RunStatus.CANCELLED,
            error_message=reason or "Cancelled by user",
        )

    async def set_confirmed_competitors(
        self,
        run_id: int,
        competitors: List[str],
    ) -> Optional[Run]:
        """Store the confirmed competitor set for a run."""
        run = await self.get(run_id)
        if not run:
            return None

        run.confirmed_competitors = {"brands": competitors}
        run.status = RunStatus.GENERATING.value
        await self.session.flush()
        return run

    async def find_cached_result(
        self,
        input_hash: str,
    ) -> Optional[Run]:
        """Find a completed run with matching input hash.

        Used for Guard #3: Change-Aware Regeneration.
        """
        query = (
            select(Run)
            .where(
                and_(
                    Run.input_hash == input_hash,
                    Run.status == RunStatus.COMPLETED.value,
                )
            )
            .order_by(Run.completed_at.desc())
            .limit(1)
        )
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def get_runs_by_user(
        self,
        user_id: int,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Run]:
        """Get runs for a specific user."""
        query = (
            select(Run)
            .where(Run.user_id == user_id)
            .order_by(Run.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def count_recent_generations(
        self,
        session_token: str,
        hours: int = 1,
    ) -> int:
        """Count generation runs in the last N hours.

        Used for rate limiting enforcement.
        """
        from datetime import timedelta

        cutoff = datetime.utcnow() - timedelta(hours=hours)

        query = select(func.count()).select_from(Run).where(
            and_(
                Run.session_token == session_token,
                Run.created_at >= cutoff,
            )
        )
        result = await self.session.execute(query)
        return result.scalar_one()

    def _compute_input_hash(
        self,
        customer_name: str,
        industry: str,
        brand_kpi: str,
        total_budget: Optional[float] = None,
        time_period_start: Optional[datetime] = None,
        time_period_end: Optional[datetime] = None,
        input_parameters: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Compute a hash of run inputs for caching.

        Used by Guard #3 to detect unchanged inputs.
        """
        input_data = {
            "customer_name": customer_name,
            "industry": industry,
            "brand_kpi": brand_kpi,
            "total_budget": str(total_budget) if total_budget else None,
            "time_period_start": time_period_start.isoformat() if time_period_start else None,
            "time_period_end": time_period_end.isoformat() if time_period_end else None,
            "input_parameters": input_parameters,
        }

        # Create stable JSON representation
        json_str = json.dumps(input_data, sort_keys=True)
        return hashlib.sha256(json_str.encode()).hexdigest()


class AllocationResultRepository(BaseRepository[AllocationResult]):
    """Repository for allocation results."""

    def __init__(self, session: AsyncSession):
        super().__init__(session, AllocationResult)

    async def get_by_run_id(self, run_id: int) -> Optional[AllocationResult]:
        """Get allocation result for a run."""
        query = select(AllocationResult).where(AllocationResult.run_id == run_id)
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def create_result(
        self,
        run_id: int,
        allocations: Dict[str, Any],
        summary: Optional[str] = None,
        confidence_score: Optional[float] = None,
        raw_response: Optional[str] = None,
        is_valid: bool = True,
        validation_errors: Optional[Dict[str, Any]] = None,
    ) -> AllocationResult:
        """Create an allocation result."""
        result = AllocationResult(
            run_id=run_id,
            allocations=allocations,
            summary=summary,
            confidence_score=confidence_score,
            raw_response=raw_response,
            is_valid=is_valid,
            validation_errors=validation_errors,
        )
        self.session.add(result)
        await self.session.flush()
        return result


class ChatHistoryRepository(BaseRepository[ChatHistory]):
    """Repository for chat history / feedback cards."""

    def __init__(self, session: AsyncSession):
        super().__init__(session, ChatHistory)

    async def get_by_run_id(
        self,
        run_id: int,
        message_type: Optional[str] = None,
    ) -> List[ChatHistory]:
        """Get chat messages for a run."""
        query = select(ChatHistory).where(ChatHistory.run_id == run_id)

        if message_type:
            query = query.where(ChatHistory.message_type == message_type)

        query = query.order_by(ChatHistory.display_order)
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def create_message(
        self,
        run_id: int,
        message_type: str,
        severity: str,
        title: str,
        content: str,
        extra_data: Optional[Dict[str, Any]] = None,
        display_order: int = 0,
    ) -> ChatHistory:
        """Create a chat message / feedback card."""
        message = ChatHistory(
            run_id=run_id,
            message_type=message_type,
            severity=severity,
            title=title,
            content=content,
            extra_data=extra_data,
            display_order=display_order,
        )
        self.session.add(message)
        await self.session.flush()
        return message

    async def add_warning(
        self,
        run_id: int,
        title: str,
        content: str,
        extra_data: Optional[Dict[str, Any]] = None,
    ) -> ChatHistory:
        """Add a warning message (yellow)."""
        count = await self._get_message_count(run_id)
        return await self.create_message(
            run_id=run_id,
            message_type="warning",
            severity="warning",
            title=title,
            content=content,
            extra_data=extra_data,
            display_order=count,
        )

    async def add_alert(
        self,
        run_id: int,
        title: str,
        content: str,
        extra_data: Optional[Dict[str, Any]] = None,
    ) -> ChatHistory:
        """Add an alert message (red)."""
        count = await self._get_message_count(run_id)
        return await self.create_message(
            run_id=run_id,
            message_type="alert",
            severity="error",
            title=title,
            content=content,
            extra_data=extra_data,
            display_order=count,
        )

    async def add_summary(
        self,
        run_id: int,
        title: str,
        content: str,
        extra_data: Optional[Dict[str, Any]] = None,
    ) -> ChatHistory:
        """Add a summary message."""
        count = await self._get_message_count(run_id)
        return await self.create_message(
            run_id=run_id,
            message_type="summary",
            severity="info",
            title=title,
            content=content,
            extra_data=extra_data,
            display_order=count,
        )

    async def _get_message_count(self, run_id: int) -> int:
        """Get current message count for ordering."""
        query = select(func.count()).select_from(ChatHistory).where(
            ChatHistory.run_id == run_id
        )
        result = await self.session.execute(query)
        return result.scalar_one()

    async def get_chat_messages(
        self,
        run_id: int,
        limit: int = 20,
        include_system: bool = True,
    ) -> List[ChatHistory]:
        """Get recent chat messages for a run.

        Used by chat agent to load conversation context.

        Args:
            run_id: Run ID to get messages for
            limit: Maximum number of messages to return
            include_system: Whether to include system messages

        Returns:
            List of ChatHistory messages, most recent first
        """
        query = select(ChatHistory).where(ChatHistory.run_id == run_id)

        if not include_system:
            # Only include chat messages (not system feedback cards)
            query = query.where(ChatHistory.message_type == "chat")

        query = query.order_by(ChatHistory.created_at.desc()).limit(limit)

        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def add_chat_message(
        self,
        run_id: int,
        role: str,
        content: str,
        tool_used: Optional[str] = None,
        changes_made: Optional[List[Dict[str, Any]]] = None,
    ) -> ChatHistory:
        """Add a chat message with agent-specific extra_data.

        Args:
            run_id: Run ID
            role: "user", "agent", or "system"
            content: Message content
            tool_used: Tool that was used (if any)
            changes_made: List of changes made by this message

        Returns:
            Created ChatHistory instance
        """
        count = await self._get_message_count(run_id)

        extra_data = {
            "role": role,
            "tool_used": tool_used,
            "changes_made": changes_made or [],
        }

        title = "User" if role == "user" else "Agent" if role == "agent" else "System"

        return await self.create_message(
            run_id=run_id,
            message_type="chat",
            severity="info",
            title=title,
            content=content,
            extra_data=extra_data,
            display_order=count,
        )

    async def get_pending_changes(
        self,
        run_id: int,
    ) -> List[Dict[str, Any]]:
        """Get pending changes since the last rerun.

        Scans chat history for changes_made in extra_data,
        resets when a "rerun" tool is seen.

        Returns:
            List of pending change records
        """
        messages = await self.get_chat_messages(run_id, limit=50)

        # Process in chronological order (oldest first)
        messages.reverse()

        pending_changes: List[Dict[str, Any]] = []

        for msg in messages:
            if not msg.extra_data:
                continue

            tool_used = msg.extra_data.get("tool_used")

            # Reset on rerun
            if tool_used == "rerun":
                pending_changes = []
                continue

            # Accumulate changes
            changes_made = msg.extra_data.get("changes_made", [])
            for change in changes_made:
                if change.get("type") != "rerun":
                    pending_changes.append(change)

        return pending_changes
