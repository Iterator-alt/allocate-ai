"""Unit tests for Prompt Management Repositories."""

from datetime import datetime

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import ExpertKnowledge, PromptGuardrails, PromptTrace, Run, RunStatus
from src.repositories import (
    ExpertKnowledgeRepository,
    PromptGuardrailsRepository,
    PromptTraceRepository,
)


class TestExpertKnowledgeRepository:
    """Tests for ExpertKnowledgeRepository."""

    @pytest_asyncio.fixture
    async def repo(self, db_session: AsyncSession) -> ExpertKnowledgeRepository:
        return ExpertKnowledgeRepository(db_session)

    @pytest_asyncio.fixture
    async def sample_knowledge(self, db_session: AsyncSession):
        """Create sample expert knowledge records."""
        records = [
            ExpertKnowledge(
                category="channel_heuristics",
                version=1,
                content="TV is effective for brand awareness.",
                is_active=False,
            ),
            ExpertKnowledge(
                category="channel_heuristics",
                version=2,
                content="TV and Digital are effective for brand awareness.",
                is_active=True,
            ),
            ExpertKnowledge(
                category="budget_rules",
                version=1,
                content="Diversify across at least 3 channels.",
                is_active=True,
            ),
        ]
        for r in records:
            db_session.add(r)
        await db_session.commit()
        return records

    async def test_get_active_by_category(
        self, repo: ExpertKnowledgeRepository, sample_knowledge, db_session: AsyncSession
    ):
        """Test getting active knowledge by category."""
        result = await repo.get_active_by_category("channel_heuristics")

        assert result is not None
        assert result.version == 2
        assert result.is_active is True
        assert "Digital" in result.content

    async def test_get_all_active(
        self, repo: ExpertKnowledgeRepository, sample_knowledge, db_session: AsyncSession
    ):
        """Test getting all active knowledge."""
        results = await repo.get_all_active()

        assert len(results) == 2
        categories = {r.category for r in results}
        assert "channel_heuristics" in categories
        assert "budget_rules" in categories

    async def test_get_categories(
        self, repo: ExpertKnowledgeRepository, sample_knowledge, db_session: AsyncSession
    ):
        """Test getting all categories."""
        categories = await repo.get_categories()

        assert "channel_heuristics" in categories
        assert "budget_rules" in categories

    async def test_get_version_history(
        self, repo: ExpertKnowledgeRepository, sample_knowledge, db_session: AsyncSession
    ):
        """Test getting version history."""
        history = await repo.get_version_history("channel_heuristics")

        assert len(history) == 2
        assert history[0].version == 2  # Most recent first
        assert history[1].version == 1

    async def test_create_version(
        self, repo: ExpertKnowledgeRepository, sample_knowledge, db_session: AsyncSession
    ):
        """Test creating a new version."""
        new = await repo.create_version(
            category="channel_heuristics",
            content="New content for version 3",
            change_notes="Added new insights",
        )
        await db_session.commit()

        assert new.version == 3
        assert new.is_active is True

        # Previous active should be deactivated
        old = await repo.get_by_version("channel_heuristics", 2)
        assert old.is_active is False


class TestPromptGuardrailsRepository:
    """Tests for PromptGuardrailsRepository."""

    @pytest_asyncio.fixture
    async def repo(self, db_session: AsyncSession) -> PromptGuardrailsRepository:
        return PromptGuardrailsRepository(db_session)

    @pytest_asyncio.fixture
    async def sample_guardrails(self, db_session: AsyncSession):
        """Create sample guardrail records."""
        records = [
            PromptGuardrails(
                guardrail_type="output_format",
                version=1,
                content="Respond in JSON format.",
                is_active=True,
            ),
            PromptGuardrails(
                guardrail_type="value_constraints",
                version=1,
                content="Percentages must sum to 100%.",
                is_active=True,
            ),
        ]
        for r in records:
            db_session.add(r)
        await db_session.commit()
        return records

    async def test_get_active_by_type(
        self, repo: PromptGuardrailsRepository, sample_guardrails, db_session: AsyncSession
    ):
        """Test getting active guardrail by type."""
        result = await repo.get_active_by_type("output_format")

        assert result is not None
        assert result.is_active is True
        assert "JSON" in result.content

    async def test_get_all_active(
        self, repo: PromptGuardrailsRepository, sample_guardrails, db_session: AsyncSession
    ):
        """Test getting all active guardrails."""
        results = await repo.get_all_active()

        assert len(results) == 2
        types = {r.guardrail_type for r in results}
        assert "output_format" in types
        assert "value_constraints" in types

    async def test_get_guardrail_types(
        self, repo: PromptGuardrailsRepository, sample_guardrails, db_session: AsyncSession
    ):
        """Test getting all guardrail types."""
        types = await repo.get_guardrail_types()

        assert "output_format" in types
        assert "value_constraints" in types

    async def test_create_version(
        self, repo: PromptGuardrailsRepository, sample_guardrails, db_session: AsyncSession
    ):
        """Test creating a new guardrail version."""
        new = await repo.create_version(
            guardrail_type="output_format",
            content="Respond in strict JSON format.",
            change_notes="Stricter formatting requirements",
        )
        await db_session.commit()

        assert new.version == 2
        assert new.is_active is True


class TestPromptTraceRepository:
    """Tests for PromptTraceRepository."""

    @pytest_asyncio.fixture
    async def repo(self, db_session: AsyncSession) -> PromptTraceRepository:
        return PromptTraceRepository(db_session)

    @pytest_asyncio.fixture
    async def sample_run(self, db_session: AsyncSession):
        """Create a sample run."""
        run = Run(
            session_token="test-session",
            customer_name="Test",
            industry="PKW",
            brand_kpi="adaware",
            status=RunStatus.COMPLETED.value,
        )
        db_session.add(run)
        await db_session.commit()
        return run

    @pytest_asyncio.fixture
    async def sample_traces(self, db_session: AsyncSession, sample_run):
        """Create sample trace records."""
        traces = [
            PromptTrace(
                run_id=sample_run.id,
                called_at=datetime.utcnow(),
                model="gpt-4o",
                prompt="Test prompt 1",
                response="Test response 1",
                prompt_tokens=100,
                completion_tokens=50,
                total_tokens=150,
                latency_ms=1500,
                status="success",
            ),
            PromptTrace(
                run_id=sample_run.id,
                called_at=datetime.utcnow(),
                model="gpt-4o",
                prompt="Test prompt 2",
                response=None,
                status="error",
                error_message="Timeout",
            ),
        ]
        for t in traces:
            db_session.add(t)
        await db_session.commit()
        return traces

    async def test_get_by_run(
        self, repo: PromptTraceRepository, sample_traces, sample_run, db_session: AsyncSession
    ):
        """Test getting traces by run ID."""
        traces = await repo.get_by_run(sample_run.id)

        assert len(traces) == 2

    async def test_get_latest_by_run(
        self, repo: PromptTraceRepository, sample_traces, sample_run, db_session: AsyncSession
    ):
        """Test getting latest trace for a run."""
        latest = await repo.get_latest_by_run(sample_run.id)

        assert latest is not None
        # Should be the most recent one
        assert latest.prompt in ["Test prompt 1", "Test prompt 2"]

    async def test_get_failed_traces(
        self, repo: PromptTraceRepository, sample_traces, db_session: AsyncSession
    ):
        """Test getting failed traces."""
        failed = await repo.get_failed_traces()

        assert len(failed) == 1
        assert failed[0].status == "error"
        assert failed[0].error_message == "Timeout"

    async def test_get_usage_stats(
        self, repo: PromptTraceRepository, sample_traces, sample_run, db_session: AsyncSession
    ):
        """Test getting usage statistics."""
        stats = await repo.get_usage_stats(run_id=sample_run.id)

        assert stats["total_calls"] == 2
        assert stats["total_prompt_tokens"] == 100  # Only from successful trace
        assert stats["total_completion_tokens"] == 50
        assert stats["total_tokens"] == 150

    async def test_get_usage_stats_all(
        self, repo: PromptTraceRepository, sample_traces, db_session: AsyncSession
    ):
        """Test getting usage stats without run filter."""
        stats = await repo.get_usage_stats()

        assert stats["total_calls"] == 2
