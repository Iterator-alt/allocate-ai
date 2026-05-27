"""Stage 2 (Part 2): Prompt Assembly Service.

Assembles the final LLM prompt from:
1. Filtered competitor data (from DataFilteringService)
2. Expert knowledge (from database)
3. Guardrails (from database)
4. User input parameters
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import List, Optional, Dict, Any
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from src.services.mediamix.data_filtering import DataFilteringService, DataFilteringResult

# NOTE: ExpertKnowledgeRepository and PromptGuardrailsRepository are not used
# in Prisma-only mode as the tables don't exist. Default values are used instead.


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


class PromptAssemblyService:
    """Assembles complete prompts for LLM budget allocation.

    Combines competitor data, expert knowledge, and guardrails
    into a structured prompt format.
    """

    # Default system prompt template
    SYSTEM_PROMPT_TEMPLATE = """You are an expert media planner specializing in budget allocation optimization.

Your task is to analyze competitor advertising data and recommend a channel allocation strategy that will optimize the target KPI metric.

You must:
1. Analyze the competitor spend patterns
2. Consider industry benchmarks
3. Apply media planning best practices
4. Recommend a channel allocation that sums to 100%

Your response must be in valid JSON format following the specified schema.

{guardrails}
"""

    # Default user prompt template
    USER_PROMPT_TEMPLATE = """## Client Information
- **Client**: {customer_name}
- **Industry**: {industry}
- **Target KPI**: {brand_kpi}
{budget_line}
{time_period_line}
{channels_line}

## Competitor Analysis Data
{data_context}

## Expert Knowledge
{expert_knowledge}

## Your Task
Based on the competitor data and expert knowledge above, recommend an optimal channel allocation strategy for {customer_name}.

Provide your recommendation in the following JSON format:
```json
{{
  "allocations": [
    {{"channel": "channel_name", "percentage": 35.0, "amount": 350000.00, "rationale": "brief explanation"}},
    ...
  ],
  "total_percentage": 100.0,
  "summary": "Brief 2-3 sentence summary of the strategy",
  "confidence": 0.85,
  "warnings": ["any data quality or coverage warnings"]
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

        # Build user prompt
        user_prompt = self._build_user_prompt(input_params, data_context, expert_knowledge)

        # Metadata for tracing
        metadata = {
            "customer_name": input_params.customer_name,
            "industry": input_params.industry,
            "brand_kpi": input_params.brand_kpi,
            "total_budget": str(input_params.total_budget) if input_params.total_budget else None,
            "year": data_result.year,
            "num_competitors": len(data_result.competitor_spend_profiles),
            "num_kpi_profiles": len(data_result.competitor_kpi_profiles),
            "channels_available": len(data_result.all_channels),
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
    ) -> str:
        """Build the user prompt from template."""
        # Budget line
        budget_line = ""
        if input_params.total_budget:
            budget_line = f"- **Total Budget**: €{input_params.total_budget:,.2f}"

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
        if input_params.additional_context:
            additional_context = f"\n## Additional Context\n{input_params.additional_context}"

        return self.USER_PROMPT_TEMPLATE.format(
            customer_name=input_params.customer_name,
            industry=input_params.industry,
            brand_kpi=input_params.brand_kpi,
            budget_line=budget_line,
            time_period_line=time_period_line,
            channels_line=channels_line,
            data_context=data_context,
            expert_knowledge=expert_knowledge,
            additional_context=additional_context,
        )

    def _get_default_expert_knowledge(self) -> str:
        """Default expert knowledge when database is empty."""
        return """### Channel Heuristics
- TV provides broad reach but lower targeting precision
- Digital channels offer precise targeting and measurable ROI
- Print works well for B2B and luxury segments
- Radio is cost-effective for local reach
- Out-of-home (OOH) drives brand awareness in urban areas

### Budget Rules
- Diversify across at least 3-4 channels for optimal reach
- Allocate more to channels with proven ROI in your industry
- Consider seasonality in channel effectiveness
- Reserve 10-20% for emerging/testing channels

### KPI Optimization
- For ad awareness (adaware): prioritize high-reach channels (TV, OOH)
- For aided recall (aided): focus on frequency and repetition
- For consideration (consider): emphasize targeted digital and content marketing
"""

    def _get_default_guardrails(self) -> str:
        """Default guardrails when database is empty."""
        return """### Output Format
- Respond only in valid JSON format
- Channel percentages must sum to exactly 100%
- Include a brief rationale for each channel allocation
- Confidence score must be between 0 and 1

### Value Constraints
- Minimum channel allocation: 5%
- Maximum single channel allocation: 50%
- Allocate to at least 3 different channels
- Do not exceed the total budget if specified

### Validation Rules
- All numeric values must be positive
- Channel names must match the available channels in the data
- Include warnings for any data quality issues observed
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
