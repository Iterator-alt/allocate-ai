"""Context loader tool for chat agent.

Tool 0: Loads full context on every message including:
- Chat history (last 20 messages from chatSnapshot)
- Current competitor set from ProjectVersionAiRun.confirmedCompetitors
- Campaign config from ProjectVersion
- Last result summary from ProjectVersionAiRun.allocationResult
- Pending changes extracted from chat history extra_data

PRISMA-ONLY MODE: Uses PrismaProjectVersionAiRun and PrismaProjectVersion
instead of Python Run/AllocationResult/ChatHistory tables.
"""

import logging
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.prisma_tables import PrismaProjectVersionAiRun, PrismaProjectVersion

logger = logging.getLogger(__name__)


@dataclass
class ChatContext:
    """Full context for chat agent processing."""

    run_id: int  # This is externalRunId
    project_id: Optional[str]  # ProjectVersion.projectId (string in Prisma)
    ai_run_id: str  # PrismaProjectVersionAiRun.id (internal Prisma ID)
    run_status: str
    has_results: bool

    # Recent chat messages with their extra_data
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
    version_name: Optional[str] = None
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

    PRISMA-ONLY MODE: Uses PrismaProjectVersionAiRun and PrismaProjectVersion.
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    async def load(
        self,
        run_id: int,
        project_id: Optional[str] = None,
    ) -> ChatContext:
        """Load full context for a run.

        Args:
            run_id: The externalRunId from ProjectVersionAiRun
            project_id: Optional project ID (not used in Prisma mode)

        Returns:
            ChatContext with all relevant state
        """
        # Load AI run by externalRunId
        ai_run = await self._get_ai_run(run_id)
        if not ai_run:
            raise ValueError(f"ProjectVersionAiRun with externalRunId {run_id} not found")

        # Load ProjectVersion for campaign inputs
        project_version = await self._get_project_version(ai_run.projectVersionId)
        if not project_version:
            raise ValueError(f"ProjectVersion {ai_run.projectVersionId} not found")

        # Check if results exist
        has_results = ai_run.allocationResult is not None and ai_run.status == "completed"

        # Get result summary if available
        result_summary = None
        if ai_run.allocationResult:
            result_summary = ai_run.allocationResult.get("reasoning_summary")

        # Load chat messages from chatSnapshot
        messages = self._get_messages_from_snapshot(ai_run.chatSnapshot)

        # Extract pending changes from messages
        pending_changes = self._extract_pending_changes(messages)

        # Extract current competitors from confirmedCompetitors array
        competitors = list(ai_run.confirmedCompetitors) if ai_run.confirmedCompetitors else []

        # Check rawPayload for any edits made via chat
        raw_payload = ai_run.rawPayload or {}
        chat_edits = raw_payload.get("chat_edits", {})

        # Get channels from ProjectVersion or chat edits
        channels = list(project_version.mediaChannels) if project_version.mediaChannels else []
        if "channels" in chat_edits:
            channels = chat_edits["channels"]

        # Get total_budget from chat edits or extract from goalText
        total_budget = chat_edits.get("total_budget")
        if total_budget is None:
            total_budget = self._extract_budget_from_goal_text(project_version.goalText)

        # Get direction from chat edits or derive from goalMode
        direction = chat_edits.get("direction")
        if direction is None:
            direction = "increase" if project_version.goalMode == "goal" else "budget_to_impact"

        # Get brand_kpi from chat edits or ProjectVersion
        brand_kpi = chat_edits.get("brand_kpi", project_version.brandKpi)

        # Get goal_text from chat edits or ProjectVersion
        goal_text = chat_edits.get("goal_text", project_version.goalText)

        return ChatContext(
            run_id=run_id,
            project_id=project_version.projectId,
            ai_run_id=ai_run.id,
            run_status=ai_run.status,
            has_results=has_results,
            recent_messages=messages,
            current_competitors=competitors,
            customer_name=project_version.customer,
            industry=project_version.industry,
            brand_kpi=brand_kpi,
            total_budget=total_budget,
            channels=channels,
            goal_text=goal_text,
            direction=direction,
            version_name=project_version.versionName,
            version_number=project_version.versionNumber,
            last_result_summary=result_summary,
            pending_changes=pending_changes,
        )

    async def _get_ai_run(self, external_run_id: int) -> Optional[PrismaProjectVersionAiRun]:
        """Get ProjectVersionAiRun by externalRunId."""
        try:
            query = select(PrismaProjectVersionAiRun).where(
                PrismaProjectVersionAiRun.externalRunId == external_run_id
            )
            result = await self.session.execute(query)
            return result.scalar_one_or_none()
        except Exception as e:
            logger.error(f"Error fetching ProjectVersionAiRun for externalRunId {external_run_id}: {e}")
            return None

    async def _get_project_version(self, project_version_id: str) -> Optional[PrismaProjectVersion]:
        """Get ProjectVersion by ID."""
        try:
            query = select(PrismaProjectVersion).where(
                PrismaProjectVersion.id == project_version_id
            )
            result = await self.session.execute(query)
            return result.scalar_one_or_none()
        except Exception as e:
            logger.error(f"Error fetching ProjectVersion {project_version_id}: {e}")
            return None

    def _get_messages_from_snapshot(
        self,
        chat_snapshot: Optional[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Extract messages from chatSnapshot JSON.

        chatSnapshot structure:
        {
            "messages": [
                {
                    "id": 1,
                    "role": "user",
                    "content": "...",
                    "tool_used": null,
                    "changes_made": [],
                    "created_at": "..."
                },
                ...
            ]
        }
        """
        if not chat_snapshot:
            return []

        messages = chat_snapshot.get("messages", [])
        return messages[-20:]  # Return last 20 messages

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

    def _extract_budget_from_goal_text(self, goal_text: str) -> Optional[float]:
        """Extract budget amount from goal_text using regex.

        Supports formats like:
        - "€2M budget" -> 2000000
        - "2M EUR" -> 2000000
        - "€500K" -> 500000
        - "500000 euros" -> 500000
        """
        import re

        if not goal_text:
            return None

        # Normalize text
        text = goal_text.lower().replace(",", "").replace(" ", "")

        # Pattern 1: €2M, €500K, 2M€, etc.
        pattern_m = r'[€$]?(\d+(?:\.\d+)?)\s*m(?:illion)?|(\d+(?:\.\d+)?)\s*m(?:illion)?\s*[€$]?'
        match = re.search(pattern_m, text, re.IGNORECASE)
        if match:
            value = float(match.group(1) or match.group(2))
            return value * 1_000_000

        # Pattern 2: €500K, 500K€, etc.
        pattern_k = r'[€$]?(\d+(?:\.\d+)?)\s*k|(\d+(?:\.\d+)?)\s*k\s*[€$]?'
        match = re.search(pattern_k, text, re.IGNORECASE)
        if match:
            value = float(match.group(1) or match.group(2))
            return value * 1_000

        # Pattern 3: Plain numbers with currency indicators
        pattern_plain = r'[€$]?\s*(\d{5,})\s*(?:eur(?:o)?s?)?|budget\s*(?:of)?\s*[€$]?\s*(\d{5,})'
        match = re.search(pattern_plain, text, re.IGNORECASE)
        if match:
            value = match.group(1) or match.group(2)
            return float(value)

        return None
