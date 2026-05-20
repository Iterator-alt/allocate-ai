"""Unit tests for Prompt Assembly Service."""

from decimal import Decimal
from datetime import datetime

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import (
    NielsenSpend,
    YouGovKPI,
    IndustryMap,
    ExpertKnowledge,
    PromptGuardrails,
)
from src.services.mediamix import PromptAssemblyService, PromptAssemblyInput


class TestPromptAssemblyService:
    """Tests for PromptAssemblyService."""

    @pytest_asyncio.fixture
    async def service(self, db_session: AsyncSession) -> PromptAssemblyService:
        return PromptAssemblyService(db_session)

    @pytest_asyncio.fixture
    async def sample_data(self, db_session: AsyncSession):
        """Create sample data for prompt assembly."""
        # Industry mapping
        db_session.add(IndustryMap(
            wirtschaftsgruppe="PKW",
            sector_label="Automotive",
            is_active=True
        ))

        # Nielsen data
        for month in range(1, 4):
            db_session.add(NielsenSpend(
                brand_name="BMW AG",
                wirtschaftsgruppe="PKW",
                year=2023,
                month=month,
                channel="TV",
                spend_eur=Decimal("100000")
            ))

        # YouGov data
        for month in range(1, 4):
            db_session.add(YouGovKPI(
                brand_label="BMW",
                sector="Automotive",
                year=2023,
                month=month,
                adaware=Decimal("45.0")
            ))

        # Expert knowledge
        db_session.add(ExpertKnowledge(
            category="channel_heuristics",
            version=1,
            content="TV is effective for brand awareness.",
            is_active=True
        ))
        db_session.add(ExpertKnowledge(
            category="budget_rules",
            version=1,
            content="Diversify across at least 3 channels.",
            is_active=True
        ))

        # Guardrails
        db_session.add(PromptGuardrails(
            guardrail_type="output_format",
            version=1,
            content="Respond only in valid JSON format.",
            is_active=True
        ))
        db_session.add(PromptGuardrails(
            guardrail_type="value_constraints",
            version=1,
            content="Percentages must sum to 100%.",
            is_active=True
        ))

        await db_session.commit()

    async def test_assemble_prompt_basic(
        self, service: PromptAssemblyService, sample_data, db_session: AsyncSession
    ):
        """Test basic prompt assembly."""
        input_params = PromptAssemblyInput(
            customer_name="Audi",
            industry="PKW",
            brand_kpi="adaware",
            total_budget=Decimal("1000000"),
            time_period_start=None,
            time_period_end=None,
            channels=None,
            nielsen_brands=["BMW AG"],
            yougov_brands=["BMW"],
        )

        result = await service.assemble_prompt(input_params, "PKW", year=2023)

        assert result.system_prompt is not None
        assert result.user_prompt is not None
        assert result.data_context is not None
        assert result.expert_knowledge is not None
        assert result.guardrails is not None
        assert result.metadata is not None

    async def test_prompt_contains_customer_info(
        self, service: PromptAssemblyService, sample_data, db_session: AsyncSession
    ):
        """Test that prompt contains customer information."""
        input_params = PromptAssemblyInput(
            customer_name="Audi",
            industry="PKW",
            brand_kpi="adaware",
            total_budget=Decimal("500000"),
            time_period_start=None,
            time_period_end=None,
            channels=["TV", "Digital"],
            nielsen_brands=["BMW AG"],
            yougov_brands=["BMW"],
        )

        result = await service.assemble_prompt(input_params, "PKW", year=2023)

        assert "Audi" in result.user_prompt
        assert "PKW" in result.user_prompt
        assert "adaware" in result.user_prompt
        assert "500,000" in result.user_prompt or "500000" in result.user_prompt

    async def test_prompt_contains_expert_knowledge(
        self, service: PromptAssemblyService, sample_data, db_session: AsyncSession
    ):
        """Test that prompt includes expert knowledge."""
        input_params = PromptAssemblyInput(
            customer_name="Audi",
            industry="PKW",
            brand_kpi="adaware",
            total_budget=None,
            time_period_start=None,
            time_period_end=None,
            channels=None,
            nielsen_brands=["BMW AG"],
            yougov_brands=["BMW"],
        )

        result = await service.assemble_prompt(input_params, "PKW", year=2023)

        assert "TV is effective" in result.expert_knowledge
        assert "Diversify" in result.expert_knowledge

    async def test_prompt_contains_guardrails(
        self, service: PromptAssemblyService, sample_data, db_session: AsyncSession
    ):
        """Test that prompt includes guardrails."""
        input_params = PromptAssemblyInput(
            customer_name="Audi",
            industry="PKW",
            brand_kpi="adaware",
            total_budget=None,
            time_period_start=None,
            time_period_end=None,
            channels=None,
            nielsen_brands=["BMW AG"],
            yougov_brands=["BMW"],
        )

        result = await service.assemble_prompt(input_params, "PKW", year=2023)

        assert "JSON" in result.guardrails
        assert "100%" in result.guardrails

    async def test_prompt_metadata(
        self, service: PromptAssemblyService, sample_data, db_session: AsyncSession
    ):
        """Test that metadata is correctly populated."""
        input_params = PromptAssemblyInput(
            customer_name="Audi",
            industry="PKW",
            brand_kpi="adaware",
            total_budget=Decimal("1000000"),
            time_period_start=None,
            time_period_end=None,
            channels=None,
            nielsen_brands=["BMW AG"],
            yougov_brands=["BMW"],
        )

        result = await service.assemble_prompt(input_params, "PKW", year=2023)

        assert result.metadata["customer_name"] == "Audi"
        assert result.metadata["industry"] == "PKW"
        assert result.metadata["brand_kpi"] == "adaware"
        assert result.metadata["year"] == 2023
        assert "assembled_at" in result.metadata

    async def test_estimate_token_count(
        self, service: PromptAssemblyService, sample_data, db_session: AsyncSession
    ):
        """Test token count estimation."""
        input_params = PromptAssemblyInput(
            customer_name="Audi",
            industry="PKW",
            brand_kpi="adaware",
            total_budget=None,
            time_period_start=None,
            time_period_end=None,
            channels=None,
            nielsen_brands=["BMW AG"],
            yougov_brands=["BMW"],
        )

        result = await service.assemble_prompt(input_params, "PKW", year=2023)
        token_count = service.estimate_token_count(result)

        # Should be a reasonable number
        assert token_count > 100
        assert token_count < 100000

    async def test_default_knowledge_when_empty(
        self, service: PromptAssemblyService, db_session: AsyncSession
    ):
        """Test that default knowledge is used when database is empty."""
        # Don't add any sample data
        db_session.add(IndustryMap(
            wirtschaftsgruppe="PKW",
            sector_label="Automotive",
            is_active=True
        ))
        await db_session.commit()

        input_params = PromptAssemblyInput(
            customer_name="Audi",
            industry="PKW",
            brand_kpi="adaware",
            total_budget=None,
            time_period_start=None,
            time_period_end=None,
            channels=None,
            nielsen_brands=[],
            yougov_brands=[],
        )

        result = await service.assemble_prompt(input_params, "PKW", year=2023)

        # Should contain default knowledge
        assert "Channel Heuristics" in result.expert_knowledge
        assert "Output Format" in result.guardrails

    async def test_prompt_preview(
        self, service: PromptAssemblyService, sample_data, db_session: AsyncSession
    ):
        """Test getting prompt preview."""
        input_params = PromptAssemblyInput(
            customer_name="Audi",
            industry="PKW",
            brand_kpi="adaware",
            total_budget=None,
            time_period_start=None,
            time_period_end=None,
            channels=None,
            nielsen_brands=["BMW AG"],
            yougov_brands=["BMW"],
        )

        preview = await service.get_prompt_preview(input_params, "PKW")

        assert "system_prompt_length" in preview
        assert "user_prompt_length" in preview
        assert "estimated_tokens" in preview
        assert "preview" in preview
