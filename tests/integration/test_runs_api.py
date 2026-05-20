"""Integration tests for run lifecycle API endpoints."""

import pytest
from httpx import AsyncClient

from src.api.schemas import RunStatus


class TestCreateRun:
    """Tests for POST /api/v1/runs endpoint."""

    async def test_create_run_success(self, client: AsyncClient):
        """Test successful run creation."""
        response = await client.post(
            "/api/v1/runs",
            json={
                "customer_name": "BMW",
                "industry": "Automotive",
                "brand_kpi": "adaware",
                "total_budget": 1000000,
            },
            headers={"X-Session-Token": "test-session-123"},
        )

        assert response.status_code == 201
        data = response.json()
        assert data["customer_name"] == "BMW"
        assert data["industry"] == "Automotive"
        assert data["brand_kpi"] == "adaware"
        assert data["status"] == "pending"
        assert "id" in data

    async def test_create_run_without_session_token(self, client: AsyncClient):
        """Test that missing session token returns 401."""
        response = await client.post(
            "/api/v1/runs",
            json={
                "customer_name": "BMW",
                "industry": "Automotive",
                "brand_kpi": "adaware",
            },
        )

        assert response.status_code == 401
        assert "session token" in response.json()["detail"].lower()

    async def test_create_run_invalid_kpi(self, client: AsyncClient):
        """Test that invalid KPI returns 422."""
        response = await client.post(
            "/api/v1/runs",
            json={
                "customer_name": "BMW",
                "industry": "Automotive",
                "brand_kpi": "invalid_kpi",
            },
            headers={"X-Session-Token": "test-session-123"},
        )

        assert response.status_code == 422

    async def test_create_run_with_optional_fields(self, client: AsyncClient):
        """Test run creation with all optional fields."""
        response = await client.post(
            "/api/v1/runs",
            json={
                "customer_name": "Mercedes",
                "industry": "Automotive",
                "brand_kpi": "consider",
                "total_budget": 500000,
                "channels": ["TV", "Digital"],
                "goal_text": "Increase brand consideration",
                "direction": "increase",
            },
            headers={"X-Session-Token": "test-session-456"},
        )

        assert response.status_code == 201
        data = response.json()
        assert data["customer_name"] == "Mercedes"
        assert data["brand_kpi"] == "consider"

    async def test_create_run_session_lock(self, client: AsyncClient):
        """Test that second run creation fails when one is active."""
        # Create first run
        response1 = await client.post(
            "/api/v1/runs",
            json={
                "customer_name": "BMW",
                "industry": "Automotive",
                "brand_kpi": "adaware",
            },
            headers={"X-Session-Token": "test-session-lock"},
        )
        assert response1.status_code == 201

        # Try to create second run with same session
        response2 = await client.post(
            "/api/v1/runs",
            json={
                "customer_name": "Audi",
                "industry": "Automotive",
                "brand_kpi": "aided",
            },
            headers={"X-Session-Token": "test-session-lock"},
        )
        assert response2.status_code == 409
        assert "active run already exists" in response2.json()["detail"].lower()


class TestGetRunStatus:
    """Tests for GET /api/v1/runs/{id}/status endpoint."""

    async def test_get_run_status_success(self, client: AsyncClient):
        """Test successful status retrieval."""
        # Create a run first
        create_response = await client.post(
            "/api/v1/runs",
            json={
                "customer_name": "BMW",
                "industry": "Automotive",
                "brand_kpi": "adaware",
            },
            headers={"X-Session-Token": "test-session-status"},
        )
        run_id = create_response.json()["id"]

        # Get status
        response = await client.get(
            f"/api/v1/runs/{run_id}/status",
            headers={"X-Session-Token": "test-session-status"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == run_id
        assert data["status"] == "pending"
        assert "progress" in data

    async def test_get_run_status_not_found(self, client: AsyncClient):
        """Test 404 for non-existent run."""
        response = await client.get(
            "/api/v1/runs/99999/status",
            headers={"X-Session-Token": "test-session-123"},
        )

        assert response.status_code == 404

    async def test_get_run_status_wrong_session(self, client: AsyncClient):
        """Test 403 when accessing another session's run."""
        # Create a run
        create_response = await client.post(
            "/api/v1/runs",
            json={
                "customer_name": "BMW",
                "industry": "Automotive",
                "brand_kpi": "adaware",
            },
            headers={"X-Session-Token": "test-session-owner"},
        )
        run_id = create_response.json()["id"]

        # Try to access with different session
        response = await client.get(
            f"/api/v1/runs/{run_id}/status",
            headers={"X-Session-Token": "different-session"},
        )

        assert response.status_code == 403


class TestStopRun:
    """Tests for POST /api/v1/runs/{id}/stop endpoint."""

    async def test_stop_run_success(self, client: AsyncClient):
        """Test successful run cancellation."""
        # Create a run
        create_response = await client.post(
            "/api/v1/runs",
            json={
                "customer_name": "BMW",
                "industry": "Automotive",
                "brand_kpi": "adaware",
            },
            headers={"X-Session-Token": "test-session-stop"},
        )
        run_id = create_response.json()["id"]

        # Stop the run
        response = await client.post(
            f"/api/v1/runs/{run_id}/stop",
            headers={"X-Session-Token": "test-session-stop"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == run_id
        assert data["status"] == "cancelled"
        assert "stopped_at" in data

    async def test_stop_run_with_reason(self, client: AsyncClient):
        """Test cancellation with reason."""
        # Create a run
        create_response = await client.post(
            "/api/v1/runs",
            json={
                "customer_name": "BMW",
                "industry": "Automotive",
                "brand_kpi": "adaware",
            },
            headers={"X-Session-Token": "test-session-stop-reason"},
        )
        run_id = create_response.json()["id"]

        # Stop with reason
        response = await client.post(
            f"/api/v1/runs/{run_id}/stop",
            json={"reason": "User requested cancellation"},
            headers={"X-Session-Token": "test-session-stop-reason"},
        )

        assert response.status_code == 200

    async def test_stop_run_not_found(self, client: AsyncClient):
        """Test 404 for non-existent run."""
        response = await client.post(
            "/api/v1/runs/99999/stop",
            headers={"X-Session-Token": "test-session-123"},
        )

        assert response.status_code == 404

    async def test_stop_run_wrong_session(self, client: AsyncClient):
        """Test 403 when stopping another session's run."""
        # Create a run
        create_response = await client.post(
            "/api/v1/runs",
            json={
                "customer_name": "BMW",
                "industry": "Automotive",
                "brand_kpi": "adaware",
            },
            headers={"X-Session-Token": "test-session-stop-owner"},
        )
        run_id = create_response.json()["id"]

        # Try to stop with different session
        response = await client.post(
            f"/api/v1/runs/{run_id}/stop",
            headers={"X-Session-Token": "different-session"},
        )

        assert response.status_code == 403

    async def test_stop_already_cancelled_run(self, client: AsyncClient):
        """Test 400 when stopping already cancelled run."""
        # Create and stop a run
        create_response = await client.post(
            "/api/v1/runs",
            json={
                "customer_name": "BMW",
                "industry": "Automotive",
                "brand_kpi": "adaware",
            },
            headers={"X-Session-Token": "test-session-double-stop"},
        )
        run_id = create_response.json()["id"]

        # Stop first time
        await client.post(
            f"/api/v1/runs/{run_id}/stop",
            headers={"X-Session-Token": "test-session-double-stop"},
        )

        # Try to stop again
        response = await client.post(
            f"/api/v1/runs/{run_id}/stop",
            headers={"X-Session-Token": "test-session-double-stop"},
        )

        assert response.status_code == 400
        assert "terminal state" in response.json()["detail"].lower()


class TestGetRun:
    """Tests for GET /api/v1/runs/{id} endpoint."""

    async def test_get_run_success(self, client: AsyncClient):
        """Test successful run retrieval."""
        # Create a run
        create_response = await client.post(
            "/api/v1/runs",
            json={
                "customer_name": "BMW",
                "industry": "Automotive",
                "brand_kpi": "adaware",
                "total_budget": 1000000,
            },
            headers={"X-Session-Token": "test-session-get"},
        )
        run_id = create_response.json()["id"]

        # Get full run details
        response = await client.get(
            f"/api/v1/runs/{run_id}",
            headers={"X-Session-Token": "test-session-get"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == run_id
        assert data["customer_name"] == "BMW"
        assert data["industry"] == "Automotive"
        assert data["brand_kpi"] == "adaware"
        assert float(data["total_budget"]) == 1000000
        assert "created_at" in data
        assert "updated_at" in data

    async def test_get_run_not_found(self, client: AsyncClient):
        """Test 404 for non-existent run."""
        response = await client.get(
            "/api/v1/runs/99999",
            headers={"X-Session-Token": "test-session-123"},
        )

        assert response.status_code == 404


class TestOwnerAccess:
    """Tests for owner-level access."""

    async def test_owner_can_access_any_run(self, client: AsyncClient):
        """Test that owner role can access any session's runs."""
        # Create a run with regular session
        create_response = await client.post(
            "/api/v1/runs",
            json={
                "customer_name": "BMW",
                "industry": "Automotive",
                "brand_kpi": "adaware",
            },
            headers={"X-Session-Token": "regular-user-session"},
        )
        run_id = create_response.json()["id"]

        # Access with owner role
        response = await client.get(
            f"/api/v1/runs/{run_id}/status",
            headers={
                "X-Session-Token": "owner-session",
                "X-User-Role": "owner",
            },
        )

        assert response.status_code == 200
