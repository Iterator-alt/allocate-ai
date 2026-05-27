"""Main Chat Agent orchestrator.

Processes user messages, routes to appropriate tools, and generates responses.
Maintains context of project state, run history, and chat history.
"""

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Dict, Any, List, Union

from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import BackgroundTasks

from src.services.chat.intent_classifier import IntentClassifier, IntentType, IntentClassificationResult
from src.services.chat.tools.context_loader import ContextLoaderTool, ChatContext
from src.services.chat.tools.competitor_tool import CompetitorManagementTool, CompetitorResult
from src.services.chat.tools.editing_tool import InteractiveEditingTool, EditResult
from src.services.chat.tools.rerun_tool import RerunTool, RerunResult
from src.services.llm_gateway.client import OpenAIClient
from src.repositories.run import ChatHistoryRepository
from src.services.stage1.debug_output import is_debug_mode, _save_debug_file

logger = logging.getLogger(__name__)


@dataclass
class AgentResponse:
    """Response from the chat agent."""

    response_text: str
    tool_used: Optional[str] = None
    updated_competitors: Optional[List[str]] = None
    updated_inputs: Optional[Dict[str, Any]] = None
    rerun_triggered: bool = False
    rerun_blocked_reason: Optional[str] = None
    chat_message_id: int = 0
    new_run_id: Optional[int] = None
    new_version_name: Optional[str] = None  # e.g., "v2", "v3"
    pending_changes: Optional[List[Dict[str, Any]]] = None


RESPONSE_GENERATION_SYSTEM_PROMPT = """You are a helpful assistant for a media budget allocation tool.
Your role is to respond naturally to the user based on the action that was just performed.

Keep responses concise and friendly. Don't over-explain.
If an action was successful, briefly confirm what was done.
If an action failed, explain why in simple terms.

When changes are made, remind the user: "Hit Generate to apply your changes."
Do NOT suggest rerunning if no changes have been made."""


class ChatAgent:
    """Main orchestrator for chat agent functionality.

    Flow:
    1. Load context (Tool 0) - includes pending_changes from chat history
    2. Classify intent(s)
    3. Execute FIRST intent only (ignore rest)
    4. Generate response
    5. Save to chat history with extra_data
    6. Return structured response
    """

    def __init__(
        self,
        session: AsyncSession,
        background_tasks: Optional[BackgroundTasks] = None,
    ):
        self.session = session
        self.background_tasks = background_tasks

        # Initialize components
        self.intent_classifier = IntentClassifier()
        self.context_loader = ContextLoaderTool(session)
        self.competitor_tool = CompetitorManagementTool(session)
        self.editing_tool = InteractiveEditingTool(session)
        self.rerun_tool = RerunTool(session, background_tasks)
        self.chat_repo = ChatHistoryRepository(session)
        self.llm_client = OpenAIClient()

    async def process_message(
        self,
        project_id: int,
        run_id: int,
        message: str,
        version_id: Optional[int] = None,
    ) -> AgentResponse:
        """Process a user message and return agent response.

        Args:
            project_id: Project ID
            run_id: Run ID
            message: User's message text
            version_id: Optional project version ID

        Returns:
            AgentResponse with response text and metadata
        """
        # Debug logging setup
        debug_run_id = str(run_id) if is_debug_mode() else None

        try:
            # 1. Load context (Tool 0)
            context = await self.context_loader.load(run_id, project_id)

            if debug_run_id:
                _save_debug_file(debug_run_id, "C0_context_loaded", {
                    "run_id": run_id,
                    "project_id": project_id,
                    "context": {
                        "run_status": context.run_status,
                        "has_results": context.has_results,
                        "current_competitors": context.current_competitors,
                        "customer_name": context.customer_name,
                        "industry": context.industry,
                        "brand_kpi": context.brand_kpi,
                        "total_budget": context.total_budget,
                        "channels": context.channels,
                        "pending_changes": context.pending_changes,
                        "has_pending_changes": context.has_pending_changes,
                    },
                })

            # 2. Classify intent(s)
            classification = await self.intent_classifier.classify(
                message=message,
                context={
                    "current_competitors": context.current_competitors,
                    "customer_name": context.customer_name,
                    "industry": context.industry,
                    "brand_kpi": context.brand_kpi,
                    "total_budget": context.total_budget,
                    "channels": context.channels,
                },
            )

            if debug_run_id:
                _save_debug_file(debug_run_id, "C1_intent_classification", {
                    "message": message,
                    "intents": [i.value for i in classification.intents],
                    "entities": {
                        k: [{"type": e.type, "value": e.value} for e in v]
                        for k, v in classification.entities.items()
                    },
                    "confidence": classification.confidence,
                    "raw_response": classification.raw_response,
                })

            # 3. Execute FIRST intent only
            first_intent = classification.intents[0] if classification.intents else IntentType.UNKNOWN
            result = await self._execute_tool(first_intent, classification, context, run_id)

            if debug_run_id:
                _save_debug_file(debug_run_id, "C2_tool_execution", {
                    "intent": first_intent.value,
                    "result": self._serialize_result(result),
                })

            # 4. Generate response
            response_text = await self._generate_response(
                message=message,
                intent=first_intent,
                result=result,
                context=context,
            )

            if debug_run_id:
                _save_debug_file(debug_run_id, "C3_response_generation", {
                    "response_text": response_text,
                })

            # 5. Save messages to chat history
            change_record = self._extract_change_record(result)
            user_msg_id = await self._save_user_message(run_id, message)

            # Determine tool_used - for rerun, only mark as "rerun" if it was actually triggered
            # This prevents false rerun markers from clearing pending changes in future context loads
            tool_used = None
            if first_intent != IntentType.UNKNOWN:
                if first_intent == IntentType.RERUN:
                    # Only mark as rerun if it actually succeeded
                    if isinstance(result, RerunResult) and result.rerun_triggered:
                        tool_used = first_intent.value
                    # If rerun failed (no changes), don't store "rerun" as tool_used
                else:
                    tool_used = first_intent.value

            agent_msg_id = await self._save_agent_message(
                run_id=run_id,
                response_text=response_text,
                tool_used=tool_used,
                change_record=change_record,
            )

            # 6. Build and return response
            return self._build_response(
                response_text=response_text,
                intent=first_intent,
                result=result,
                chat_message_id=agent_msg_id,
                context=context,
                change_record=change_record,
            )

        except Exception as e:
            logger.error(f"Error processing message for run {run_id}: {str(e)}")

            # Save error message
            error_response = f"I encountered an error processing your request: {str(e)}"
            try:
                await self._save_user_message(run_id, message)
                agent_msg_id = await self._save_agent_message(
                    run_id=run_id,
                    response_text=error_response,
                    tool_used=None,
                    change_record=None,
                )
            except:
                agent_msg_id = 0

            return AgentResponse(
                response_text=error_response,
                chat_message_id=agent_msg_id,
            )

    async def _execute_tool(
        self,
        intent: IntentType,
        classification: IntentClassificationResult,
        context: ChatContext,
        run_id: int,
    ) -> Union[CompetitorResult, EditResult, RerunResult, None]:
        """Execute the appropriate tool based on intent.

        Returns the tool result or None for unknown intent.
        """
        entities = classification.entities

        if intent == IntentType.COMPETITOR_ADD:
            # Extract brand name from entities
            brands = entities.get("brands", [])
            if brands:
                brand = brands[0].value
                return await self.competitor_tool.add_competitor(run_id, brand, context)
            else:
                return None

        elif intent == IntentType.COMPETITOR_REMOVE:
            brands = entities.get("brands", [])
            if brands:
                brand = brands[0].value
                return await self.competitor_tool.remove_competitor(run_id, brand, context)
            else:
                return None

        elif intent == IntentType.EDIT_INPUT:
            field_entities = entities.get("field", [])
            value_entities = entities.get("value", [])
            action_entities = entities.get("action", [])

            if field_entities and value_entities:
                field = field_entities[0].value
                value = value_entities[0].value
                action = action_entities[0].value if action_entities else None
                return await self.editing_tool.edit_field(run_id, field, value, context, action)
            else:
                return None

        elif intent == IntentType.RERUN:
            return await self.rerun_tool.execute(run_id, context)

        else:
            return None

    async def _generate_response(
        self,
        message: str,
        intent: IntentType,
        result: Union[CompetitorResult, EditResult, RerunResult, None],
        context: ChatContext,
    ) -> str:
        """Generate a natural language response.

        For simple tool results, we use the result's message directly.
        For complex cases or unknown intents, we use the LLM.
        """
        # If we have a tool result with a message, use it
        if result and hasattr(result, 'message'):
            base_message = result.message

            # Add reminder about generating if changes were made
            if hasattr(result, 'success') and result.success:
                if intent in [IntentType.COMPETITOR_ADD, IntentType.COMPETITOR_REMOVE, IntentType.EDIT_INPUT]:
                    # Don't add reminder if rerun was already triggered
                    if not (isinstance(result, RerunResult) and result.rerun_triggered):
                        base_message += "\n\nHit Generate to apply your changes."

            return base_message

        # Unknown intent - ask for clarification
        if intent == IntentType.UNKNOWN:
            return "I didn't quite get that — are you trying to change an input, add or remove a competitor, or rerun the allocation?"

        # Fallback - shouldn't happen
        return "I processed your request but couldn't determine the outcome. Please try again."

    async def _save_user_message(
        self,
        run_id: int,
        message: str,
    ) -> int:
        """Save user message to chat history."""
        chat_msg = await self.chat_repo.create_message(
            run_id=run_id,
            message_type="chat",
            severity="info",
            title="User",
            content=message,
            extra_data={
                "role": "user",
                "tool_used": None,
                "changes_made": [],
            },
        )
        return chat_msg.id

    async def _save_agent_message(
        self,
        run_id: int,
        response_text: str,
        tool_used: Optional[str],
        change_record: Optional[Dict[str, Any]],
    ) -> int:
        """Save agent message to chat history."""
        changes_made = [change_record] if change_record else []

        chat_msg = await self.chat_repo.create_message(
            run_id=run_id,
            message_type="chat",
            severity="info",
            title="Agent",
            content=response_text,
            extra_data={
                "role": "agent",
                "tool_used": tool_used,
                "changes_made": changes_made,
            },
        )
        return chat_msg.id

    def _extract_change_record(
        self,
        result: Union[CompetitorResult, EditResult, RerunResult, None],
    ) -> Optional[Dict[str, Any]]:
        """Extract change record from a tool result."""
        if result is None:
            return None

        if hasattr(result, 'change_record'):
            return result.change_record

        # For rerun, create a special record
        if isinstance(result, RerunResult) and result.rerun_triggered:
            return {"type": "rerun"}

        return None

    def _serialize_result(
        self,
        result: Union[CompetitorResult, EditResult, RerunResult, None],
    ) -> Dict[str, Any]:
        """Serialize a tool result for debug logging."""
        if result is None:
            return {"type": "none"}

        if isinstance(result, CompetitorResult):
            return {
                "type": "CompetitorResult",
                "success": result.success,
                "action": result.action,
                "brand": result.brand,
                "message": result.message,
                "warning": result.warning,
                "updated_competitors": result.updated_competitors,
            }
        elif isinstance(result, EditResult):
            return {
                "type": "EditResult",
                "success": result.success,
                "field": result.field,
                "message": result.message,
                "old_value": str(result.old_value) if result.old_value else None,
                "new_value": str(result.new_value) if result.new_value else None,
            }
        elif isinstance(result, RerunResult):
            return {
                "type": "RerunResult",
                "success": result.success,
                "message": result.message,
                "rerun_triggered": result.rerun_triggered,
                "blocked_reason": result.blocked_reason,
                "new_run_id": result.new_run_id,
            }
        else:
            return {"type": "unknown", "value": str(result)}

    def _build_response(
        self,
        response_text: str,
        intent: IntentType,
        result: Union[CompetitorResult, EditResult, RerunResult, None],
        chat_message_id: int,
        context: ChatContext,
        change_record: Optional[Dict[str, Any]],
    ) -> AgentResponse:
        """Build the final AgentResponse."""
        tool_used = intent.value if intent != IntentType.UNKNOWN else None

        # Extract updated values from results
        updated_competitors = None
        updated_inputs = None
        rerun_triggered = False
        rerun_blocked_reason = None
        new_run_id = None

        if isinstance(result, CompetitorResult) and result.updated_competitors:
            updated_competitors = result.updated_competitors

        if isinstance(result, EditResult) and result.success:
            updated_inputs = {result.field: result.new_value}

        new_version_name = None
        if isinstance(result, RerunResult):
            rerun_triggered = result.rerun_triggered
            rerun_blocked_reason = result.blocked_reason
            new_run_id = result.new_run_id
            new_version_name = result.new_version_name

        # Get updated pending changes
        pending_changes = context.pending_changes.copy() if context.pending_changes else []
        if change_record and change_record.get("type") != "rerun":
            pending_changes.append(change_record)
        elif change_record and change_record.get("type") == "rerun":
            pending_changes = []  # Clear on rerun

        return AgentResponse(
            response_text=response_text,
            tool_used=tool_used,
            updated_competitors=updated_competitors,
            updated_inputs=updated_inputs,
            rerun_triggered=rerun_triggered,
            rerun_blocked_reason=rerun_blocked_reason,
            chat_message_id=chat_message_id,
            new_run_id=new_run_id,
            new_version_name=new_version_name,
            pending_changes=pending_changes,
        )
