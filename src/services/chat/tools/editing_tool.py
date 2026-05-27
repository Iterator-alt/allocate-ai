"""Interactive editing tool for chat agent.

Tool 2: Edit campaign inputs including:
- total_budget: Budget amount
- channels: Channel list (add/remove)
- goal_text: Goal description
- brand_kpi: adaware/aided/consider
- direction: increase/maintain/decrease

PRISMA-ONLY MODE: Stores edits in ProjectVersionAiRun.rawPayload
since ProjectVersion is READ-ONLY from Python.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Dict, Any, Union
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.prisma_tables import PrismaProjectVersionAiRun
from src.services.chat.tools.context_loader import ChatContext

logger = logging.getLogger(__name__)


# Valid values for constrained fields
VALID_KPI_VALUES = ["adaware", "aided", "consider"]
VALID_DIRECTION_VALUES = ["increase", "maintain", "decrease"]


@dataclass
class EditResult:
    """Result of an edit operation."""

    success: bool
    field: str
    message: str
    old_value: Any = None
    new_value: Any = None
    needs_clarification: bool = False
    clarification_question: Optional[str] = None
    change_record: Optional[Dict[str, Any]] = None


class InteractiveEditingTool:
    """Handles interactive editing of campaign inputs.

    Editable fields:
    - total_budget: Budget amount
    - channels: Channel list (add/remove)
    - goal_text: Goal description
    - brand_kpi: adaware/aided/consider
    - direction: increase/maintain/decrease

    PRISMA-ONLY MODE: Stores edits in ProjectVersionAiRun.rawPayload["chat_edits"]
    since ProjectVersion is READ-ONLY from Python.
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    async def edit_field(
        self,
        run_id: int,
        field: str,
        value: Any,
        context: ChatContext,
        action: Optional[str] = None,  # "add" or "remove" for channels
    ) -> EditResult:
        """Edit a campaign field.

        Args:
            run_id: externalRunId from ProjectVersionAiRun
            field: Field name to edit
            value: New value
            context: Current chat context
            action: For channels, "add" or "remove"

        Returns:
            EditResult with outcome
        """
        field = field.lower().strip()

        # Validate field name
        valid_fields = ["total_budget", "channels", "goal_text", "brand_kpi", "direction"]
        if field not in valid_fields:
            return EditResult(
                success=False,
                field=field,
                message=f"Unknown field '{field}'. Editable fields are: {', '.join(valid_fields)}",
            )

        # Dispatch to appropriate handler
        if field == "total_budget":
            return await self._edit_budget(run_id, value, context)
        elif field == "channels":
            return await self._edit_channels(run_id, value, context, action)
        elif field == "goal_text":
            return await self._edit_goal_text(run_id, value, context)
        elif field == "brand_kpi":
            return await self._edit_brand_kpi(run_id, value, context)
        elif field == "direction":
            return await self._edit_direction(run_id, value, context)

        return EditResult(
            success=False,
            field=field,
            message=f"Field '{field}' editing not implemented.",
        )

    async def _edit_budget(
        self,
        run_id: int,
        value: Any,
        context: ChatContext,
    ) -> EditResult:
        """Edit the total budget."""
        # Parse budget value
        try:
            if isinstance(value, str):
                # Remove common formatting
                value = value.replace(",", "").replace("€", "").replace("EUR", "").strip()
                # Handle k/K for thousands, m/M for millions
                if value.lower().endswith("k"):
                    budget = float(value[:-1]) * 1000
                elif value.lower().endswith("m"):
                    budget = float(value[:-1]) * 1000000
                else:
                    budget = float(value)
            else:
                budget = float(value)
        except (ValueError, TypeError):
            return EditResult(
                success=False,
                field="total_budget",
                message=f"Could not parse '{value}' as a budget amount. Please provide a number like '500000' or '500k'.",
            )

        if budget <= 0:
            return EditResult(
                success=False,
                field="total_budget",
                message="Budget must be a positive number.",
            )

        old_value = context.total_budget
        await self._update_chat_edit(run_id, "total_budget", budget)

        change_record = {
            "type": "edit",
            "field": "total_budget",
            "old": old_value,
            "new": budget,
        }

        # Format for display
        formatted = f"€{budget:,.0f}"

        return EditResult(
            success=True,
            field="total_budget",
            message=f"Budget updated to {formatted}.",
            old_value=old_value,
            new_value=budget,
            change_record=change_record,
        )

    async def _edit_channels(
        self,
        run_id: int,
        value: Any,
        context: ChatContext,
        action: Optional[str] = None,
    ) -> EditResult:
        """Edit the channels list."""
        current_channels = context.channels or []

        # Determine action if not specified
        if not action:
            # Default to "add" for new channel, or "set" for list
            if isinstance(value, list):
                action = "set"
            else:
                action = "add"

        if action == "set":
            # Replace entire channel list
            if isinstance(value, str):
                new_channels = [v.strip() for v in value.split(",")]
            else:
                new_channels = list(value)

            old_value = current_channels
            await self._update_chat_edit(run_id, "channels", new_channels)

            change_record = {
                "type": "edit",
                "field": "channels",
                "old": old_value,
                "new": new_channels,
            }

            return EditResult(
                success=True,
                field="channels",
                message=f"Channels set to: {', '.join(new_channels)}.",
                old_value=old_value,
                new_value=new_channels,
                change_record=change_record,
            )

        elif action == "add":
            # Add channel to list
            channel = str(value).strip()
            if channel.lower() in [c.lower() for c in current_channels]:
                return EditResult(
                    success=False,
                    field="channels",
                    message=f"Channel '{channel}' is already in the list.",
                )

            new_channels = current_channels + [channel]
            await self._update_chat_edit(run_id, "channels", new_channels)

            change_record = {
                "type": "edit",
                "field": "channels",
                "action": "add",
                "value": channel,
                "old": current_channels,
                "new": new_channels,
            }

            return EditResult(
                success=True,
                field="channels",
                message=f"Added '{channel}' to channels.",
                old_value=current_channels,
                new_value=new_channels,
                change_record=change_record,
            )

        elif action == "remove":
            # Remove channel from list
            channel = str(value).strip()
            matching = [c for c in current_channels if c.lower() == channel.lower()]
            if not matching:
                return EditResult(
                    success=False,
                    field="channels",
                    message=f"Channel '{channel}' is not in the list.",
                )

            new_channels = [c for c in current_channels if c.lower() != channel.lower()]
            await self._update_chat_edit(run_id, "channels", new_channels)

            change_record = {
                "type": "edit",
                "field": "channels",
                "action": "remove",
                "value": channel,
                "old": current_channels,
                "new": new_channels,
            }

            return EditResult(
                success=True,
                field="channels",
                message=f"Removed '{matching[0]}' from channels.",
                old_value=current_channels,
                new_value=new_channels,
                change_record=change_record,
            )

        return EditResult(
            success=False,
            field="channels",
            message=f"Unknown action '{action}'. Use 'add', 'remove', or 'set'.",
        )

    async def _edit_goal_text(
        self,
        run_id: int,
        value: Any,
        context: ChatContext,
    ) -> EditResult:
        """Edit the goal text."""
        goal_text = str(value).strip()

        if len(goal_text) > 1000:
            return EditResult(
                success=False,
                field="goal_text",
                message="Goal text is too long. Please keep it under 1000 characters.",
            )

        old_value = context.goal_text
        await self._update_chat_edit(run_id, "goal_text", goal_text)

        change_record = {
            "type": "edit",
            "field": "goal_text",
            "old": old_value,
            "new": goal_text,
        }

        return EditResult(
            success=True,
            field="goal_text",
            message=f"Goal updated to: \"{goal_text}\"",
            old_value=old_value,
            new_value=goal_text,
            change_record=change_record,
        )

    async def _edit_brand_kpi(
        self,
        run_id: int,
        value: Any,
        context: ChatContext,
    ) -> EditResult:
        """Edit the brand KPI."""
        kpi = str(value).lower().strip()

        if kpi not in VALID_KPI_VALUES:
            return EditResult(
                success=False,
                field="brand_kpi",
                message=f"Invalid KPI '{kpi}'. Must be one of: {', '.join(VALID_KPI_VALUES)}",
            )

        old_value = context.brand_kpi
        await self._update_chat_edit(run_id, "brand_kpi", kpi)

        change_record = {
            "type": "edit",
            "field": "brand_kpi",
            "old": old_value,
            "new": kpi,
        }

        return EditResult(
            success=True,
            field="brand_kpi",
            message=f"KPI changed to '{kpi}'.",
            old_value=old_value,
            new_value=kpi,
            change_record=change_record,
        )

    async def _edit_direction(
        self,
        run_id: int,
        value: Any,
        context: ChatContext,
    ) -> EditResult:
        """Edit the direction (increase/maintain/decrease)."""
        direction = str(value).lower().strip()

        if direction not in VALID_DIRECTION_VALUES:
            return EditResult(
                success=False,
                field="direction",
                message=f"Invalid direction '{direction}'. Must be one of: {', '.join(VALID_DIRECTION_VALUES)}",
            )

        old_value = context.direction
        await self._update_chat_edit(run_id, "direction", direction)

        change_record = {
            "type": "edit",
            "field": "direction",
            "old": old_value,
            "new": direction,
        }

        return EditResult(
            success=True,
            field="direction",
            message=f"Direction set to '{direction}'.",
            old_value=old_value,
            new_value=direction,
            change_record=change_record,
        )

    async def _update_chat_edit(
        self,
        run_id: int,
        field: str,
        value: Any,
    ) -> None:
        """Update a field in ProjectVersionAiRun.rawPayload["chat_edits"].

        Since ProjectVersion is READ-ONLY from Python, we store edits
        in rawPayload and the context_loader reads from there.
        """
        query = select(PrismaProjectVersionAiRun).where(
            PrismaProjectVersionAiRun.externalRunId == run_id
        )
        result = await self.session.execute(query)
        ai_run = result.scalar_one_or_none()

        if not ai_run:
            raise ValueError(f"ProjectVersionAiRun with externalRunId {run_id} not found")

        # Get or initialize rawPayload
        raw_payload = ai_run.rawPayload or {}

        # Get or initialize chat_edits
        chat_edits = raw_payload.get("chat_edits", {})

        # Update the field
        chat_edits[field] = value
        chat_edits["updated_at"] = datetime.utcnow().isoformat()

        # Store back
        raw_payload["chat_edits"] = chat_edits
        ai_run.rawPayload = raw_payload
        ai_run.updatedAt = datetime.utcnow()

        await self.session.flush()
