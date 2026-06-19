"""Stage 2 (Part 2): Prompt Assembly Service.

Assembles the final LLM prompt from:
1. Filtered competitor data (from DataFilteringService)
2. Expert knowledge (from database)
3. Guardrails (from database)
4. User input parameters

Supports two modes:
- BUDGET_TO_IMPACT: Customer has fixed budget, optimize KPI
- GOAL_TO_BUDGET: Customer has KPI goal, calculate required budget
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import List, Optional, Dict, Any, Literal
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from src.services.mediamix.data_filtering import DataFilteringService, DataFilteringResult

# NOTE: ExpertKnowledgeRepository and PromptGuardrailsRepository are not used
# in Prisma-only mode as the tables don't exist. Default values are used instead.

# Goal direction modes
GoalDirection = Literal["budget_to_impact", "goal_to_budget", "increase", "maintain", "decrease"]


@dataclass
class AssembledPrompt:
    """Final assembled prompt ready for LLM."""

    system_prompt: str
    user_prompt: str
    data_context: str
    expert_knowledge: str
    guardrails: str
    metadata: Dict[str, Any]


@dataclass
class PromptAssemblyInput:
    """Input parameters for prompt assembly."""

    customer_name: str
    industry: str
    brand_kpi: str
    total_budget: Optional[Decimal]
    time_period_start: Optional[datetime]
    time_period_end: Optional[datetime]
    channels: Optional[List[str]]
    nielsen_brands: List[str]
    yougov_brands: List[str]
    additional_context: Optional[str] = None
    goal_direction: Optional[str] = None  # "budget_to_impact" or "goal_to_budget" or "increase"
    goal_text: Optional[str] = None  # Original goal text for Goal→Budget mode
    customer_historical_spend: Optional[float] = None  # Customer's historical total spend in EUR
    chat_preferences: Optional[str] = None  # Net allocation preferences extracted from chat


class PromptAssemblyService:
    """Assembles complete prompts for LLM budget allocation.

    Combines competitor data, expert knowledge, and guardrails
    into a structured prompt format.

    Supports two modes:
    - BUDGET_TO_IMPACT: Fixed budget, optimize for KPI improvement
    - GOAL_TO_BUDGET: KPI goal given, calculate required budget
    """

    # Default system prompt template
    SYSTEM_PROMPT_TEMPLATE = """You are an expert media planner. Your recommendations must be DATA-DRIVEN, based on the competitor spend and KPI data provided.

CRITICAL INSTRUCTIONS:
1. Base ALL allocation percentages on competitor spend ratios from the data
2. Each channel rationale MUST cite specific competitor names, spend amounts (in EUR), and KPI changes
3. Do NOT use generic phrases like "broad reach" or "precise targeting" - use actual data
4. The summary MUST name which competitors were used as benchmarks

REASONING FORMAT FOR EACH CHANNEL:
- Name the competitor(s) who spent on this channel
- State their exact spend amount in EUR
- State their KPI change (e.g., -1.21pp)
- Explain why this informs your allocation

Example rationale: "[Competitor A] spent €X on [CHANNEL] with -Y.YYpp awareness change (best in sector). [Competitor B] spent €Z with -W.WWpp. [Channel] dominates competitor spend (X% of total), recommending Y% allocation."

{guardrails}
"""

    # User prompt template for BUDGET_TO_IMPACT mode
    BUDGET_TO_IMPACT_TEMPLATE = """## Client Information
- **Client**: {customer_name}
- **Industry**: {industry}
- **Target KPI**: {brand_kpi}
- **Total Budget**: €{total_budget:,.2f}
{time_period_line}
{channels_line}

## Mode: BUDGET → IMPACT
Customer has a gross budget of €{total_budget:,.2f}. Recommend how to split this budget based on competitor spend patterns.

## Competitor Spend & KPI Data
{data_context}

## Reference Information
{expert_knowledge}

## ALLOCATION RULES (Data-Driven)
1. Calculate the total competitor spend per channel from the data above
2. Use competitor channel mix ratios as your starting point for allocation percentages
3. Adjust based on KPI efficiency: favor channels where competitors achieved better KPI results
4. The competitor with the smallest KPI decline (closest to 0 or positive) is the most efficient benchmark

## Your Task
Recommend an optimal channel allocation for {customer_name} based on the competitor benchmarks above.

MANDATORY REQUIREMENTS:
1. Each channel rationale MUST name specific competitors and their spend amounts
2. Rationale MUST reference the competitor's KPI change for that channel
3. Explain how competitor spend ratios influenced your percentage allocation
4. The summary MUST state which competitor(s) were used as the primary benchmark and why

Return JSON:
```json
{{
  "allocations": [
    {{"channel": "CHANNEL_NAME", "percentage": 35.0, "amount": 350000.00, "rationale": "[Competitor] spent €[X]M on [channel] with [Y]pp KPI change. Based on their [efficiency/dominance], allocating [Z]%."}},
    ...
  ],
  "total_percentage": 100.0,
  "total_budget_eur": {total_budget},
  "kpi_projection": 1.5,
  "summary": "Using [Competitor1] and [Competitor2] as benchmarks (combined spend €X, KPI changes of Y and Z). Strategy focuses on [reasoning based on their data].",
  "confidence": 0.85,
  "warnings": ["any data quality issues"]
}}
```

{additional_context}
"""

    # User prompt template for GOAL_TO_BUDGET mode
    GOAL_TO_BUDGET_TEMPLATE = """## Client Information
- **Client**: {customer_name}
- **Industry**: {industry}
- **Target KPI**: {brand_kpi}
- **Customer Historical Spend**: {customer_historical_spend_line}
{time_period_line}
{channels_line}

## Mode: GOAL → BUDGET
Customer goal is: {goal_text}

No fixed budget — calculate the required budget based on competitor spend efficiency data.

## Competitor Spend & KPI Data
{data_context}

## Reference Information
{expert_knowledge}

## CRITICAL: BUDGET CALCULATION RULES

### Rule 1: Handle Negative Sector KPI Trends
If ALL competitors show NEGATIVE KPI uplift (sector-wide decline), do NOT extrapolate that more spend = positive uplift.
Instead reason like this:
- "Sector shows awareness decline of approximately X pp over the period despite heavy spending."
- "To achieve +Ypp uplift, the brand needs to reverse a sector trend, which requires strategic differentiation."
- "Based on sector average spend levels and customer scale, a realistic budget to defend and grow against this trend is approximately €Z."

### Rule 2: Anchor Budget to Customer's Historical Spend
The customer ({customer_name}) has historical spend of approximately {customer_historical_spend_formatted}.
- Use the CUSTOMER's own historical spend as the baseline anchor — NOT the sector leader's spend.
- A realistic budget recommendation should be between 0.5x and 5x their historical spend.
- Only exceed 5x if there is strong data evidence to justify it (e.g., dramatic market entry or category expansion).

### Rule 3: Sanity Check
If your calculated budget exceeds 10x the customer's historical spend, you MUST:
- Add a warning: "Calculated budget significantly exceeds historical spend benchmarks for this brand — treat as indicative only."
- Consider whether a more conservative estimate is appropriate.

### Rule 4: Calculation Logic
1. Calculate sector average KPI change (e.g., average of all competitor KPI changes)
2. Note that achieving POSITIVE uplift in a declining sector requires outperforming competitors
3. Use customer historical spend as baseline: recommend 1x-3x for moderate goals, 3x-5x for aggressive goals
4. Channel allocation percentages should mirror the most efficient competitor's channel mix

## Your Task
Calculate the required budget and recommend channel allocation for {customer_name} to achieve: {goal_text}

MANDATORY REQUIREMENTS:
1. Acknowledge if sector shows overall negative KPI trend
2. Anchor budget calculation to customer's historical spend level
3. Each channel rationale MUST name competitors and their spend amounts
4. Explain why achieving positive uplift in this sector is challenging (if applicable)
5. The summary MUST state the budget relative to customer's historical spend

Return JSON:
```json
{{
  "allocations": [
    {{"channel": "CHANNEL_NAME", "percentage": 35.0, "amount": 350000.00, "rationale": "[Competitor] spent €[X]M on [channel] with [Y]pp KPI change. Based on their spend ratio, allocating [Z]%."}},
    ...
  ],
  "total_percentage": 100.0,
  "total_budget_eur": 1500000.00,
  "kpi_projection": 3.0,
  "summary": "Sector shows [X]pp average decline. Customer historical spend is €Y. To achieve +Zpp against this trend, recommending budget of €W (approximately Nx historical spend). Using [Competitor] channel mix as benchmark.",
  "confidence": 0.85,
  "warnings": ["any data quality issues", "add warning if budget exceeds 10x historical spend"]
}}
```

{additional_context}
"""

    def __init__(self, session: AsyncSession):
        self.session = session
        # PRISMA-ONLY MODE: Don't initialize repos for tables that don't exist
        # self.knowledge_repo = ExpertKnowledgeRepository(session)
        # self.guardrails_repo = PromptGuardrailsRepository(session)
        self.data_service = DataFilteringService(session)

    def _is_budget_to_impact_mode(self, input_params: PromptAssemblyInput) -> bool:
        """Determine if we're in Budget→Impact mode.

        Budget→Impact: User has a fixed budget
        Goal→Budget: User has a goal but no fixed budget
        """
        # If we have a budget, it's Budget→Impact mode
        if input_params.total_budget and input_params.total_budget > 0:
            return True

        # Check goal_direction explicitly
        if input_params.goal_direction:
            direction = input_params.goal_direction.lower()
            if direction == "budget_to_impact":
                return True
            if direction == "goal_to_budget":
                return False

        # Default: if no budget, it's Goal→Budget mode
        return False

    async def assemble_prompt(
        self,
        input_params: PromptAssemblyInput,
        wirtschaftsgruppe: str,
        year: Optional[int] = None,
    ) -> AssembledPrompt:
        """Assemble the complete prompt for LLM.

        Args:
            input_params: User input and competitor information
            wirtschaftsgruppe: Industry classification for data lookup
            year: Year for data analysis

        Returns:
            AssembledPrompt with all components
        """
        # Get filtered data context
        data_result = await self.data_service.build_data_context(
            nielsen_brands=input_params.nielsen_brands,
            yougov_brands=input_params.yougov_brands,
            wirtschaftsgruppe=wirtschaftsgruppe,
            kpi_name=input_params.brand_kpi,
            year=year,
        )
        data_context = self.data_service.format_for_prompt(data_result)

        # Get expert knowledge
        expert_knowledge = await self._get_expert_knowledge()

        # Get guardrails
        guardrails = await self._get_guardrails()

        # Build system prompt
        system_prompt = self.SYSTEM_PROMPT_TEMPLATE.format(
            guardrails=guardrails,
        )

        # Build user prompt based on mode
        is_budget_mode = self._is_budget_to_impact_mode(input_params)
        user_prompt = self._build_user_prompt(input_params, data_context, expert_knowledge, is_budget_mode)

        # Metadata for tracing
        metadata = {
            "customer_name": input_params.customer_name,
            "industry": input_params.industry,
            "brand_kpi": input_params.brand_kpi,
            "total_budget": str(input_params.total_budget) if input_params.total_budget else None,
            "goal_direction": input_params.goal_direction,
            "goal_text": input_params.goal_text,
            "mode": "budget_to_impact" if is_budget_mode else "goal_to_budget",
            "chat_preferences_applied": bool(input_params.chat_preferences),
            "year": data_result.year,
            "num_competitors": len(data_result.competitor_kpi_profiles),
            "num_kpi_profiles": len(data_result.competitor_kpi_profiles),
            "channels_available": len(data_result.all_channels),
            "relationship_table_rows": len(data_result.relationship_table) if data_result.relationship_table else 0,
            "kpi_uplifts_count": len(data_result.kpi_uplifts) if data_result.kpi_uplifts else 0,
            "data_warnings": data_result.warnings if data_result.warnings else [],
            "assembled_at": datetime.utcnow().isoformat(),
        }

        return AssembledPrompt(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            data_context=data_context,
            expert_knowledge=expert_knowledge,
            guardrails=guardrails,
            metadata=metadata,
        )

    async def _get_expert_knowledge(self) -> str:
        """Load and combine all active expert knowledge.

        In Prisma-only mode (no Python tables), always returns defaults.
        """
        # Skip database query entirely - use defaults
        # The expert_knowledge table doesn't exist in Prisma-only mode
        return self._get_default_expert_knowledge()

    async def _get_guardrails(self) -> str:
        """Load and combine all active guardrails.

        In Prisma-only mode (no Python tables), always returns defaults.
        """
        # Skip database query entirely - use defaults
        # The prompt_guardrails table doesn't exist in Prisma-only mode
        return self._get_default_guardrails()

    def _build_user_prompt(
        self,
        input_params: PromptAssemblyInput,
        data_context: str,
        expert_knowledge: str,
        is_budget_mode: bool,
    ) -> str:
        """Build the user prompt from template based on mode."""
        # Time period line
        time_period_line = ""
        if input_params.time_period_start and input_params.time_period_end:
            start = input_params.time_period_start.strftime("%Y-%m")
            end = input_params.time_period_end.strftime("%Y-%m")
            time_period_line = f"- **Time Period**: {start} to {end}"

        # Channels line
        channels_line = ""
        if input_params.channels:
            channels_line = f"- **Target Channels**: {', '.join(input_params.channels)}"

        # Additional context
        additional_context = ""
        if input_params.additional_context and is_budget_mode:
            # In budget mode, additional_context is extra info
            additional_context = f"\n## Additional Context\n{input_params.additional_context}"

        # Chat preferences section (appended in both modes when present)
        chat_preferences_section = ""
        if input_params.chat_preferences:
            chat_preferences_section = (
                "\n## User Preferences from Chat\n"
                "The user expressed the following allocation preferences in chat since the last run.\n"
                "Apply them when generating this allocation, while still respecting all allocation rules:\n"
                f"{input_params.chat_preferences}"
            )

        if is_budget_mode:
            # BUDGET → IMPACT mode
            total_budget = float(input_params.total_budget) if input_params.total_budget else 0
            return self.BUDGET_TO_IMPACT_TEMPLATE.format(
                customer_name=input_params.customer_name,
                industry=input_params.industry,
                brand_kpi=input_params.brand_kpi,
                total_budget=total_budget,
                time_period_line=time_period_line,
                channels_line=channels_line,
                data_context=data_context,
                expert_knowledge=expert_knowledge,
                additional_context=additional_context + chat_preferences_section,
            )
        else:
            # GOAL → BUDGET mode
            goal_text = input_params.goal_text or input_params.additional_context or "Improve brand KPI"

            # Format customer historical spend
            customer_spend = input_params.customer_historical_spend or 0
            if customer_spend > 0:
                customer_historical_spend_line = f"€{customer_spend:,.0f}"
                customer_historical_spend_formatted = f"€{customer_spend:,.0f}"
            else:
                customer_historical_spend_line = "Not available"
                customer_historical_spend_formatted = "unknown (use sector average as proxy)"

            return self.GOAL_TO_BUDGET_TEMPLATE.format(
                customer_name=input_params.customer_name,
                industry=input_params.industry,
                brand_kpi=input_params.brand_kpi,
                goal_text=goal_text,
                time_period_line=time_period_line,
                channels_line=channels_line,
                data_context=data_context,
                expert_knowledge=expert_knowledge,
                additional_context=chat_preferences_section,  # Goal text is already in {goal_text}
                customer_historical_spend_line=customer_historical_spend_line,
                customer_historical_spend_formatted=customer_historical_spend_formatted,
            )

    def _get_default_expert_knowledge(self) -> str:
        """Channel name mappings only - no generic advice."""
        return """### Channel Name Reference
- FERNSEHEN = TV
- ONLINE = Digital/Online
- PLAKAT = OOH/Outdoor/Billboards
- RADIO = Radio
- SOCIAL = Social Media
- ZEITUNGEN = Newspapers
- PUBLIKUMSZEITSCHRIFTEN = Consumer Magazines
- FACHZEITSCHRIFTEN = Trade Publications
- TRANSPORT MEDIA = Transit Advertising
- AT-RETAIL-MEDIA = Retail/POS Media
- KINO = Cinema
- AMBIENT MEDIA = Ambient/Experiential

### Interpreting KPI Changes
- Negative KPI changes (e.g., -1.21pp) are common due to market-wide trends
- The competitor with the SMALLEST decline (closest to 0) is most efficient
- Compare relative performance, not absolute values
"""

    def _get_default_guardrails(self) -> str:
        """Default guardrails when database is empty."""
        return """### Output Format
- Respond only in valid JSON format
- Channel percentages must sum to exactly 100%
- kpi_projection MUST be a numeric float, NEVER null

### Data-Driven Rationale Requirements
- EVERY channel rationale must name at least one competitor
- EVERY rationale must include a specific EUR spend amount from the data
- EVERY rationale must reference a KPI change (e.g., "-1.21pp")
- NO generic phrases like "broad reach", "precise targeting", "cost-effective"
- The summary MUST name the benchmark competitors used

### Value Constraints
- Minimum channel allocation: 5%
- Maximum single channel allocation: 60%
- Allocate to at least 3 different channels
- total_budget_eur must be a positive number, never 0 or null

### Validation
- Channel names should use the German names from the data (FERNSEHEN, ONLINE, etc.)
- Include warnings for any data gaps or quality issues
"""

    def estimate_token_count(self, prompt: AssembledPrompt) -> int:
        """Estimate token count for the assembled prompt.

        Uses a simple heuristic of ~4 characters per token.
        """
        total_chars = (
            len(prompt.system_prompt)
            + len(prompt.user_prompt)
        )
        return total_chars // 4

    async def get_prompt_preview(
        self,
        input_params: PromptAssemblyInput,
        wirtschaftsgruppe: str,
    ) -> Dict[str, Any]:
        """Get a preview of the prompt without full assembly.

        Useful for debugging and validation.
        """
        prompt = await self.assemble_prompt(input_params, wirtschaftsgruppe)

        return {
            "system_prompt_length": len(prompt.system_prompt),
            "user_prompt_length": len(prompt.user_prompt),
            "estimated_tokens": self.estimate_token_count(prompt),
            "metadata": prompt.metadata,
            "preview": {
                "system_prompt_start": prompt.system_prompt[:500] + "...",
                "user_prompt_start": prompt.user_prompt[:500] + "...",
            },
        }
