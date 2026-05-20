"""Tests for API endpoints."""

import pytest
from httpx import AsyncClient


class TestHealthEndpoints:
    """Tests for health check endpoints."""

    async def test_health_check(self, client: AsyncClient):
        """Test /health endpoint returns healthy status."""
        response = await client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert "timestamp" in data
        assert "version" in data

    async def test_readiness_check(self, client: AsyncClient):
        """Test /ready endpoint returns ready status with checks."""
        response = await client.get("/ready")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] in ["ready", "degraded"]
        assert "checks" in data
        assert "database" in data["checks"]
