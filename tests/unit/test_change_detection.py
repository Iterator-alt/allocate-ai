"""Unit tests for Change Detection Guard."""

from decimal import Decimal
from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Run, AllocationResult, RunStatus
from src.services.guards import ChangeDetectionGuard, ChangeDetectionResult


class TestChangeDetectionGuard:
    """Tests for Change Detection Guard (Guard #3)."""

    @pytest_asyncio.fixture
    async def guard(self, db_session: AsyncSession) -> ChangeDetectionGuard:
        return ChangeDetectionGuard(db_session, cache_ttl_minutes=60)

    @pytest_asyncio.fixture
    async def completed_run(self, db_session: AsyncSession):
        """Create a completed run with cached result."""
        run = Run(
            session_token="test-session",
            customer_name="BMW",
            industry="PKW",
            brand_kpi="adaware",
            status=RunStatus.COMPLETED.value,
            completed_at=datetime.utcnow(),
        )
        db_session.add(run)
        await db_session.flush()

        # Set input hash
        guard = ChangeDetectionGuard(db_session)
        run.input_hash = guard._compute_input_hash(
            customer_name="BMW",
            industry="PKW",
            brand_kpi="adaware",
            confirmed_competitors=["Mercedes-Benz", "Audi"],
        )

        # Add result
        result = AllocationResult(
            run_id=run.id,
            allocations={
                "channels": [
                    {"channel": "TV", "percentage": 40.0},
                    {"channel": "Digital", "percentage": 60.0},
                ]
            },
            summary="Test allocation",
            confidence_score=Decimal("0.85"),
        )
        db_session.add(result)
        await db_session.commit()

        return run

    async def test_cache_hit_returns_cached_result(
        self, guard: ChangeDetectionGuard, completed_run, db_session: AsyncSession
    ):
        """Test that identical inputs return cached result."""
        result = await guard.check_for_cached_result(
            customer_name="BMW",
            industry="PKW",
            brand_kpi="adaware",
            confirmed_competitors=["Mercedes-Benz", "Audi"],
        )

        assert result.has_changes is False
        assert result.cached_run_id == completed_run.id
        assert result.cached_result is not None
        assert "channels" in result.cached_result
        assert "Returning cached result" in result.reason

    async def test_cache_miss_on_different_inputs(
        self, guard: ChangeDetectionGuard, completed_run, db_session: AsyncSession
    ):
        """Test that different inputs result in cache miss."""
        result = await guard.check_for_cached_result(
            customer_name="Audi",  # Different customer
            industry="PKW",
            brand_kpi="adaware",
            confirmed_competitors=["Mercedes-Benz", "BMW"],
        )

        assert result.has_changes is True
        assert result.cached_result is None
        assert "No previous run found" in result.reason

    async def test_cache_miss_on_different_competitors(
        self, guard: ChangeDetectionGuard, completed_run, db_session: AsyncSession
    ):
        """Test that different competitors result in cache miss."""
        result = await guard.check_for_cached_result(
            customer_name="BMW",
            industry="PKW",
            brand_kpi="adaware",
            confirmed_competitors=["VW", "Porsche"],  # Different competitors
        )

        assert result.has_changes is True
        assert result.cached_result is None

    async def test_expired_cache_returns_changes(
        self, guard: ChangeDetectionGuard, db_session: AsyncSession
    ):
        """Test that expired cache is treated as changed."""
        # Create a run completed 2 hours ago (outside 60 min TTL)
        old_run = Run(
            session_token="test-session",
            customer_name="BMW",
            industry="PKW",
            brand_kpi="adaware",
            status=RunStatus.COMPLETED.value,
            completed_at=datetime.utcnow() - timedelta(hours=2),
        )
        db_session.add(old_run)
        await db_session.flush()

        old_run.input_hash = guard._compute_input_hash(
            customer_name="BMW",
            industry="PKW",
            brand_kpi="adaware",
        )

        db_session.add(AllocationResult(
            run_id=old_run.id,
            allocations={"channels": []},
        ))
        await db_session.commit()

        result = await guard.check_for_cached_result(
            customer_name="BMW",
            industry="PKW",
            brand_kpi="adaware",
        )

        assert result.has_changes is True
        assert "expired" in result.reason.lower()

    async def test_input_hash_deterministic(self, guard: ChangeDetectionGuard):
        """Test that input hash is deterministic."""
        hash1 = guard._compute_input_hash(
            customer_name="BMW",
            industry="PKW",
            brand_kpi="adaware",
            confirmed_competitors=["Mercedes", "Audi"],
        )
        hash2 = guard._compute_input_hash(
            customer_name="BMW",
            industry="PKW",
            brand_kpi="adaware",
            confirmed_competitors=["Mercedes", "Audi"],
        )

        assert hash1 == hash2

    async def test_input_hash_normalized(self, guard: ChangeDetectionGuard):
        """Test that input hash normalizes values."""
        # Different cases and whitespace should produce same hash
        hash1 = guard._compute_input_hash(
            customer_name="BMW",
            industry="PKW",
            brand_kpi="adaware",
        )
        hash2 = guard._compute_input_hash(
            customer_name="  bmw  ",
            industry="pkw",
            brand_kpi="ADAWARE",
        )

        assert hash1 == hash2

    async def test_competitor_order_ignored(self, guard: ChangeDetectionGuard):
        """Test that competitor order doesn't affect hash."""
        hash1 = guard._compute_input_hash(
            customer_name="BMW",
            industry="PKW",
            brand_kpi="adaware",
            confirmed_competitors=["Mercedes", "Audi", "VW"],
        )
        hash2 = guard._compute_input_hash(
            customer_name="BMW",
            industry="PKW",
            brand_kpi="adaware",
            confirmed_competitors=["VW", "Mercedes", "Audi"],  # Different order
        )

        assert hash1 == hash2

    async def test_cache_key_preview(self, guard: ChangeDetectionGuard):
        """Test cache key preview."""
        preview = guard.get_cache_key_preview(
            customer_name="BMW",
            industry="PKW",
            brand_kpi="adaware",
            confirmed_competitors=["Mercedes"],
        )

        assert "input_hash" in preview
        assert "normalized_inputs" in preview
        assert preview["normalized_inputs"]["customer_name"] == "bmw"
        assert preview["normalized_inputs"]["industry"] == "pkw"

    async def test_invalidate_cache_by_hash(
        self, guard: ChangeDetectionGuard, completed_run, db_session: AsyncSession
    ):
        """Test cache invalidation by input hash."""
        # Get the hash
        original_hash = completed_run.input_hash

        # Invalidate
        count = await guard.invalidate_cache(input_hash=original_hash)
        await db_session.commit()

        assert count == 1

        # Check that cache miss now occurs
        await db_session.refresh(completed_run)
        assert completed_run.input_hash is None

    async def test_update_run_hash(
        self, guard: ChangeDetectionGuard, db_session: AsyncSession
    ):
        """Test updating run hash after confirmation."""
        run = Run(
            session_token="test-session",
            customer_name="Test",
            industry="PKW",
            brand_kpi="adaware",
            status=RunStatus.GENERATING.value,
        )
        db_session.add(run)
        await db_session.flush()

        new_hash = guard._compute_input_hash(
            customer_name="Test",
            industry="PKW",
            brand_kpi="adaware",
            confirmed_competitors=["BMW"],
        )

        await guard.update_run_hash(run.id, new_hash)
        await db_session.commit()

        await db_session.refresh(run)
        assert run.input_hash == new_hash

    async def test_no_cached_result_returns_changes(
        self, guard: ChangeDetectionGuard, db_session: AsyncSession
    ):
        """Test that missing result is treated as changed."""
        # Create completed run WITHOUT result
        run = Run(
            session_token="test-session",
            customer_name="BMW",
            industry="PKW",
            brand_kpi="adaware",
            status=RunStatus.COMPLETED.value,
            completed_at=datetime.utcnow(),
        )
        db_session.add(run)
        await db_session.flush()

        run.input_hash = guard._compute_input_hash(
            customer_name="BMW",
            industry="PKW",
            brand_kpi="adaware",
        )
        await db_session.commit()

        result = await guard.check_for_cached_result(
            customer_name="BMW",
            industry="PKW",
            brand_kpi="adaware",
        )

        assert result.has_changes is True
        assert "no result available" in result.reason.lower()
