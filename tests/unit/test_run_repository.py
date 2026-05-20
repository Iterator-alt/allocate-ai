"""Tests for Run repository."""

from decimal import Decimal
from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Run, User
from src.db.models.run import RunStatus
from src.repositories import RunRepository, AllocationResultRepository, ChatHistoryRepository


class TestRunRepository:
    """Tests for RunRepository."""

    @pytest_asyncio.fixture
    async def repo(self, db_session: AsyncSession) -> RunRepository:
        return RunRepository(db_session)

    @pytest_asyncio.fixture
    async def user(self, db_session: AsyncSession) -> User:
        """Create a test user."""
        user = User(email="test@example.com", name="Test User", role="user")
        db_session.add(user)
        await db_session.flush()
        return user

    async def test_create_run(self, repo: RunRepository, db_session: AsyncSession):
        """Test creating a new run."""
        run = await repo.create_run(
            session_token="test-session-123",
            customer_name="BMW",
            industry="Automotive",
            brand_kpi="adaware",
            total_budget=1000000.0,
        )

        assert run.id is not None
        assert run.session_token == "test-session-123"
        assert run.customer_name == "BMW"
        assert run.industry == "Automotive"
        assert run.brand_kpi == "adaware"
        assert run.status == RunStatus.PENDING.value
        assert run.input_hash is not None

    async def test_create_run_with_user(
        self, repo: RunRepository, user: User, db_session: AsyncSession
    ):
        """Test creating a run linked to a user."""
        run = await repo.create_run(
            session_token="test-session-456",
            customer_name="Mercedes",
            industry="Automotive",
            brand_kpi="aided",
            user_id=user.id,
        )

        assert run.user_id == user.id

    async def test_get_by_session(self, repo: RunRepository, db_session: AsyncSession):
        """Test getting runs by session token."""
        # Create multiple runs
        await repo.create_run(
            session_token="session-A",
            customer_name="BMW",
            industry="Automotive",
            brand_kpi="adaware",
        )
        await repo.create_run(
            session_token="session-A",
            customer_name="Audi",
            industry="Automotive",
            brand_kpi="aided",
        )
        await repo.create_run(
            session_token="session-B",
            customer_name="Mercedes",
            industry="Automotive",
            brand_kpi="consider",
        )

        runs = await repo.get_by_session("session-A")
        assert len(runs) == 2
        assert all(r.session_token == "session-A" for r in runs)

    async def test_get_active_run_for_session(
        self, repo: RunRepository, db_session: AsyncSession
    ):
        """Test getting active (non-terminal) run for session."""
        # Create a pending run
        run = await repo.create_run(
            session_token="active-session",
            customer_name="BMW",
            industry="Automotive",
            brand_kpi="adaware",
        )

        active_run = await repo.get_active_run_for_session("active-session")
        assert active_run is not None
        assert active_run.id == run.id

    async def test_no_active_run_after_completion(
        self, repo: RunRepository, db_session: AsyncSession
    ):
        """Test that completed run is not returned as active."""
        run = await repo.create_run(
            session_token="completed-session",
            customer_name="BMW",
            industry="Automotive",
            brand_kpi="adaware",
        )

        # Mark as completed
        await repo.update_status(run.id, RunStatus.COMPLETED)

        active_run = await repo.get_active_run_for_session("completed-session")
        assert active_run is None

    async def test_update_status(self, repo: RunRepository, db_session: AsyncSession):
        """Test updating run status."""
        run = await repo.create_run(
            session_token="status-session",
            customer_name="BMW",
            industry="Automotive",
            brand_kpi="adaware",
        )

        # Update to matching
        updated = await repo.update_status(run.id, RunStatus.MATCHING)
        assert updated.status == RunStatus.MATCHING.value
        assert updated.started_at is not None

        # Update to completed
        updated = await repo.update_status(run.id, RunStatus.COMPLETED)
        assert updated.status == RunStatus.COMPLETED.value
        assert updated.completed_at is not None

    async def test_mark_cancelled(self, repo: RunRepository, db_session: AsyncSession):
        """Test marking a run as cancelled."""
        run = await repo.create_run(
            session_token="cancel-session",
            customer_name="BMW",
            industry="Automotive",
            brand_kpi="adaware",
        )

        cancelled = await repo.mark_cancelled(run.id, "User requested cancellation")
        assert cancelled.status == RunStatus.CANCELLED.value
        assert cancelled.error_message == "User requested cancellation"
        assert cancelled.completed_at is not None

    async def test_input_hash_consistency(
        self, repo: RunRepository, db_session: AsyncSession
    ):
        """Test that same inputs produce same hash."""
        run1 = await repo.create_run(
            session_token="hash-session-1",
            customer_name="BMW",
            industry="Automotive",
            brand_kpi="adaware",
            total_budget=1000000.0,
        )

        run2 = await repo.create_run(
            session_token="hash-session-2",
            customer_name="BMW",
            industry="Automotive",
            brand_kpi="adaware",
            total_budget=1000000.0,
        )

        assert run1.input_hash == run2.input_hash

    async def test_input_hash_difference(
        self, repo: RunRepository, db_session: AsyncSession
    ):
        """Test that different inputs produce different hashes."""
        run1 = await repo.create_run(
            session_token="hash-diff-1",
            customer_name="BMW",
            industry="Automotive",
            brand_kpi="adaware",
        )

        run2 = await repo.create_run(
            session_token="hash-diff-2",
            customer_name="BMW",
            industry="Automotive",
            brand_kpi="aided",  # Different KPI
        )

        assert run1.input_hash != run2.input_hash

    async def test_find_cached_result(
        self, repo: RunRepository, db_session: AsyncSession
    ):
        """Test finding cached result by input hash."""
        # Create and complete a run
        run = await repo.create_run(
            session_token="cache-session",
            customer_name="BMW",
            industry="Automotive",
            brand_kpi="adaware",
        )
        await repo.update_status(run.id, RunStatus.COMPLETED)

        # Find by hash
        cached = await repo.find_cached_result(run.input_hash)
        assert cached is not None
        assert cached.id == run.id

    async def test_set_confirmed_competitors(
        self, repo: RunRepository, db_session: AsyncSession
    ):
        """Test storing confirmed competitor set."""
        run = await repo.create_run(
            session_token="competitors-session",
            customer_name="BMW",
            industry="Automotive",
            brand_kpi="adaware",
        )

        competitors = ["Mercedes", "Audi", "Volkswagen"]
        updated = await repo.set_confirmed_competitors(run.id, competitors)

        assert updated.confirmed_competitors == {"brands": competitors}
        assert updated.status == RunStatus.GENERATING.value


class TestAllocationResultRepository:
    """Tests for AllocationResultRepository."""

    @pytest_asyncio.fixture
    async def run_repo(self, db_session: AsyncSession) -> RunRepository:
        return RunRepository(db_session)

    @pytest_asyncio.fixture
    async def result_repo(self, db_session: AsyncSession) -> AllocationResultRepository:
        return AllocationResultRepository(db_session)

    @pytest_asyncio.fixture
    async def run(self, run_repo: RunRepository) -> Run:
        """Create a test run."""
        return await run_repo.create_run(
            session_token="result-session",
            customer_name="BMW",
            industry="Automotive",
            brand_kpi="adaware",
        )

    async def test_create_result(
        self,
        result_repo: AllocationResultRepository,
        run: Run,
        db_session: AsyncSession,
    ):
        """Test creating an allocation result."""
        allocations = {
            "channels": [
                {"channel": "TV", "share_pct": 40.0, "budget_gross_eur": 400000},
                {"channel": "Digital", "share_pct": 35.0, "budget_gross_eur": 350000},
                {"channel": "Print", "share_pct": 25.0, "budget_gross_eur": 250000},
            ]
        }

        result = await result_repo.create_result(
            run_id=run.id,
            allocations=allocations,
            summary="Budget allocated across three channels.",
            confidence_score=0.85,
        )

        assert result.id is not None
        assert result.run_id == run.id
        assert result.allocations == allocations
        assert float(result.confidence_score) == 0.85

    async def test_get_by_run_id(
        self,
        result_repo: AllocationResultRepository,
        run: Run,
        db_session: AsyncSession,
    ):
        """Test retrieving result by run ID."""
        await result_repo.create_result(
            run_id=run.id,
            allocations={"channels": []},
        )

        result = await result_repo.get_by_run_id(run.id)
        assert result is not None
        assert result.run_id == run.id


class TestChatHistoryRepository:
    """Tests for ChatHistoryRepository."""

    @pytest_asyncio.fixture
    async def run_repo(self, db_session: AsyncSession) -> RunRepository:
        return RunRepository(db_session)

    @pytest_asyncio.fixture
    async def chat_repo(self, db_session: AsyncSession) -> ChatHistoryRepository:
        return ChatHistoryRepository(db_session)

    @pytest_asyncio.fixture
    async def run(self, run_repo: RunRepository) -> Run:
        """Create a test run."""
        return await run_repo.create_run(
            session_token="chat-session",
            customer_name="BMW",
            industry="Automotive",
            brand_kpi="adaware",
        )

    async def test_add_warning(
        self,
        chat_repo: ChatHistoryRepository,
        run: Run,
        db_session: AsyncSession,
    ):
        """Test adding a warning message."""
        warning = await chat_repo.add_warning(
            run_id=run.id,
            title="Low Data Confidence",
            content="Limited historical data available.",
        )

        assert warning.id is not None
        assert warning.message_type == "warning"
        assert warning.severity == "warning"

    async def test_add_alert(
        self,
        chat_repo: ChatHistoryRepository,
        run: Run,
        db_session: AsyncSession,
    ):
        """Test adding an alert message."""
        alert = await chat_repo.add_alert(
            run_id=run.id,
            title="Competitor Gap Detected",
            content="Missing spend data for key competitor.",
        )

        assert alert.message_type == "alert"
        assert alert.severity == "error"

    async def test_add_summary(
        self,
        chat_repo: ChatHistoryRepository,
        run: Run,
        db_session: AsyncSession,
    ):
        """Test adding a summary message."""
        summary = await chat_repo.add_summary(
            run_id=run.id,
            title="Allocation Summary",
            content="Budget has been optimally distributed.",
        )

        assert summary.message_type == "summary"
        assert summary.severity == "info"

    async def test_get_by_run_id(
        self,
        chat_repo: ChatHistoryRepository,
        run: Run,
        db_session: AsyncSession,
    ):
        """Test retrieving messages by run ID."""
        await chat_repo.add_warning(run.id, "Warning 1", "Content 1")
        await chat_repo.add_alert(run.id, "Alert 1", "Content 2")
        await chat_repo.add_summary(run.id, "Summary", "Content 3")

        messages = await chat_repo.get_by_run_id(run.id)
        assert len(messages) == 3

    async def test_display_order(
        self,
        chat_repo: ChatHistoryRepository,
        run: Run,
        db_session: AsyncSession,
    ):
        """Test that messages are ordered by display_order."""
        await chat_repo.add_warning(run.id, "First", "Content")
        await chat_repo.add_alert(run.id, "Second", "Content")
        await chat_repo.add_summary(run.id, "Third", "Content")

        messages = await chat_repo.get_by_run_id(run.id)
        assert messages[0].title == "First"
        assert messages[1].title == "Second"
        assert messages[2].title == "Third"
