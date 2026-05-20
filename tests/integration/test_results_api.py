"""Integration tests for allocation result and chat history API endpoints."""

from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Run, AllocationResult, ChatHistory
from src.db.models.run import RunStatus


class TestGetAllocationResult:
    """Tests for GET /api/v1/runs/{id}/result endpoint."""

    @pytest_asyncio.fixture
    async def completed_run_with_result(self, db_session: AsyncSession):
        """Create a completed run with allocation result."""
        # Create run
        run = Run(
            session_token="test-result-session",
            customer_name="Test Brand",
            industry="Automotive",
            brand_kpi="adaware",
            total_budget=Decimal("1000000"),
            status=RunStatus.COMPLETED.value,
        )
        db_session.add(run)
        await db_session.flush()

        # Create allocation result
        result = AllocationResult(
            run_id=run.id,
            allocations={
                "channels": [
                    {"name": "TV", "percentage": 40.0, "amount": 400000, "rationale": "High reach"},
                    {"name": "Digital", "percentage": 35.0, "amount": 350000, "rationale": "Cost efficient"},
                    {"name": "Print", "percentage": 25.0, "amount": 250000, "rationale": "Brand building"},
                ],
                "total_percentage": 100.0,
            },
            summary="A balanced media mix for automotive brand",
            confidence_score=Decimal("0.85"),
            is_valid=True,
        )
        db_session.add(result)
        await db_session.commit()

        return run

    async def test_get_result_success(
        self, client: AsyncClient, completed_run_with_result, db_session: AsyncSession
    ):
        """Test successful retrieval of allocation result."""
        run = completed_run_with_result

        response = await client.get(
            f"/api/v1/runs/{run.id}/result",
            headers={"X-Session-Token": "test-result-session"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["run_id"] == run.id
        assert len(data["allocations"]) == 3
        assert data["allocations"][0]["channel"] == "TV"
        assert float(data["allocations"][0]["share_pct"]) == 40.0
        assert data["reasoning_summary"] == "A balanced media mix for automotive brand"
        assert float(data["confidence_score"]) == 0.85

    async def test_get_result_not_completed(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that result is not available for non-completed run."""
        # Create pending run
        run = Run(
            session_token="test-pending-session",
            customer_name="Test Brand",
            industry="Automotive",
            brand_kpi="adaware",
            status=RunStatus.GENERATING.value,
        )
        db_session.add(run)
        await db_session.commit()

        response = await client.get(
            f"/api/v1/runs/{run.id}/result",
            headers={"X-Session-Token": "test-pending-session"},
        )

        assert response.status_code == 400
        assert "generating" in response.json()["detail"].lower()

    async def test_get_result_not_found(self, client: AsyncClient):
        """Test 404 for non-existent run."""
        response = await client.get(
            "/api/v1/runs/99999/result",
            headers={"X-Session-Token": "test-session"},
        )
        assert response.status_code == 404

    async def test_get_result_unauthorized(self, client: AsyncClient):
        """Test 401 without session token."""
        response = await client.get("/api/v1/runs/1/result")
        assert response.status_code == 401

    async def test_get_result_wrong_session(
        self, client: AsyncClient, completed_run_with_result, db_session: AsyncSession
    ):
        """Test 403 when accessing another session's run."""
        run = completed_run_with_result

        response = await client.get(
            f"/api/v1/runs/{run.id}/result",
            headers={"X-Session-Token": "different-session"},
        )

        assert response.status_code == 403

    async def test_get_result_with_validation_warnings(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test result with validation warnings."""
        run = Run(
            session_token="test-warning-session",
            customer_name="Test Brand",
            industry="Automotive",
            brand_kpi="adaware",
            status=RunStatus.COMPLETED.value,
        )
        db_session.add(run)
        await db_session.flush()

        result = AllocationResult(
            run_id=run.id,
            allocations={
                "channels": [{"name": "TV", "percentage": 100.0}],
                "total_percentage": 100.0,
            },
            is_valid=True,
            validation_errors={
                "issues": [
                    {"field": "channel", "message": "Unknown channel used", "severity": "warning"},
                ],
            },
        )
        db_session.add(result)
        await db_session.commit()

        response = await client.get(
            f"/api/v1/runs/{run.id}/result",
            headers={"X-Session-Token": "test-warning-session"},
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data["warnings"]) >= 1
        assert "Unknown channel" in data["warnings"][0]

    async def test_owner_can_access_any_result(
        self, client: AsyncClient, completed_run_with_result, db_session: AsyncSession
    ):
        """Test that owner role can access any run's result."""
        run = completed_run_with_result

        response = await client.get(
            f"/api/v1/runs/{run.id}/result",
            headers={
                "X-Session-Token": "different-session",
                "X-User-Role": "owner",
            },
        )

        assert response.status_code == 200


class TestGetChatHistory:
    """Tests for GET /api/v1/runs/{id}/chat endpoint."""

    @pytest_asyncio.fixture
    async def run_with_chat(self, db_session: AsyncSession):
        """Create a run with chat history messages."""
        run = Run(
            session_token="test-chat-session",
            customer_name="Test Brand",
            industry="Automotive",
            brand_kpi="adaware",
            status=RunStatus.COMPLETED.value,
        )
        db_session.add(run)
        await db_session.flush()

        # Add various message types
        messages = [
            ChatHistory(
                run_id=run.id,
                message_type="summary",
                severity="info",
                title="Allocation Summary",
                content="Your allocation focuses on TV and Digital",
                display_order=0,
            ),
            ChatHistory(
                run_id=run.id,
                message_type="warning",
                severity="warning",
                title="Low Data Confidence",
                content="Limited data for Q4",
                display_order=1,
            ),
            ChatHistory(
                run_id=run.id,
                message_type="alert",
                severity="error",
                title="High Concentration",
                content="TV allocation exceeds 70%",
                display_order=2,
            ),
            ChatHistory(
                run_id=run.id,
                message_type="recommendation",
                severity="info",
                title="Consider Social Media",
                content="Your competitors are active on social media",
                display_order=3,
            ),
        ]
        for msg in messages:
            db_session.add(msg)
        await db_session.commit()

        return run

    async def test_get_chat_success(
        self, client: AsyncClient, run_with_chat, db_session: AsyncSession
    ):
        """Test successful retrieval of chat history."""
        run = run_with_chat

        response = await client.get(
            f"/api/v1/runs/{run.id}/chat",
            headers={"X-Session-Token": "test-chat-session"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["run_id"] == run.id
        assert data["total_messages"] == 4
        assert data["has_warnings"] is True
        assert data["has_alerts"] is True

        # Check messages are ordered
        messages = data["messages"]
        assert messages[0]["display_order"] == 0
        assert messages[0]["message_type"] == "summary"

    async def test_get_chat_filter_by_type(
        self, client: AsyncClient, run_with_chat, db_session: AsyncSession
    ):
        """Test filtering chat by message type."""
        run = run_with_chat

        response = await client.get(
            f"/api/v1/runs/{run.id}/chat",
            params={"message_type": "warning"},
            headers={"X-Session-Token": "test-chat-session"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["total_messages"] == 1
        assert data["messages"][0]["message_type"] == "warning"

    async def test_get_chat_invalid_type_filter(
        self, client: AsyncClient, run_with_chat, db_session: AsyncSession
    ):
        """Test error for invalid message type filter."""
        run = run_with_chat

        response = await client.get(
            f"/api/v1/runs/{run.id}/chat",
            params={"message_type": "invalid"},
            headers={"X-Session-Token": "test-chat-session"},
        )

        assert response.status_code == 400
        assert "invalid message_type" in response.json()["detail"].lower()

    async def test_get_chat_empty(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test chat history for run with no messages."""
        run = Run(
            session_token="test-empty-chat-session",
            customer_name="Test Brand",
            industry="Automotive",
            brand_kpi="adaware",
            status=RunStatus.COMPLETED.value,
        )
        db_session.add(run)
        await db_session.commit()

        response = await client.get(
            f"/api/v1/runs/{run.id}/chat",
            headers={"X-Session-Token": "test-empty-chat-session"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["total_messages"] == 0
        assert data["messages"] == []
        assert data["has_warnings"] is False
        assert data["has_alerts"] is False

    async def test_get_chat_not_found(self, client: AsyncClient):
        """Test 404 for non-existent run."""
        response = await client.get(
            "/api/v1/runs/99999/chat",
            headers={"X-Session-Token": "test-session"},
        )
        assert response.status_code == 404

    async def test_get_chat_unauthorized(self, client: AsyncClient):
        """Test 401 without session token."""
        response = await client.get("/api/v1/runs/1/chat")
        assert response.status_code == 401

    async def test_get_chat_wrong_session(
        self, client: AsyncClient, run_with_chat, db_session: AsyncSession
    ):
        """Test 403 when accessing another session's chat."""
        run = run_with_chat

        response = await client.get(
            f"/api/v1/runs/{run.id}/chat",
            headers={"X-Session-Token": "different-session"},
        )

        assert response.status_code == 403

    async def test_get_chat_with_metadata(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test chat messages with extra_data/metadata."""
        run = Run(
            session_token="test-metadata-session",
            customer_name="Test Brand",
            industry="Automotive",
            brand_kpi="adaware",
            status=RunStatus.COMPLETED.value,
        )
        db_session.add(run)
        await db_session.flush()

        message = ChatHistory(
            run_id=run.id,
            message_type="warning",
            severity="warning",
            title="Concentration Warning",
            content="TV has high concentration",
            extra_data={"channel": "TV", "percentage": 75.0},
            display_order=0,
        )
        db_session.add(message)
        await db_session.commit()

        response = await client.get(
            f"/api/v1/runs/{run.id}/chat",
            headers={"X-Session-Token": "test-metadata-session"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["messages"][0]["metadata"]["channel"] == "TV"
        assert data["messages"][0]["metadata"]["percentage"] == 75.0

    async def test_owner_can_access_any_chat(
        self, client: AsyncClient, run_with_chat, db_session: AsyncSession
    ):
        """Test that owner role can access any run's chat."""
        run = run_with_chat

        response = await client.get(
            f"/api/v1/runs/{run.id}/chat",
            headers={
                "X-Session-Token": "different-session",
                "X-User-Role": "owner",
            },
        )

        assert response.status_code == 200
        assert response.json()["total_messages"] == 4
