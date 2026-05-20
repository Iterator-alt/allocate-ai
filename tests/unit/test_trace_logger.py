"""Tests for prompt trace logger service."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import PromptTrace
from src.services.llm_gateway.client import LLMResponse
from src.services.llm_gateway.trace_logger import PromptTraceLogger


class TestPromptTraceLogger:
    """Tests for PromptTraceLogger."""

    @pytest_asyncio.fixture
    async def trace_logger(self, db_session: AsyncSession):
        """Create a trace logger instance."""
        return PromptTraceLogger(db_session)

    async def test_start_trace(self, trace_logger: PromptTraceLogger, db_session: AsyncSession):
        """Test starting a new trace."""
        trace = await trace_logger.start_trace(
            run_id=1,
            model="gpt-4o",
            prompt="Test prompt content",
        )

        assert trace.id is not None
        assert trace.run_id == 1
        assert trace.model == "gpt-4o"
        assert trace.prompt == "Test prompt content"
        assert trace.status == "pending"
        assert trace.called_at is not None

    async def test_complete_trace(self, trace_logger: PromptTraceLogger, db_session: AsyncSession):
        """Test completing a trace with success."""
        # Start a trace
        trace = await trace_logger.start_trace(
            run_id=1,
            model="gpt-4o",
            prompt="Test prompt",
        )

        # Create mock response
        response = LLMResponse(
            content='{"result": "test"}',
            parsed_json={"result": "test"},
            model="gpt-4o",
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
            latency_ms=1500,
            finish_reason="stop",
        )

        # Complete the trace
        completed = await trace_logger.complete_trace(trace.id, response)

        assert completed is not None
        assert completed.status == "success"
        assert completed.response == '{"result": "test"}'
        assert completed.prompt_tokens == 100
        assert completed.completion_tokens == 50
        assert completed.total_tokens == 150
        assert completed.latency_ms == 1500
        assert completed.error_message is None

    async def test_fail_trace(self, trace_logger: PromptTraceLogger, db_session: AsyncSession):
        """Test failing a trace."""
        # Start a trace
        trace = await trace_logger.start_trace(
            run_id=1,
            model="gpt-4o",
            prompt="Test prompt",
        )

        # Fail the trace
        failed = await trace_logger.fail_trace(
            trace.id,
            error_message="API rate limit exceeded",
            status="error",
            latency_ms=500,
        )

        assert failed is not None
        assert failed.status == "error"
        assert failed.error_message == "API rate limit exceeded"
        assert failed.latency_ms == 500

    async def test_fail_trace_timeout(self, trace_logger: PromptTraceLogger, db_session: AsyncSession):
        """Test failing a trace with timeout status."""
        trace = await trace_logger.start_trace(
            run_id=1,
            model="gpt-4o",
            prompt="Test prompt",
        )

        failed = await trace_logger.fail_trace(
            trace.id,
            error_message="Request timed out after 45s",
            status="timeout",
            latency_ms=45000,
        )

        assert failed.status == "timeout"

    async def test_log_complete_call(self, trace_logger: PromptTraceLogger, db_session: AsyncSession):
        """Test logging a complete call in one operation."""
        response = LLMResponse(
            content="Test response",
            parsed_json=None,
            model="gpt-4o",
            prompt_tokens=200,
            completion_tokens=100,
            total_tokens=300,
            latency_ms=2000,
            finish_reason="stop",
        )

        trace = await trace_logger.log_complete_call(
            run_id=2,
            model="gpt-4o",
            prompt="Full prompt here",
            response=response,
        )

        assert trace.id is not None
        assert trace.run_id == 2
        assert trace.status == "success"
        assert trace.response == "Test response"
        assert trace.total_tokens == 300

    async def test_log_complete_call_error(self, trace_logger: PromptTraceLogger, db_session: AsyncSession):
        """Test logging a failed call in one operation."""
        trace = await trace_logger.log_complete_call(
            run_id=3,
            model="gpt-4o",
            prompt="Failed prompt",
            error_message="Connection refused",
            status="error",
        )

        assert trace.status == "error"
        assert trace.error_message == "Connection refused"
        assert trace.response is None

    async def test_get_run_statistics(self, trace_logger: PromptTraceLogger, db_session: AsyncSession):
        """Test getting statistics for a run."""
        # Create some traces
        for i in range(3):
            response = LLMResponse(
                content=f"Response {i}",
                parsed_json=None,
                model="gpt-4o",
                prompt_tokens=100 + i * 10,
                completion_tokens=50 + i * 5,
                total_tokens=150 + i * 15,
                latency_ms=1000 + i * 100,
                finish_reason="stop",
            )
            await trace_logger.log_complete_call(
                run_id=10,
                model="gpt-4o",
                prompt=f"Prompt {i}",
                response=response,
            )

        stats = await trace_logger.get_run_statistics(10)

        assert stats["total_calls"] == 3
        assert stats["total_tokens"] == 150 + 165 + 180  # 495

    async def test_complete_nonexistent_trace(self, trace_logger: PromptTraceLogger, db_session: AsyncSession):
        """Test completing a non-existent trace returns None."""
        response = LLMResponse(
            content="Test",
            parsed_json=None,
            model="gpt-4o",
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
            latency_ms=100,
            finish_reason="stop",
        )

        result = await trace_logger.complete_trace(99999, response)
        assert result is None

    async def test_fail_nonexistent_trace(self, trace_logger: PromptTraceLogger, db_session: AsyncSession):
        """Test failing a non-existent trace returns None."""
        result = await trace_logger.fail_trace(99999, "Error")
        assert result is None
