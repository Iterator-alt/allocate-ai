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

    COMPETITOR_ADD = "competitor_add"
    COMPETITOR_REMOVE = "competitor_remove"
    EDIT_INPUT = "edit_input"
    RERUN = "rerun"
    UNKNOWN = "unknown"


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
- edit_input: User wants to change campaign parameters (budget, channels, goal, KPI direction)
- rerun: User explicitly wants to regenerate/rerun the allocation (phrases: "rerun", "regenerate", "apply changes", "run again", "redo")
- unknown: Cannot determine intent, need clarification

IMPORTANT RULES:
1. Only return ONE intent - the FIRST/PRIMARY intent if multiple are present
2. For rerun: ONLY classify as rerun for explicit phrases like "rerun", "regenerate", "apply changes", "run again", "redo"
3. Do NOT classify as rerun for: "looks good", "okay", "thanks", "yes", confirmations
4. Extract relevant entities (brand names, field names, values)

Editable fields:
- total_budget: Budget amount (e.g., "set budget to 500000", "budget 500k")
- channels: Channel list (e.g., "add TV channel", "remove digital")
- goal_text: Goal description (e.g., "change goal to increase awareness")
- brand_kpi: KPI type - must be one of: adaware, aided, consider
- direction: KPI direction - must be one of: increase, maintain, decrease

Respond in JSON format:
{
    "intent": "competitor_add|competitor_remove|edit_input|rerun|unknown",
    "confidence": 0.0-1.0,
    "entities": {
        "brands": ["brand1", "brand2"],
        "field": "total_budget|channels|goal_text|brand_kpi|direction",
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
