"""Question answering tool for chat agent simple mode.

Answers questions about the allocation result using LLM.
Only active in simple mode (chat_agent_mode=False).

PRISMA-ONLY MODE: Uses PrismaProjectVersionAiRun for context.
"""

import logging
from dataclasses import dataclass
from typing import Optional, Dict, Any, List

from src.services.llm_gateway.client import OpenAIClient
from src.services.chat.tools.context_loader import ChatContext

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
    """

    def __init__(self):
        self.llm_client = OpenAIClient()

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
        """Build context text for the LLM from ChatContext."""
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
            if "channel_allocations" in result:
                parts.append("\nChannel Allocations:")
                for alloc in result["channel_allocations"]:
                    channel = alloc.get("channel", "Unknown")
                    amount = alloc.get("amount_eur", 0)
                    pct = alloc.get("percentage", 0)
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

        return "\n".join(parts)
