"""Tests for usage logging service."""

from datetime import datetime, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.logging import UsageLog
from src.services.llm_gateway.client import LLMResponse
from src.services.usage_logging import UsageLoggingService, MODEL_PRICING


class TestUsageLoggingService:
    """Tests for UsageLoggingService."""

    @pytest_asyncio.fixture
    async def usage_service(self, db_session: AsyncSession):
        """Create a usage logging service instance."""
        return UsageLoggingService(db_session)

    async def test_log_usage(self, usage_service: UsageLoggingService, db_session: AsyncSession):
        """Test logging usage."""
        usage = await usage_service.log_usage(
            model="gpt-4o",
            prompt_tokens=1000,
            completion_tokens=500,
            run_id=1,
            user_id=10,
            session_token="test-session",
        )

        assert usage.id is not None
        assert usage.model == "gpt-4o"
        assert usage.prompt_tokens == 1000
        assert usage.completion_tokens == 500
        assert usage.total_tokens == 1500
        assert usage.cost_usd is not None
        assert usage.cost_usd > 0

    async def test_log_from_response(self, usage_service: UsageLoggingService, db_session: AsyncSession):
        """Test logging from LLMResponse."""
        response = LLMResponse(
            content="Test",
            parsed_json=None,
            model="gpt-4o",
            prompt_tokens=2000,
            completion_tokens=1000,
            total_tokens=3000,
            latency_ms=1500,
            finish_reason="stop",
        )

        usage = await usage_service.log_from_response(
            response,
            run_id=2,
            session_token="test-session-2",
        )

        assert usage.model == "gpt-4o"
        assert usage.total_tokens == 3000
        assert usage.extra_data is not None
        assert usage.extra_data["latency_ms"] == 1500

    async def test_cost_calculation_gpt4o(self, usage_service: UsageLoggingService):
        """Test cost calculation for GPT-4o."""
        # GPT-4o pricing: $5/1M input, $15/1M output
        cost = usage_service.estimate_cost(
            model="gpt-4o",
            prompt_tokens=1000,
            completion_tokens=1000,
        )

        expected_prompt_cost = 1000 / 1000 * 0.005  # $0.005
        expected_completion_cost = 1000 / 1000 * 0.015  # $0.015
        expected_total = expected_prompt_cost + expected_completion_cost  # $0.02

        assert abs(cost - expected_total) < 0.0001

    async def test_cost_calculation_gpt4o_mini(self, usage_service: UsageLoggingService):
        """Test cost calculation for GPT-4o-mini."""
        cost = usage_service.estimate_cost(
            model="gpt-4o-mini",
            prompt_tokens=10000,
            completion_tokens=5000,
        )

        # Much cheaper than GPT-4o
        gpt4o_cost = usage_service.estimate_cost("gpt-4o", 10000, 5000)
        assert cost < gpt4o_cost

    async def test_cost_calculation_unknown_model(self, usage_service: UsageLoggingService):
        """Test cost calculation for unknown model uses defaults."""
        cost = usage_service.estimate_cost(
            model="unknown-model-xyz",
            prompt_tokens=1000,
            completion_tokens=1000,
        )

        # Should use default pricing
        assert cost > 0

    async def test_get_usage_by_session(self, usage_service: UsageLoggingService, db_session: AsyncSession):
        """Test getting usage by session."""
        session_token = "test-session-stats"

        # Log some usage
        for i in range(3):
            await usage_service.log_usage(
                model="gpt-4o",
                prompt_tokens=100 * (i + 1),
                completion_tokens=50 * (i + 1),
                session_token=session_token,
            )

        stats = await usage_service.get_usage_by_session(session_token, hours=24)

        assert stats["session_token"] == session_token
        assert stats["total_requests"] == 3
        assert stats["total_prompt_tokens"] == 100 + 200 + 300
        assert stats["total_completion_tokens"] == 50 + 100 + 150
        assert stats["total_cost_usd"] > 0

    async def test_get_usage_by_user(self, usage_service: UsageLoggingService, db_session: AsyncSession):
        """Test getting usage by user."""
        user_id = 42

        for i in range(2):
            await usage_service.log_usage(
                model="gpt-4o",
                prompt_tokens=500,
                completion_tokens=250,
                user_id=user_id,
            )

        stats = await usage_service.get_usage_by_user(user_id, days=30)

        assert stats["user_id"] == user_id
        assert stats["total_requests"] == 2
        assert stats["total_tokens"] == 1500

    async def test_get_usage_by_run(self, usage_service: UsageLoggingService, db_session: AsyncSession):
        """Test getting usage by run."""
        run_id = 100

        await usage_service.log_usage(
            model="gpt-4o",
            prompt_tokens=1500,
            completion_tokens=750,
            run_id=run_id,
        )

        stats = await usage_service.get_usage_by_run(run_id)

        assert stats["run_id"] == run_id
        assert stats["total_requests"] == 1
        assert stats["total_tokens"] == 2250

    async def test_get_daily_usage(self, usage_service: UsageLoggingService, db_session: AsyncSession):
        """Test getting daily usage breakdown."""
        # Log some usage for today
        await usage_service.log_usage(
            model="gpt-4o",
            prompt_tokens=1000,
            completion_tokens=500,
        )

        daily = await usage_service.get_daily_usage(days=7)

        # Should have at least one day
        assert len(daily) >= 1
        assert daily[0]["total_requests"] >= 1

    async def test_get_model_usage(self, usage_service: UsageLoggingService, db_session: AsyncSession):
        """Test getting usage breakdown by model."""
        # Log usage for different models
        await usage_service.log_usage(
            model="gpt-4o",
            prompt_tokens=1000,
            completion_tokens=500,
        )
        await usage_service.log_usage(
            model="gpt-4o-mini",
            prompt_tokens=2000,
            completion_tokens=1000,
        )

        model_usage = await usage_service.get_model_usage(days=30)

        # Should have both models
        models = [m["model"] for m in model_usage]
        assert "gpt-4o" in models or "gpt-4o-mini" in models

    async def test_log_error_status(self, usage_service: UsageLoggingService, db_session: AsyncSession):
        """Test logging with error status."""
        usage = await usage_service.log_usage(
            model="gpt-4o",
            prompt_tokens=100,
            completion_tokens=0,
            status="error",
            extra_data={"error": "Rate limited"},
        )

        assert usage.status == "error"
        assert usage.completion_tokens == 0
        assert usage.extra_data["error"] == "Rate limited"
