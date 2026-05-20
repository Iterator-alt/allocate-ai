"""Integration tests for competitor matching API endpoints."""

from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import IndustryMap, BrandMap, NielsenSpend, YouGovKPI


class TestGetCompetitors:
    """Tests for GET /api/v1/runs/{id}/competitors endpoint."""

    @pytest_asyncio.fixture
    async def sample_data(self, db_session: AsyncSession):
        """Create sample data for competitor matching."""
        # Industry mapping
        db_session.add(IndustryMap(
            wirtschaftsgruppe="Automotive",
            sector_label="Automotive",
            is_active=True
        ))

        # Brand mappings
        db_session.add(BrandMap(
            nielsen_brand="BMW AG",
            yougov_brand_label="BMW",
            wirtschaftsgruppe="Automotive",
            confidence=0.95,
            is_active=True
        ))
        db_session.add(BrandMap(
            nielsen_brand="Mercedes-Benz",
            yougov_brand_label="Mercedes",
            wirtschaftsgruppe="Automotive",
            confidence=0.90,
            is_active=True
        ))

        # YouGov data (need at least 3 months)
        for month in range(1, 4):
            db_session.add(YouGovKPI(
                brand_label="BMW",
                sector="Automotive",
                year=2023,
                month=month,
                adaware=Decimal("45.0")
            ))
            db_session.add(YouGovKPI(
                brand_label="Mercedes",
                sector="Automotive",
                year=2023,
                month=month,
                adaware=Decimal("50.0")
            ))

        # Nielsen data
        db_session.add(NielsenSpend(
            brand_name="BMW AG",
            wirtschaftsgruppe="Automotive",
            year=2023,
            month=1,
            channel="TV",
            spend_eur=Decimal("100000")
        ))
        db_session.add(NielsenSpend(
            brand_name="Mercedes-Benz",
            wirtschaftsgruppe="Automotive",
            year=2023,
            month=1,
            channel="TV",
            spend_eur=Decimal("150000")
        ))

        await db_session.commit()

    async def test_get_competitors_success(
        self, client: AsyncClient, sample_data, db_session: AsyncSession
    ):
        """Test successful competitor retrieval."""
        # First create a run
        create_response = await client.post(
            "/api/v1/runs",
            json={
                "customer_name": "Audi",  # Not in our sample data
                "industry": "Automotive",
                "brand_kpi": "adaware",
            },
            headers={"X-Session-Token": "test-competitors-session"},
        )
        assert create_response.status_code == 201
        run_id = create_response.json()["id"]

        # Get competitors
        response = await client.get(
            f"/api/v1/runs/{run_id}/competitors",
            headers={"X-Session-Token": "test-competitors-session"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["run_id"] == run_id
        assert data["industry"] == "Automotive"
        assert data["sector_label"] == "Automotive"
        assert data["total_competitors"] >= 0

    async def test_get_competitors_unknown_industry(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test competitor retrieval with unknown industry."""
        # Create run with unknown industry
        create_response = await client.post(
            "/api/v1/runs",
            json={
                "customer_name": "Test Brand",
                "industry": "UnknownIndustry",
                "brand_kpi": "adaware",
            },
            headers={"X-Session-Token": "test-unknown-industry"},
        )
        run_id = create_response.json()["id"]

        # Get competitors should fail
        response = await client.get(
            f"/api/v1/runs/{run_id}/competitors",
            headers={"X-Session-Token": "test-unknown-industry"},
        )

        assert response.status_code == 400
        data = response.json()
        assert "detail" in data

    async def test_get_competitors_not_found(self, client: AsyncClient):
        """Test 404 for non-existent run."""
        response = await client.get(
            "/api/v1/runs/99999/competitors",
            headers={"X-Session-Token": "test-session"},
        )
        assert response.status_code == 404

    async def test_get_competitors_unauthorized(self, client: AsyncClient):
        """Test 401 without session token."""
        response = await client.get("/api/v1/runs/1/competitors")
        assert response.status_code == 401


class TestConfirmCompetitors:
    """Tests for POST /api/v1/runs/{id}/competitors/confirm endpoint."""

    @pytest_asyncio.fixture
    async def sample_data(self, db_session: AsyncSession):
        """Create sample data."""
        db_session.add(IndustryMap(
            wirtschaftsgruppe="Automotive",
            sector_label="Automotive",
            is_active=True
        ))
        db_session.add(BrandMap(
            nielsen_brand="BMW AG",
            yougov_brand_label="BMW",
            wirtschaftsgruppe="Automotive",
            confidence=0.95,
            is_active=True
        ))
        for month in range(1, 4):
            db_session.add(YouGovKPI(
                brand_label="BMW",
                sector="Automotive",
                year=2023,
                month=month,
                adaware=Decimal("45.0")
            ))
        db_session.add(NielsenSpend(
            brand_name="BMW AG",
            wirtschaftsgruppe="Automotive",
            year=2023,
            month=1,
            channel="TV",
            spend_eur=Decimal("100000")
        ))
        await db_session.commit()

    async def test_confirm_competitors_approve(
        self, client: AsyncClient, sample_data, db_session: AsyncSession
    ):
        """Test approving competitor set."""
        # Create run and get competitors
        create_response = await client.post(
            "/api/v1/runs",
            json={
                "customer_name": "Test Brand",
                "industry": "Automotive",
                "brand_kpi": "adaware",
            },
            headers={"X-Session-Token": "test-confirm-approve"},
        )
        run_id = create_response.json()["id"]

        # Get competitors to trigger matching
        await client.get(
            f"/api/v1/runs/{run_id}/competitors",
            headers={"X-Session-Token": "test-confirm-approve"},
        )

        # Confirm competitors
        response = await client.post(
            f"/api/v1/runs/{run_id}/competitors/confirm",
            json={"action": "approve"},
            headers={"X-Session-Token": "test-confirm-approve"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "approved"
        assert "confirmed_competitors" in data

    async def test_confirm_competitors_cancel(
        self, client: AsyncClient, sample_data, db_session: AsyncSession
    ):
        """Test cancelling competitor confirmation."""
        # Create run and get competitors
        create_response = await client.post(
            "/api/v1/runs",
            json={
                "customer_name": "Test Brand",
                "industry": "Automotive",
                "brand_kpi": "adaware",
            },
            headers={"X-Session-Token": "test-confirm-cancel"},
        )
        run_id = create_response.json()["id"]

        await client.get(
            f"/api/v1/runs/{run_id}/competitors",
            headers={"X-Session-Token": "test-confirm-cancel"},
        )

        # Cancel
        response = await client.post(
            f"/api/v1/runs/{run_id}/competitors/confirm",
            json={"action": "cancel", "reason": "Changed my mind"},
            headers={"X-Session-Token": "test-confirm-cancel"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "cancelled"

    async def test_confirm_wrong_state(
        self, client: AsyncClient, sample_data, db_session: AsyncSession
    ):
        """Test confirming when not in awaiting_confirmation state."""
        # Create run but don't get competitors
        create_response = await client.post(
            "/api/v1/runs",
            json={
                "customer_name": "Test Brand",
                "industry": "Automotive",
                "brand_kpi": "adaware",
            },
            headers={"X-Session-Token": "test-wrong-state"},
        )
        run_id = create_response.json()["id"]

        # Try to confirm without getting competitors first
        response = await client.post(
            f"/api/v1/runs/{run_id}/competitors/confirm",
            json={"action": "approve"},
            headers={"X-Session-Token": "test-wrong-state"},
        )

        assert response.status_code == 400
        assert "not awaiting confirmation" in response.json()["detail"].lower()

    async def test_confirm_invalid_action(
        self, client: AsyncClient, sample_data, db_session: AsyncSession
    ):
        """Test invalid confirmation action."""
        create_response = await client.post(
            "/api/v1/runs",
            json={
                "customer_name": "Test Brand",
                "industry": "Automotive",
                "brand_kpi": "adaware",
            },
            headers={"X-Session-Token": "test-invalid-action"},
        )
        run_id = create_response.json()["id"]

        await client.get(
            f"/api/v1/runs/{run_id}/competitors",
            headers={"X-Session-Token": "test-invalid-action"},
        )

        response = await client.post(
            f"/api/v1/runs/{run_id}/competitors/confirm",
            json={"action": "invalid"},
            headers={"X-Session-Token": "test-invalid-action"},
        )

        # Pydantic validation returns 422 for invalid enum values
        assert response.status_code in (400, 422)


class TestFeasibilityEndpoint:
    """Tests for GET /api/v1/runs/{id}/feasibility endpoint."""

    @pytest_asyncio.fixture
    async def sample_data(self, db_session: AsyncSession):
        """Create sample data."""
        db_session.add(IndustryMap(
            wirtschaftsgruppe="Automotive",
            sector_label="Automotive",
            is_active=True
        ))
        db_session.add(NielsenSpend(
            brand_name="BMW",
            wirtschaftsgruppe="Automotive",
            year=2023,
            month=1,
            channel="TV",
            spend_eur=Decimal("100000")
        ))
        await db_session.commit()

    async def test_feasibility_check_success(
        self, client: AsyncClient, sample_data, db_session: AsyncSession
    ):
        """Test feasibility check endpoint."""
        create_response = await client.post(
            "/api/v1/runs",
            json={
                "customer_name": "Test",
                "industry": "Automotive",
                "brand_kpi": "adaware",
            },
            headers={"X-Session-Token": "test-feasibility"},
        )
        run_id = create_response.json()["id"]

        response = await client.get(
            f"/api/v1/runs/{run_id}/feasibility",
            headers={"X-Session-Token": "test-feasibility"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["run_id"] == run_id
        assert "is_feasible" in data
        assert "issues" in data
        assert "warnings" in data
