"""Intent classification for chat agent.

Uses GPT-4o-mini for fast, low-cost intent detection from user messages.
Classifies into: competitor_add, competitor_remove, edit_input, rerun, unknown.
"""

import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Dict, Any

from src.services.llm_gateway.client import OpenAIClient

logger = logging.getLogger(__name__)


class IntentType(str, Enum):
    """Types of intents the agent can detect."""

    # Full agent mode intents
    COMPETITOR_ADD = "competitor_add"
    COMPETITOR_REMOVE = "competitor_remove"
    EDIT_INPUT = "edit_input"
    RERUN = "rerun"
    UNKNOWN = "unknown"
    ALLOCATION_PREFERENCE = "allocation_preference"  # User expresses allocation weighting / goal adjustment

    # Simple mode intents
    QUESTION = "question"  # General question about allocation result
    BLOCKED_EDIT = "blocked_edit"  # Tried to edit definition area fields
    BLOCKED_COMPETITOR = "blocked_competitor"  # Tried to change competitors
    BLOCKED_RERUN = "blocked_rerun"  # Tried to trigger rerun


@dataclass
class ExtractedEntity:
    """An entity extracted from the user message."""

    type: str  # 'brand', 'field', 'value', etc.
    value: Any
    confidence: float = 1.0


@dataclass
class IntentClassificationResult:
    """Result of intent classification."""

    intents: List[IntentType]
    entities: Dict[str, List[ExtractedEntity]] = field(default_factory=dict)
    confidence: float = 1.0
    raw_response: Optional[str] = None


INTENT_CLASSIFICATION_SYSTEM_PROMPT = """You are an intent classifier for a media budget allocation chat agent.

Given a user message, classify it into ONE of these intents:
- competitor_add: User wants to add a brand/competitor to the analysis
- competitor_remove: User wants to remove a brand/competitor from the analysis
- edit_input: User wants to change campaign parameters (budget total, channel list membership, KPI type, KPI direction)
- allocation_preference: User expresses how the allocation should be weighted or shifted between the existing channels (e.g., "increase TV by 10%", "less Print", "prioritize Digital", "don't over-invest in Radio") OR adjusts the goal/strategy in free text (e.g., "the goal should focus more on younger audiences")
- rerun: User explicitly wants to regenerate/rerun the allocation (phrases: "rerun", "regenerate", "apply changes", "run again", "redo")
- unknown: Cannot determine intent, need clarification

IMPORTANT RULES:
1. Only return ONE intent - the FIRST/PRIMARY intent if multiple are present
2. For rerun: ONLY classify as rerun for explicit phrases like "rerun", "regenerate", "apply changes", "run again", "redo"
3. Do NOT classify as rerun for: "looks good", "okay", "thanks", "yes", confirmations
4. Extract relevant entities (brand names, field names, values)
5. edit_input vs allocation_preference: edit_input is ONLY for changing the budget total, the KPI type, the KPI direction, or adding/removing channels from the channel selection. Shifting weight between existing channels or adjusting the goal/strategy wording is allocation_preference, NOT edit_input.

Editable fields:
- total_budget: Budget amount (e.g., "set budget to 500000", "budget 500k")
- channels: Channel list (e.g., "add TV channel", "remove digital")
- brand_kpi: KPI type - must be one of: adaware, aided, consider
- direction: KPI direction - must be one of: increase, maintain, decrease

Respond in JSON format:
{
    "intent": "competitor_add|competitor_remove|edit_input|allocation_preference|rerun|unknown",
    "confidence": 0.0-1.0,
    "entities": {
        "brands": ["brand1", "brand2"],
        "field": "total_budget|channels|brand_kpi|direction",
        "value": "the new value",
        "action": "add|remove" (for channels)
    },
    "reasoning": "brief explanation"
}"""


class IntentClassifier:
    """Classifies user message intents using GPT-4o-mini."""

    MODEL = "gpt-4o-mini"  # Fast, low-cost model for classification

    def __init__(self):
        self.llm_client = OpenAIClient(model=self.MODEL)

    async def classify(
        self,
        message: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> IntentClassificationResult:
        """Classify the intent of a user message.

        Args:
            message: The user's message text
            context: Optional context about current state (competitors, inputs, etc.)

        Returns:
            IntentClassificationResult with detected intent(s) and entities
        """
        # Build user prompt with optional context
        user_prompt = f"User message: {message}"

        if context:
            context_str = json.dumps(context, indent=2, default=str)
            user_prompt = f"""Current context:
{context_str}

User message: {message}"""

        try:
            response = await self.llm_client.generate(
                system_prompt=INTENT_CLASSIFICATION_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                temperature=0.1,  # Low temperature for consistent classification
                max_tokens=500,
                json_mode=True,
            )

            # Parse the response
            parsed = response.parsed_json
            if not parsed:
                logger.warning(f"Failed to parse intent classification response: {response.content}")
                return IntentClassificationResult(
                    intents=[IntentType.UNKNOWN],
                    confidence=0.0,
                    raw_response=response.content,
                )

            # Map intent string to enum
            intent_str = parsed.get("intent", "unknown").lower()
            try:
                intent = IntentType(intent_str)
            except ValueError:
                intent = IntentType.UNKNOWN

            # Extract entities
            entities: Dict[str, List[ExtractedEntity]] = {}
            raw_entities = parsed.get("entities", {})

            if "brands" in raw_entities and raw_entities["brands"]:
                entities["brands"] = [
                    ExtractedEntity(type="brand", value=b)
                    for b in raw_entities["brands"]
                ]

            if "field" in raw_entities and raw_entities["field"]:
                entities["field"] = [
                    ExtractedEntity(type="field", value=raw_entities["field"])
                ]

            if "value" in raw_entities and raw_entities["value"] is not None:
                entities["value"] = [
                    ExtractedEntity(type="value", value=raw_entities["value"])
                ]

            if "action" in raw_entities and raw_entities["action"]:
                entities["action"] = [
                    ExtractedEntity(type="action", value=raw_entities["action"])
                ]

            return IntentClassificationResult(
                intents=[intent],
                entities=entities,
                confidence=parsed.get("confidence", 1.0),
                raw_response=response.content,
            )

        except Exception as e:
            logger.error(f"Intent classification failed: {str(e)}")
            return IntentClassificationResult(
                intents=[IntentType.UNKNOWN],
                confidence=0.0,
                raw_response=str(e),
            )

    async def classify_simple_mode(
        self,
        message: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> IntentClassificationResult:
        """Classify intent in simple mode (Q&A only, no modifications allowed).

        In simple mode:
        - Questions about the result -> QUESTION
        - Edit attempts (budget, KPI, channels, customer, industry) -> BLOCKED_EDIT
        - Allocation preferences / goal adjustments -> ALLOCATION_PREFERENCE (passes through)
        - Competitor changes -> BLOCKED_COMPETITOR
        - Rerun requests -> BLOCKED_RERUN
        - Everything else -> QUESTION (default to answering)

        Args:
            message: The user's message text
            context: Optional context about current state

        Returns:
            IntentClassificationResult with simple mode intent
        """
        # First, use normal classification to detect what user is trying to do
        result = await self.classify(message, context)

        if not result.intents:
            return IntentClassificationResult(
                intents=[IntentType.QUESTION],
                confidence=1.0,
                raw_response=result.raw_response,
            )

        original_intent = result.intents[0]

        # Map full mode intents to simple mode intents
        if original_intent == IntentType.COMPETITOR_ADD:
            return IntentClassificationResult(
                intents=[IntentType.BLOCKED_COMPETITOR],
                entities=result.entities,
                confidence=result.confidence,
                raw_response=result.raw_response,
            )

        elif original_intent == IntentType.COMPETITOR_REMOVE:
            return IntentClassificationResult(
                intents=[IntentType.BLOCKED_COMPETITOR],
                entities=result.entities,
                confidence=result.confidence,
                raw_response=result.raw_response,
            )

        elif original_intent == IntentType.EDIT_INPUT:
            return IntentClassificationResult(
                intents=[IntentType.BLOCKED_EDIT],
                entities=result.entities,
                confidence=result.confidence,
                raw_response=result.raw_response,
            )

        elif original_intent == IntentType.RERUN:
            return IntentClassificationResult(
                intents=[IntentType.BLOCKED_RERUN],
                entities=result.entities,
                confidence=result.confidence,
                raw_response=result.raw_response,
            )

        elif original_intent == IntentType.ALLOCATION_PREFERENCE:
            # Allocation preferences pass through unblocked - they are picked up
            # from chatSnapshot at the next rerun (preference extraction)
            return result

        else:
            # UNKNOWN or anything else -> treat as question
            return IntentClassificationResult(
                intents=[IntentType.QUESTION],
                entities=result.entities,
                confidence=result.confidence,
                raw_response=result.raw_response,
            )
