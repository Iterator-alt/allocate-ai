"""Context loader tool for chat agent.

Tool 0: Loads full context on every message including:
- Chat history (last 20 messages for PROJECT - shared across versions)
- Current competitor set from run.confirmed_competitors
- Campaign config from Run model
- Last result summary from AllocationResult
- Pending changes extracted from chat history extra_data
"""

import logging
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any

from sqlalchemy import select, desc, and_
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Run, AllocationResult, ChatHistory
from src.db.models.run import RunStatus

logger = logging.getLogger(__name__)


@dataclass
class ChatContext:
    """Full context for chat agent processing."""

    run_id: int
    project_id: Optional[int]
    run_status: str
    has_results: bool

    # Recent chat messages with their extra_data (shared across project versions)
    recent_messages: List[Dict[str, Any]] = field(default_factory=list)

    # Current competitor set
    current_competitors: List[str] = field(default_factory=list)

    # Campaign configuration
    customer_name: str = ""
    industry: str = ""
    brand_kpi: str = ""
    total_budget: Optional[float] = None
    channels: List[str] = field(default_factory=list)
    goal_text: Optional[str] = None
    direction: Optional[str] = None

    # Version info
    version_name: Optional[str] = None  # e.g., "v1", "v2", "v3"
    version_number: int = 1

    # Last result summary
    last_result_summary: Optional[str] = None

    # Pending changes since last rerun
    pending_changes: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def has_pending_changes(self) -> bool:
        """Quick check for rerun validation."""
        return len(self.pending_changes) > 0


class ContextLoaderTool:
    """Loads context for chat agent processing.

    This tool runs silently on every message to provide
    full context to the agent for decision making.
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    async def load(
        self,
        run_id: int,
        project_id: Optional[int] = None,
    ) -> ChatContext:
        """Load full context for a run.

        Args:
            run_id: The run ID to load context for
            project_id: Optional project ID

        Returns:
            ChatContext with all relevant state
        """
        # Load run
        run = await self._get_run(run_id)
        if not run:
            raise ValueError(f"Run {run_id} not found")

        # Determine project_id
        effective_project_id = project_id or run.project_id

        # Load allocation result if exists
        result = await self._get_allocation_result(run_id)

        # Load recent chat messages - SHARED ACROSS PROJECT VERSIONS
        if effective_project_id:
            messages = await self._get_project_messages(effective_project_id, limit=20)
        else:
            # Fallback to run-specific if no project
            messages = await self._get_recent_messages(run_id, limit=20)

        # Extract pending changes from messages
        pending_changes = self._extract_pending_changes(messages)

        # Extract current competitors from run.confirmed_competitors
        competitors = self._extract_competitors(run.confirmed_competitors)

        # Extract campaign config from run
        channels = []
        goal_text = None
        direction = None
        version_name = None
        version_number = 1
        if run.input_parameters:
            channels = run.input_parameters.get("channels", []) or []
            goal_text = run.input_parameters.get("goal_text")
            direction = run.input_parameters.get("direction")
            version_name = run.input_parameters.get("version_name")
            version_number = run.input_parameters.get("version_number", 1)

        return ChatContext(
            run_id=run_id,
            project_id=effective_project_id,
            run_status=run.status,
            has_results=result is not None,
            recent_messages=messages,
            current_competitors=competitors,
            customer_name=run.customer_name,
            industry=run.industry,
            brand_kpi=run.brand_kpi,
            total_budget=float(run.total_budget) if run.total_budget else None,
            channels=channels,
            goal_text=goal_text,
            direction=direction,
            version_name=version_name,
            version_number=version_number,
            last_result_summary=result.summary if result else None,
            pending_changes=pending_changes,
        )

    async def _get_run(self, run_id: int) -> Optional[Run]:
        """Get run by ID."""
        query = select(Run).where(Run.id == run_id)
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def _get_allocation_result(self, run_id: int) -> Optional[AllocationResult]:
        """Get allocation result for a run."""
        query = select(AllocationResult).where(AllocationResult.run_id == run_id)
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def _get_recent_messages(
        self,
        run_id: int,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """Get recent chat messages with their extra_data for a single run."""
        query = (
            select(ChatHistory)
            .where(ChatHistory.run_id == run_id)
            .order_by(desc(ChatHistory.created_at))
            .limit(limit)
        )
        result = await self.session.execute(query)
        rows = result.scalars().all()

        return self._format_messages(rows)

    async def _get_project_messages(
        self,
        project_id: int,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """Get recent chat messages across ALL runs in a project.

        This enables chat history to be shared across version tabs.
        """
        # Join ChatHistory with Run to filter by project_id
        query = (
            select(ChatHistory)
            .join(Run, ChatHistory.run_id == Run.id)
            .where(Run.project_id == project_id)
            .order_by(desc(ChatHistory.created_at))
            .limit(limit)
        )
        result = await self.session.execute(query)
        rows = result.scalars().all()

        return self._format_messages(rows)

    def _format_messages(self, rows: List[ChatHistory]) -> List[Dict[str, Any]]:
        """Format ChatHistory rows into message dicts."""
        messages = []
        for row in reversed(rows):  # Reverse to get chronological order
            msg = {
                "id": row.id,
                "run_id": row.run_id,  # Include run_id for context
                "message_type": row.message_type,
                "title": row.title,
                "content": row.content,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }

            # Parse extra_data for chat agent fields
            if row.extra_data:
                msg["role"] = row.extra_data.get("role", "system")
                msg["tool_used"] = row.extra_data.get("tool_used")
                msg["changes_made"] = row.extra_data.get("changes_made", [])
            else:
                msg["role"] = "system"
                msg["tool_used"] = None
                msg["changes_made"] = []

            messages.append(msg)

        return messages

    def _extract_pending_changes(
        self,
        messages: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Extract pending changes from message history.

        Changes are considered pending until a "rerun" message is seen.
        When we encounter a rerun message, we reset the pending changes.

        Note: Only SUCCESSFUL reruns should have tool_used="rerun".
        Failed reruns (no changes to apply) should NOT have this marker,
        to avoid incorrectly clearing pending changes.
        """
        pending_changes: List[Dict[str, Any]] = []

        for msg in messages:
            tool_used = msg.get("tool_used")

            # If we see a rerun, clear pending changes
            if tool_used == "rerun":
                pending_changes = []
                continue

            # Accumulate changes from messages
            changes_made = msg.get("changes_made", [])
            for change in changes_made:
                # Only add non-rerun changes
                if change.get("type") != "rerun":
                    pending_changes.append(change)

        return pending_changes

    def _extract_competitors(
        self,
        confirmed_competitors: Optional[Dict[str, Any]],
    ) -> List[str]:
        """Extract competitor list from confirmed_competitors JSON.

        Priority order:
        1. "brands" - set by chat agent updates
        2. "confirmed_brands" - set when user confirms selected competitors
        3. "stage1_result.competitors" - original Stage 1 suggestions (fallback)
        """
        if not confirmed_competitors:
            return []

        # Priority 1: Check for brands list (set by chat agent)
        if "brands" in confirmed_competitors:
            return confirmed_competitors["brands"]

        # Priority 2: Check for confirmed_brands (set at confirmation step)
        if "confirmed_brands" in confirmed_competitors:
            return confirmed_competitors["confirmed_brands"]

        # Priority 3: Fallback to stage1_result.competitors (original suggestions)
        stage1_result = confirmed_competitors.get("stage1_result", {})
        competitors = stage1_result.get("competitors", [])

        return [c.get("brand_label", "") for c in competitors if c.get("brand_label")]
