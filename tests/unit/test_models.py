"""Tests for database models."""

from decimal import Decimal
from datetime import datetime

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import (
    NielsenSpend,
    YouGovKPI,
    IndustryMap,
    BrandMap,
    Run,
    AllocationResult,
    ChatHistory,
    User,
    Project,
    ExpertKnowledge,
    PromptGuardrails,
)
from src.db.models.run import RunStatus


class TestNielsenSpend:
    """Tests for NielsenSpend model."""

    async def test_create_nielsen_spend(self, db_session: AsyncSession):
        """Test creating a NielsenSpend record."""
        record = NielsenSpend(
            brand_name="BMW",
            wirtschaftsgruppe="Automotive",
            year=2023,
            month=6,
            channel="TV",
            spend_eur=Decimal("150000.00"),
            source_file="nielsen_2023.xlsx",
        )
        db_session.add(record)
        await db_session.flush()

        assert record.id is not None
        assert record.brand_name == "BMW"
        assert record.spend_eur == Decimal("150000.00")


class TestYouGovKPI:
    """Tests for YouGovKPI model."""

    async def test_create_yougov_kpi(self, db_session: AsyncSession):
        """Test creating a YouGovKPI record."""
        record = YouGovKPI(
            brand_label="BMW",
            sector="Automotive",
            year=2023,
            month=6,
            adaware=Decimal("45.50"),
            aided=Decimal("78.20"),
            consider=Decimal("32.10"),
        )
        db_session.add(record)
        await db_session.flush()

        assert record.id is not None
        assert record.adaware == Decimal("45.50")

    async def test_nullable_kpi_values(self, db_session: AsyncSession):
        """Test that KPI values can be null."""
        record = YouGovKPI(
            brand_label="NewBrand",
            sector="Tech",
            year=2023,
            month=1,
            adaware=Decimal("30.00"),
            aided=None,
            consider=None,
        )
        db_session.add(record)
        await db_session.flush()

        assert record.aided is None
        assert record.consider is None


class TestMappingModels:
    """Tests for mapping models."""

    async def test_create_industry_map(self, db_session: AsyncSession):
        """Test creating an IndustryMap record."""
        record = IndustryMap(
            wirtschaftsgruppe="PKW",
            sector_label="Automotive",
            description="Personal vehicles",
            is_active=True,
        )
        db_session.add(record)
        await db_session.flush()

        assert record.id is not None
        assert record.is_active is True

    async def test_create_brand_map(self, db_session: AsyncSession):
        """Test creating a BrandMap record."""
        record = BrandMap(
            nielsen_brand="BMW AG",
            yougov_brand_label="BMW",
            wirtschaftsgruppe="Automotive",
            confidence=0.95,
            is_active=True,
        )
        db_session.add(record)
        await db_session.flush()

        assert record.id is not None
        assert record.confidence == 0.95


class TestRunModels:
    """Tests for run-related models."""

    @pytest_asyncio.fixture
    async def user(self, db_session: AsyncSession) -> User:
        """Create a test user."""
        user = User(email="test@example.com", name="Test User", role="user")
        db_session.add(user)
        await db_session.flush()
        return user

    @pytest_asyncio.fixture
    async def project(self, db_session: AsyncSession, user: User) -> Project:
        """Create a test project."""
        project = Project(
            name="Test Project",
            customer_name="BMW",
            industry="Automotive",
            owner_id=user.id,
        )
        db_session.add(project)
        await db_session.flush()
        return project

    async def test_create_run(self, db_session: AsyncSession, user: User, project: Project):
        """Test creating a Run record."""
        run = Run(
            session_token="test-session-123",
            user_id=user.id,
            project_id=project.id,
            customer_name="BMW",
            industry="Automotive",
            brand_kpi="adaware",
            total_budget=Decimal("1000000.00"),
            status=RunStatus.PENDING.value,
        )
        db_session.add(run)
        await db_session.flush()

        assert run.id is not None
        assert run.status == "pending"

    async def test_run_status_values(self):
        """Test RunStatus enum values."""
        assert RunStatus.PENDING.value == "pending"
        assert RunStatus.MATCHING.value == "matching"
        assert RunStatus.GENERATING.value == "generating"
        assert RunStatus.COMPLETED.value == "completed"
        assert RunStatus.FAILED.value == "failed"

    async def test_create_allocation_result(self, db_session: AsyncSession, user: User, project: Project):
        """Test creating an AllocationResult record."""
        run = Run(
            session_token="test-session-456",
            user_id=user.id,
            customer_name="BMW",
            industry="Automotive",
            brand_kpi="adaware",
        )
        db_session.add(run)
        await db_session.flush()

        result = AllocationResult(
            run_id=run.id,
            allocations={
                "channels": [
                    {"name": "TV", "percentage": 40.0, "amount": 400000},
                    {"name": "Digital", "percentage": 35.0, "amount": 350000},
                    {"name": "Print", "percentage": 25.0, "amount": 250000},
                ]
            },
            summary="Budget allocated across three channels.",
            confidence_score=Decimal("0.85"),
            is_valid=True,
        )
        db_session.add(result)
        await db_session.flush()

        assert result.id is not None
        assert result.allocations["channels"][0]["name"] == "TV"

    async def test_create_chat_history(self, db_session: AsyncSession, user: User):
        """Test creating a ChatHistory record."""
        run = Run(
            session_token="test-session-789",
            user_id=user.id,
            customer_name="BMW",
            industry="Automotive",
            brand_kpi="adaware",
        )
        db_session.add(run)
        await db_session.flush()

        chat = ChatHistory(
            run_id=run.id,
            message_type="warning",
            severity="warning",
            title="Low Data Confidence",
            content="Limited historical data available for this brand.",
            display_order=1,
        )
        db_session.add(chat)
        await db_session.flush()

        assert chat.id is not None
        assert chat.severity == "warning"


class TestPromptModels:
    """Tests for prompt management models."""

    async def test_create_expert_knowledge(self, db_session: AsyncSession):
        """Test creating an ExpertKnowledge record."""
        record = ExpertKnowledge(
            version=1,
            category="channel_heuristics",
            content="TV advertising is most effective for brand awareness campaigns.",
            structured_content={"effectiveness": {"TV": 0.8, "Digital": 0.7}},
            is_active=True,
        )
        db_session.add(record)
        await db_session.flush()

        assert record.id is not None
        assert record.structured_content["effectiveness"]["TV"] == 0.8

    async def test_create_prompt_guardrails(self, db_session: AsyncSession):
        """Test creating a PromptGuardrails record."""
        record = PromptGuardrails(
            version=1,
            guardrail_type="output_format",
            content="Output must be valid JSON with allocations summing to 100%.",
            structured_rules={"sum_to_100": True, "min_channels": 2},
            is_active=True,
        )
        db_session.add(record)
        await db_session.flush()

        assert record.id is not None
        assert record.structured_rules["sum_to_100"] is True
