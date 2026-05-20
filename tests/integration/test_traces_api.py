"""Integration tests for prompt trace API endpoints."""

from datetime import datetime
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Run, PromptTrace
from src.db.models.run import RunStatus


class TestGetPromptTraces:
    """Tests for GET /api/v1/runs/{id}/trace endpoint."""

    @pytest_asyncio.fixture
    async def run_with_traces(self, db_session: AsyncSession):
        """Create a run with prompt traces."""
        run = Run(
            session_token="test-trace-session",
            customer_name="Test Brand",
            industry="Automotive",
            brand_kpi="adaware",
            status=RunStatus.COMPLETED.value,
        )
        db_session.add(run)
        await db_session.flush()

        # Add traces
        traces = [
            PromptTrace(
                run_id=run.id,
                called_at=datetime.utcnow(),
                model="gpt-4o",
                prompt="System: You are a media planning assistant.\nUser: Analyze competitors.",
                response='{"allocations": [...]}',
                prompt_tokens=500,
                completion_tokens=200,
                total_tokens=700,
                latency_ms=2500,
                status="success",
            ),
            PromptTrace(
                run_id=run.id,
                called_at=datetime.utcnow(),
                model="gpt-4o",
                prompt="System: Generate feedback.\nUser: Provide recommendations.",
                response='{"recommendations": [...]}',
                prompt_tokens=300,
                completion_tokens=150,
                total_tokens=450,
                latency_ms=1800,
                status="success",
            ),
        ]
        for trace in traces:
            db_session.add(trace)
        await db_session.commit()

        return run

    async def test_owner_can_access_traces(
        self, client: AsyncClient, run_with_traces, db_session: AsyncSession
    ):
        """Test that owner can access prompt traces."""
        run = run_with_traces

        response = await client.get(
            f"/api/v1/runs/{run.id}/trace",
            headers={
                "X-Session-Token": "any-session",
                "X-User-Role": "owner",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["run_id"] == run.id
        assert data["total_traces"] == 2
        assert data["total_tokens"] == 1150  # 700 + 450
        assert data["total_latency_ms"] == 4300  # 2500 + 1800
        assert data["success_rate"] == 1.0

    async def test_owner_sees_full_prompts(
        self, client: AsyncClient, run_with_traces, db_session: AsyncSession
    ):
        """Test that owner sees full prompt content."""
        run = run_with_traces

        response = await client.get(
            f"/api/v1/runs/{run.id}/trace",
            headers={
                "X-Session-Token": "any-session",
                "X-User-Role": "owner",
            },
        )

        assert response.status_code == 200
        data = response.json()
        traces = data["traces"]

        assert len(traces) == 2
        assert "You are a media planning assistant" in traces[0]["prompt"]
        assert traces[0]["response"] is not None
        assert traces[0]["prompt_tokens"] == 500

    async def test_non_owner_denied(
        self, client: AsyncClient, run_with_traces, db_session: AsyncSession
    ):
        """Test that regular users cannot access traces."""
        run = run_with_traces

        response = await client.get(
            f"/api/v1/runs/{run.id}/trace",
            headers={
                "X-Session-Token": "test-trace-session",
                "X-User-Role": "user",
            },
        )

        assert response.status_code == 403
        assert "owner" in response.json()["detail"].lower()

    async def test_admin_denied(
        self, client: AsyncClient, run_with_traces, db_session: AsyncSession
    ):
        """Test that admin users cannot access traces (owner only)."""
        run = run_with_traces

        response = await client.get(
            f"/api/v1/runs/{run.id}/trace",
            headers={
                "X-Session-Token": "test-trace-session",
                "X-User-Role": "admin",
            },
        )

        assert response.status_code == 403

    async def test_unauthorized(self, client: AsyncClient):
        """Test 401 without session token."""
        response = await client.get("/api/v1/runs/1/trace")
        assert response.status_code == 401

    async def test_run_not_found(self, client: AsyncClient):
        """Test 404 for non-existent run."""
        response = await client.get(
            "/api/v1/runs/99999/trace",
            headers={
                "X-Session-Token": "any-session",
                "X-User-Role": "owner",
            },
        )
        assert response.status_code == 404

    async def test_empty_traces(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test response for run with no traces."""
        run = Run(
            session_token="test-empty-traces",
            customer_name="Test Brand",
            industry="Automotive",
            brand_kpi="adaware",
            status=RunStatus.COMPLETED.value,
        )
        db_session.add(run)
        await db_session.commit()

        response = await client.get(
            f"/api/v1/runs/{run.id}/trace",
            headers={
                "X-Session-Token": "any-session",
                "X-User-Role": "owner",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["total_traces"] == 0
        assert data["traces"] == []
        assert data["success_rate"] == 0.0

    async def test_trace_with_error(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test trace with error status."""
        run = Run(
            session_token="test-error-trace",
            customer_name="Test Brand",
            industry="Automotive",
            brand_kpi="adaware",
            status=RunStatus.FAILED.value,
        )
        db_session.add(run)
        await db_session.flush()

        trace = PromptTrace(
            run_id=run.id,
            called_at=datetime.utcnow(),
            model="gpt-4o",
            prompt="Test prompt",
            status="error",
            error_message="Rate limit exceeded",
            latency_ms=500,
        )
        db_session.add(trace)
        await db_session.commit()

        response = await client.get(
            f"/api/v1/runs/{run.id}/trace",
            headers={
                "X-Session-Token": "any-session",
                "X-User-Role": "owner",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success_rate"] == 0.0
        assert data["traces"][0]["status"] == "error"
        assert data["traces"][0]["error_message"] == "Rate limit exceeded"


class TestHealthEndpoints:
    """Tests for health check endpoints."""

    async def test_health_check(self, client: AsyncClient):
        """Test basic health check."""
        response = await client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert "timestamp" in data
        assert "version" in data

    async def test_readiness_check(self, client: AsyncClient):
        """Test readiness check with DB connectivity."""
        response = await client.get(
            "/ready",
            headers={"X-Session-Token": "health-check-token"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] in ["ready", "degraded"]
        assert "checks" in data
        assert "database" in data["checks"]
