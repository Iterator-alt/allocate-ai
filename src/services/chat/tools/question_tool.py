"""Question answering tool for chat agent simple mode.

Answers questions about the allocation result using LLM.
Only active in simple mode (chat_agent_mode=False).

PRISMA-ONLY MODE: Uses PrismaProjectVersionAiRun for context.

CONVERSATION COMPACTION:
When conversation exceeds chat_compaction_threshold (default 10) messages,
older messages are summarized for LLM context. chatSnapshot remains intact.
"""

import logging
from dataclasses import dataclass
from typing import Optional, Dict, Any, List, Tuple

from src.services.llm_gateway.client import OpenAIClient
from src.services.chat.tools.context_loader import ChatContext
from src.config import get_settings

logger = logging.getLogger(__name__)


@dataclass
class QuestionResult:
    """Result of answering a question."""

    success: bool
    message: str
    question: str


QUESTION_SYSTEM_PROMPT = """You are a helpful assistant for a media budget allocation tool called AllocateAI.

Your role is to answer questions about the current allocation result. Be concise, helpful, and specific.

You have access to:
- The allocation result showing budget distribution across channels
- Competitor data and their spending patterns
- The reasoning behind the allocation

Guidelines:
- Answer questions directly and concisely
- Reference specific numbers from the allocation when relevant
- Explain the reasoning behind allocations when asked
- If you don't have enough information to answer, say so
- Keep responses under 3-4 sentences unless more detail is needed
- Use EUR for currency values
- Format large numbers with thousands separators (e.g., EUR 1,500,000)

Do NOT:
- Suggest making changes to the allocation
- Offer to modify inputs or rerun
- Promise capabilities you don't have
"""


class QuestionAnswerTool:
    """Answers questions about the allocation result.

    Used in simple mode to provide Q&A capability without
    allowing any modifications to the run.

    Implements conversation compaction: when message count exceeds threshold,
    older messages are summarized for LLM context while chatSnapshot stays intact.
    """

    def __init__(self):
        self.llm_client = OpenAIClient()
        self._last_compaction_info: Dict[str, Any] = {}  # For debug logging

    async def answer(
        self,
        run_id: int,
        question: str,
        context: ChatContext,
    ) -> QuestionResult:
        """Answer a question about the allocation result.

        Args:
            run_id: externalRunId from ProjectVersionAiRun
            question: User's question
            context: Current chat context with allocation data

        Returns:
            QuestionResult with the answer
        """
        # Build context for the LLM
        context_text = self._build_context_text(context)

        # Build the prompt
        user_prompt = f"""Here is the current allocation context:

{context_text}

User question: {question}

Please answer the user's question based on the allocation data above."""

        try:
            # Call LLM using the generate method
            # IMPORTANT: json_mode=False for natural language responses
            response = await self.llm_client.generate(
                system_prompt=QUESTION_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                temperature=0.3,
                max_tokens=500,
                json_mode=False,  # We want natural language, not JSON
            )

            answer = response.content.strip()

            return QuestionResult(
                success=True,
                message=answer,
                question=question,
            )

        except Exception as e:
            import traceback
            logger.error(f"Error answering question for run {run_id}: {str(e)}")
            logger.error(f"Traceback: {traceback.format_exc()}")
            return QuestionResult(
                success=False,
                message=f"I encountered an error while processing your question. Please try again.",
                question=question,
            )

    def _build_context_text(self, context: ChatContext) -> str:
        """Build context text for the LLM from ChatContext.

        Includes conversation compaction when message count exceeds threshold.
        """
        parts = []

        # Basic info
        parts.append(f"Customer: {context.customer_name}")
        parts.append(f"Industry: {context.industry}")
        parts.append(f"KPI: {context.brand_kpi}")

        if context.total_budget:
            parts.append(f"Total Budget: EUR {context.total_budget:,.0f}")

        if context.direction:
            parts.append(f"Direction: {context.direction}")

        if context.goal_text:
            parts.append(f"Goal: {context.goal_text}")

        # Competitors
        if context.current_competitors:
            parts.append(f"\nCompetitors in analysis: {', '.join(context.current_competitors)}")

        # Channels
        if context.channels:
            parts.append(f"Channels: {', '.join(context.channels)}")

        # Allocation result
        if context.allocation_result:
            parts.append("\n--- ALLOCATION RESULT ---")
            result = context.allocation_result

            # Channel allocations
            if "allocations" in result:
                parts.append("\nChannel Allocations:")
                for alloc in result["allocations"]:
                    channel = alloc.get("channel", "Unknown")
                    amount = alloc.get("budget_gross_eur") or 0
                    pct = alloc.get("share_pct") or 0
                    parts.append(f"  - {channel}: EUR {amount:,.0f} ({pct:.1f}%)")

            # Reasoning summary
            if "reasoning_summary" in result:
                parts.append(f"\nReasoning: {result['reasoning_summary']}")

            # Competitor insights if available
            if "competitor_insights" in result:
                parts.append("\nCompetitor Insights:")
                for insight in result["competitor_insights"][:3]:  # Limit to 3
                    parts.append(f"  - {insight}")

        else:
            parts.append("\n[No allocation result available yet]")

        # Add conversation history (compacted if needed)
        recent_msgs, conversation_summary, compaction_info = self._get_compacted_conversation(context)

        if conversation_summary:
            parts.append("\n--- EARLIER CONVERSATION (SUMMARY) ---")
            parts.append(conversation_summary)

        if recent_msgs:
            parts.append("\n--- RECENT CONVERSATION ---")
            for msg in recent_msgs:
                role = msg.get("role", "unknown").capitalize()
                content = msg.get("content", "")
                parts.append(f"{role}: {content}")

        # Store compaction info for debug logging
        self._last_compaction_info = compaction_info
        logger.info(f"_build_context_text: stored compaction_info={compaction_info}")

        return "\n".join(parts)

    def _get_compacted_conversation(
        self,
        context: ChatContext,
    ) -> Tuple[List[Dict[str, Any]], Optional[str], Dict[str, Any]]:
        """Get conversation with compaction for LLM context.

        Returns:
            Tuple of (recent_messages, conversation_summary, compaction_info)
            - If no compaction: (all_messages, None, info)
            - If compacted: (last_N_messages, summary_of_older, info)

        Note: chatSnapshot remains intact - compaction is internal only.
        """
        settings = get_settings()
        threshold = settings.chat_compaction_threshold
        keep_recent = settings.chat_compaction_keep_recent

        messages = context.recent_messages  # Already limited to 20 by context_loader

        # Filter out system cards (warnings, summaries) - only count user/agent messages
        user_agent_messages = [
            m for m in messages
            if m.get("role") in ("user", "agent")
        ]

        total_count = len(user_agent_messages)

        compaction_info = {
            "total_messages_in_snapshot": total_count,
            "compaction_threshold": threshold,
            "keep_recent": keep_recent,
            "compaction_applied": False,
            "messages_summarized": 0,
            "messages_kept_verbatim": total_count,
        }

        if total_count <= threshold:
            # No compaction needed
            logger.info(
                f"COMPACTION NOT NEEDED: total_messages={total_count}, "
                f"threshold={threshold}"
            )
            return user_agent_messages, None, compaction_info

        # Split into older and recent
        older_messages = user_agent_messages[:-keep_recent]
        recent_messages = user_agent_messages[-keep_recent:]

        # Build summary of older messages
        summary = self._summarize_older_messages(older_messages)

        compaction_info["compaction_applied"] = True
        compaction_info["messages_summarized"] = len(older_messages)
        compaction_info["messages_kept_verbatim"] = len(recent_messages)
        compaction_info["conversation_summary"] = summary  # Include in debug

        logger.info(
            f"COMPACTION TRIGGERED: total_messages={total_count}, "
            f"compacting={len(older_messages)}, keeping_recent={len(recent_messages)}"
        )

        return recent_messages, summary, compaction_info

    def _summarize_older_messages(self, messages: List[Dict[str, Any]]) -> str:
        """Summarize older messages for context compaction.

        This is a simple extraction, NOT an LLM call - to keep it fast.
        Extracts tool actions and truncates long content.
        """
        summary_parts = []

        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            tool_used = msg.get("tool_used")
            changes_made = msg.get("changes_made", [])

            if role == "user":
                # Truncate long user messages
                truncated = content[:100] + "..." if len(content) > 100 else content
                summary_parts.append(f"User: {truncated}")

            elif role == "agent":
                if tool_used and changes_made:
                    # Summarize by action taken
                    if tool_used == "competitor_add":
                        brands = [c.get("brand") for c in changes_made if c.get("type") == "competitor_add"]
                        if brands:
                            summary_parts.append(f"Agent: Added competitor(s): {', '.join(filter(None, brands))}")
                        else:
                            summary_parts.append(f"Agent: {tool_used}")
                    elif tool_used == "competitor_remove":
                        brands = [c.get("brand") for c in changes_made if c.get("type") == "competitor_remove"]
                        if brands:
                            summary_parts.append(f"Agent: Removed competitor(s): {', '.join(filter(None, brands))}")
                        else:
                            summary_parts.append(f"Agent: {tool_used}")
                    elif tool_used == "edit_input":
                        for c in changes_made:
                            if c.get("type") == "edit":
                                summary_parts.append(f"Agent: Changed {c.get('field')} to {c.get('new')}")
                    elif tool_used == "rerun":
                        summary_parts.append("Agent: Triggered rerun")
                    elif tool_used == "question":
                        truncated = content[:80] + "..." if len(content) > 80 else content
                        summary_parts.append(f"Agent answered: {truncated}")
                    else:
                        summary_parts.append(f"Agent: {tool_used}")
                elif tool_used == "question":
                    truncated = content[:80] + "..." if len(content) > 80 else content
                    summary_parts.append(f"Agent answered: {truncated}")
                elif tool_used:
                    summary_parts.append(f"Agent: {tool_used}")
                else:
                    # Q&A or simple response - truncate
                    truncated = content[:80] + "..." if len(content) > 80 else content
                    summary_parts.append(f"Agent: {truncated}")

        return "\n".join(summary_parts)
