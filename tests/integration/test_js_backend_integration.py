"""Integration tests for JS Backend (Prisma) integration endpoints.

Tests the Manager's Spec v2 endpoints:
- POST /api/v1/runs/start - Start an existing run
- POST /api/v1/runs/competitors/confirm - Confirm competitors (run_id in body)
- GET /api/v1/runs/{id}/status - Status with stage, progress_pct fields
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.run import Run, RunStatus as DBRunStatus


class TestStartRunEndpoint:
    """Tests for POST /api/v1/runs/start endpoint (Manager's Spec v2)."""

    async def test_start_existing_pending_run(self, client: AsyncClient):
        """Test starting a run that was created by JS Backend."""
        # First create a run (simulating what JS Backend does via direct DB insert)
        # In this test we use the normal create endpoint to get a run in DB
        create_response = await client.post(
            "/api/v1/runs",
            json={
                "customer_name": "TestBrand",
                "industry": "FMCG",
                "brand_kpi": "adaware",
            },
            headers={"X-Session-Token": "js-backend-session"},
        )
        assert create_response.status_code == 201
        run_id = create_response.json()["id"]

        # Now use /start endpoint (the JS Backend integration endpoint)
        response = await client.post(
            "/api/v1/runs/start",
            json={"run_id": run_id, "action": "start"},
            headers={"X-Session-Token": "js-backend-session"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["run_id"] == run_id
        assert data["status"] == "started"
        assert data["error_message"] is None

    async def test_start_nonexistent_run(self, client: AsyncClient):
        """Test starting a run that doesn't exist returns error."""
        response = await client.post(
            "/api/v1/runs/start",
            json={"run_id": 99999, "action": "start"},
            headers={"X-Session-Token": "test-session"},
        )

        assert response.status_code == 200  # Returns 200 with error in body
        data = response.json()
        assert data["run_id"] == 99999
        assert data["status"] == "error"
        assert "not found" in data["error_message"].lower()

    async def test_start_already_started_run(self, client: AsyncClient):
        """Test starting a run that's already started returns error."""
        # Create and start a run
        create_response = await client.post(
            "/api/v1/runs",
            json={
                "customer_name": "TestBrand",
                "industry": "FMCG",
                "brand_kpi": "adaware",
            },
            headers={"X-Session-Token": "test-session-already-started"},
        )
        run_id = create_response.json()["id"]

        # Start it first time
        await client.post(
            "/api/v1/runs/start",
            json={"run_id": run_id, "action": "start"},
            headers={"X-Session-Token": "test-session-already-started"},
        )

        # Try to start again
        response = await client.post(
            "/api/v1/runs/start",
            json={"run_id": run_id, "action": "start"},
            headers={"X-Session-Token": "test-session-already-started"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "error"
        assert "already started" in data["error_message"].lower()

    async def test_start_run_invalid_action(self, client: AsyncClient):
        """Test that invalid action returns 422."""
        response = await client.post(
            "/api/v1/runs/start",
            json={"run_id": 1, "action": "invalid"},
            headers={"X-Session-Token": "test-session"},
        )

        assert response.status_code == 422


class TestConfirmCompetitorsV2Endpoint:
    """Tests for POST /api/v1/runs/competitors/confirm endpoint (Manager's Spec v2)."""

    async def test_confirm_competitors_not_awaiting(self, client: AsyncClient):
        """Test error when run is not in awaiting_confirmation state."""
        # Create a run (will be in pending state)
        create_response = await client.post(
            "/api/v1/runs",
            json={
                "customer_name": "TestBrand",
                "industry": "FMCG",
                "brand_kpi": "adaware",
            },
            headers={"X-Session-Token": "test-not-awaiting"},
        )
        run_id = create_response.json()["id"]

        # Try to confirm while in pending state
        response = await client.post(
            "/api/v1/runs/competitors/confirm",
            json={"run_id": run_id, "action": "approved"},
            headers={"X-Session-Token": "test-not-awaiting"},
        )

        assert response.status_code == 400
        assert "not awaiting confirmation" in response.json()["detail"].lower()

    async def test_confirm_competitors_not_found(self, client: AsyncClient):
        """Test error when run doesn't exist."""
        response = await client.post(
            "/api/v1/runs/competitors/confirm",
            json={"run_id": 99999, "action": "approved"},
            headers={"X-Session-Token": "test-session"},
        )

        assert response.status_code == 404


class TestRunStatusFields:
    """Tests for status response fields needed by JS Backend."""

    async def test_status_includes_stage_field(self, client: AsyncClient):
        """Test that status response includes stage field computed from status."""
        # Create a run
        create_response = await client.post(
            "/api/v1/runs",
            json={
                "customer_name": "TestBrand",
                "industry": "FMCG",
                "brand_kpi": "adaware",
            },
            headers={"X-Session-Token": "test-stage-field"},
        )
        run_id = create_response.json()["id"]

        # Get status - stage is computed from status, not stored in DB
        response = await client.get(
            f"/api/v1/runs/{run_id}/status",
            headers={"X-Session-Token": "test-stage-field"},
        )

        assert response.status_code == 200
        data = response.json()
        assert "stage" in data
        # For pending status, stage is None
        assert data["stage"] is None or isinstance(data["stage"], str)

    async def test_status_includes_progress_pct(self, client: AsyncClient):
        """Test that status response includes progress_pct field (0-100)."""
        # Create a run
        create_response = await client.post(
            "/api/v1/runs",
            json={
                "customer_name": "TestBrand",
                "industry": "FMCG",
                "brand_kpi": "adaware",
            },
            headers={"X-Session-Token": "test-progress-pct"},
        )
        run_id = create_response.json()["id"]

        # Get status - progress_pct is computed from status, not stored in DB
        response = await client.get(
            f"/api/v1/runs/{run_id}/status",
            headers={"X-Session-Token": "test-progress-pct"},
        )

        assert response.status_code == 200
        data = response.json()
        assert "progress_pct" in data
        # progress_pct is computed from status, pending = 0
        assert data["progress_pct"] == 0
        assert 0 <= data["progress_pct"] <= 100

    async def test_status_includes_progress_message(self, client: AsyncClient):
        """Test that status response includes progress field (human-readable)."""
        # Create a run
        create_response = await client.post(
            "/api/v1/runs",
            json={
                "customer_name": "TestBrand",
                "industry": "FMCG",
                "brand_kpi": "adaware",
            },
            headers={"X-Session-Token": "test-progress-msg"},
        )
        run_id = create_response.json()["id"]

        # Get status
        response = await client.get(
            f"/api/v1/runs/{run_id}/status",
            headers={"X-Session-Token": "test-progress-msg"},
        )

        assert response.status_code == 200
        data = response.json()
        assert "progress" in data

    async def test_stage_values_documented(self, client: AsyncClient):
        """Test that stage field is documented in status response.

        Stage values (S1, S1.5, S2, S3, S4) are computed from status:
        - MATCHING -> S1
        - AWAITING_CONFIRMATION -> S1.5
        - GENERATING -> S2
        - PARSING -> S3
        - FEEDBACK -> S4
        """
        # Create a run
        create_response = await client.post(
            "/api/v1/runs",
            json={
                "customer_name": "TestBrand",
                "industry": "FMCG",
                "brand_kpi": "adaware",
            },
            headers={"X-Session-Token": "test-stage-values"},
        )
        run_id = create_response.json()["id"]

        response = await client.get(
            f"/api/v1/runs/{run_id}/status",
            headers={"X-Session-Token": "test-stage-values"},
        )

        assert response.status_code == 200
        data = response.json()
        # Stage field exists in response
        assert "stage" in data
        # For pending, stage is None
        assert data["stage"] is None


class TestIntegrationFlow:
    """End-to-end tests simulating JS Backend integration flow."""

    async def test_full_js_backend_flow(self, client: AsyncClient):
        """Test the full flow as JS Backend would use it.

        1. Create run (JS Backend does this via Prisma)
        2. POST /runs/start
        3. Poll GET /status
        """
        # Step 1: Create run
        create_response = await client.post(
            "/api/v1/runs",
            json={
                "customer_name": "TestBrand",
                "industry": "FMCG",
                "brand_kpi": "adaware",
                "total_budget": 500000,
            },
            headers={"X-Session-Token": "js-backend-flow"},
        )
        assert create_response.status_code == 201
        run_id = create_response.json()["id"]

        # Step 2: Start the run
        start_response = await client.post(
            "/api/v1/runs/start",
            json={"run_id": run_id, "action": "start"},
            headers={"X-Session-Token": "js-backend-flow"},
        )
        assert start_response.status_code == 200
        assert start_response.json()["status"] == "started"

        # Step 3: Poll status (simulate waiting for Stage 1)
        status_response = await client.get(
            f"/api/v1/runs/{run_id}/status",
            headers={"X-Session-Token": "js-backend-flow"},
        )
        assert status_response.status_code == 200
        status_data = status_response.json()
        assert "id" in status_data
        assert "status" in status_data
        assert "stage" in status_data
        assert "progress_pct" in status_data
        assert "progress" in status_data
