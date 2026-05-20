"""Unit tests for OpenAI Client with mocked responses."""

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime

from src.services.llm_gateway import (
    OpenAIClient,
    LLMResponse,
    CircuitState,
)


class TestOpenAIClient:
    """Tests for OpenAI client wrapper."""

    @pytest.fixture
    def mock_openai_response(self):
        """Create a mock OpenAI response."""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = '{"allocations": [], "summary": "Test"}'
        mock_response.choices[0].finish_reason = "stop"
        mock_response.model = "gpt-4o"
        mock_response.usage = MagicMock()
        mock_response.usage.prompt_tokens = 100
        mock_response.usage.completion_tokens = 50
        mock_response.usage.total_tokens = 150
        return mock_response

    @pytest.fixture
    def client(self):
        """Create client with test API key."""
        return OpenAIClient(
            api_key="test-key",
            model="gpt-4o",
            timeout_seconds=45,
            max_retries=3,
        )

    @pytest.mark.asyncio
    async def test_generate_success(self, client, mock_openai_response):
        """Test successful LLM generation."""
        with patch.object(
            client._client.chat.completions,
            "create",
            new_callable=AsyncMock,
            return_value=mock_openai_response,
        ):
            response = await client.generate(
                system_prompt="You are a helpful assistant.",
                user_prompt="Say hello.",
                json_mode=True,
            )

            assert isinstance(response, LLMResponse)
            assert response.content == '{"allocations": [], "summary": "Test"}'
            assert response.parsed_json is not None
            assert response.parsed_json["summary"] == "Test"
            assert response.model == "gpt-4o"
            assert response.prompt_tokens == 100
            assert response.completion_tokens == 50
            assert response.total_tokens == 150

    @pytest.mark.asyncio
    async def test_generate_parses_json(self, client, mock_openai_response):
        """Test that JSON mode parses response."""
        mock_openai_response.choices[0].message.content = '{"key": "value", "number": 42}'

        with patch.object(
            client._client.chat.completions,
            "create",
            new_callable=AsyncMock,
            return_value=mock_openai_response,
        ):
            response = await client.generate(
                system_prompt="System",
                user_prompt="User",
                json_mode=True,
            )

            assert response.parsed_json == {"key": "value", "number": 42}

    @pytest.mark.asyncio
    async def test_generate_handles_invalid_json(self, client, mock_openai_response):
        """Test handling of invalid JSON response."""
        mock_openai_response.choices[0].message.content = "Not valid JSON"

        with patch.object(
            client._client.chat.completions,
            "create",
            new_callable=AsyncMock,
            return_value=mock_openai_response,
        ):
            response = await client.generate(
                system_prompt="System",
                user_prompt="User",
                json_mode=True,
            )

            # Should still return response but with None parsed_json
            assert response.content == "Not valid JSON"
            assert response.parsed_json is None

    @pytest.mark.asyncio
    async def test_retry_on_timeout(self, client, mock_openai_response):
        """Test retry on timeout error."""
        from openai import APITimeoutError

        call_count = 0

        async def mock_create(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise APITimeoutError(request=MagicMock())
            return mock_openai_response

        with patch.object(
            client._client.chat.completions,
            "create",
            side_effect=mock_create,
        ):
            response = await client.generate(
                system_prompt="System",
                user_prompt="User",
            )

            assert call_count == 3
            assert response.content is not None

    @pytest.mark.asyncio
    async def test_circuit_breaker_opens_on_failures(self, client):
        """Test circuit breaker opens after threshold failures."""
        from openai import APIError

        # Simulate 3 failures
        for _ in range(3):
            with patch.object(
                client._client.chat.completions,
                "create",
                side_effect=APIError(
                    message="Server error",
                    request=MagicMock(),
                    body=None,
                ),
            ):
                try:
                    await client.generate(
                        system_prompt="System",
                        user_prompt="User",
                    )
                except Exception:
                    pass

        # Circuit should now be open
        state = client.get_circuit_state()
        assert state["state"] == CircuitState.OPEN.value

    @pytest.mark.asyncio
    async def test_circuit_breaker_rejects_when_open(self, client):
        """Test that circuit breaker rejects requests when open."""
        # Manually set circuit to open
        client._circuit.state = CircuitState.OPEN
        client._circuit.last_failure_time = datetime.utcnow()

        with pytest.raises(Exception) as exc_info:
            await client.generate(
                system_prompt="System",
                user_prompt="User",
            )

        assert "Circuit breaker is open" in str(exc_info.value)

    def test_circuit_breaker_reset(self, client):
        """Test manual circuit breaker reset."""
        client._circuit.state = CircuitState.OPEN
        client._circuit.failure_count = 5

        client.reset_circuit()

        state = client.get_circuit_state()
        assert state["state"] == CircuitState.CLOSED.value
        assert state["failure_count"] == 0

    def test_backoff_time_calculation(self, client):
        """Test exponential backoff calculation."""
        # First attempt should be ~1 second
        backoff1 = client._get_backoff_time(1)
        assert 0.75 <= backoff1 <= 1.25

        # Second attempt should be ~2 seconds
        backoff2 = client._get_backoff_time(2)
        assert 1.5 <= backoff2 <= 2.5

        # Third attempt should be ~4 seconds
        backoff3 = client._get_backoff_time(3)
        assert 3.0 <= backoff3 <= 5.0

    @pytest.mark.asyncio
    async def test_latency_tracking(self, client, mock_openai_response):
        """Test that latency is tracked."""
        with patch.object(
            client._client.chat.completions,
            "create",
            new_callable=AsyncMock,
            return_value=mock_openai_response,
        ):
            response = await client.generate(
                system_prompt="System",
                user_prompt="User",
            )

            # Latency should be recorded (at least 0ms)
            assert response.latency_ms >= 0

    @pytest.mark.asyncio
    async def test_finish_reason_captured(self, client, mock_openai_response):
        """Test that finish reason is captured."""
        mock_openai_response.choices[0].finish_reason = "length"

        with patch.object(
            client._client.chat.completions,
            "create",
            new_callable=AsyncMock,
            return_value=mock_openai_response,
        ):
            response = await client.generate(
                system_prompt="System",
                user_prompt="User",
            )

            assert response.finish_reason == "length"


class TestCircuitBreakerState:
    """Tests for circuit breaker state management."""

    @pytest.fixture
    def client(self):
        return OpenAIClient(api_key="test-key")

    def test_initial_state_is_closed(self, client):
        """Test that circuit starts in closed state."""
        state = client.get_circuit_state()
        assert state["state"] == CircuitState.CLOSED.value
        assert state["failure_count"] == 0

    def test_record_success_resets_half_open(self, client):
        """Test that success in half-open state closes circuit."""
        client._circuit.state = CircuitState.HALF_OPEN
        client._circuit.failure_count = 3

        client._record_success()

        assert client._circuit.state == CircuitState.CLOSED
        assert client._circuit.failure_count == 0

    def test_record_failure_increments_count(self, client):
        """Test that failure increments counter."""
        initial_count = client._circuit.failure_count

        client._record_failure()

        assert client._circuit.failure_count == initial_count + 1
        assert client._circuit.last_failure_time is not None
