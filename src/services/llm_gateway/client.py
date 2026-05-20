"""OpenAI client wrapper with retry and circuit breaker.

Provides a resilient interface for LLM API calls with:
- JSON mode for structured output
- Configurable timeout (default 45s)
- Retry with exponential backoff (3 attempts)
- Circuit breaker (opens after 3 failures in 10 min)
"""

import json
import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
from enum import Enum

from openai import AsyncOpenAI, APIError, APITimeoutError, RateLimitError

from src.config import get_settings

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    """Circuit breaker states."""

    CLOSED = "closed"  # Normal operation
    OPEN = "open"  # Failing, reject calls
    HALF_OPEN = "half_open"  # Testing if recovered


@dataclass
class LLMResponse:
    """Response from LLM call."""

    content: str
    parsed_json: Optional[Dict[str, Any]]
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    latency_ms: int
    finish_reason: str


@dataclass
class LLMError:
    """Error from LLM call."""

    error_type: str
    message: str
    is_retryable: bool
    attempt: int


@dataclass
class CircuitBreakerState:
    """Tracks circuit breaker state."""

    state: CircuitState = CircuitState.CLOSED
    failure_count: int = 0
    last_failure_time: Optional[datetime] = None
    failures_in_window: List[datetime] = field(default_factory=list)

    # Configuration
    failure_threshold: int = 3  # Open after 3 failures
    recovery_timeout: timedelta = timedelta(minutes=10)  # Wait before half-open
    window_size: timedelta = timedelta(minutes=10)  # Window for counting failures


class OpenAIClient:
    """Resilient OpenAI client with retry and circuit breaker."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "gpt-4o",
        timeout_seconds: int = 45,
        max_retries: int = 3,
    ):
        """Initialize the client.

        Args:
            api_key: OpenAI API key (defaults to settings)
            model: Model to use (default gpt-4o)
            timeout_seconds: Request timeout (default 45s)
            max_retries: Maximum retry attempts (default 3)
        """
        settings = get_settings()
        self.api_key = api_key or settings.openai_api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries

        # Initialize OpenAI client
        self._client = AsyncOpenAI(
            api_key=self.api_key,
            timeout=float(self.timeout_seconds),
        )

        # Circuit breaker state
        self._circuit = CircuitBreakerState()

    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        json_mode: bool = True,
    ) -> LLMResponse:
        """Generate a response from the LLM.

        Args:
            system_prompt: System message
            user_prompt: User message
            temperature: Sampling temperature (0-2)
            max_tokens: Maximum tokens in response
            json_mode: Whether to use JSON mode

        Returns:
            LLMResponse with content and metadata

        Raises:
            LLMError: If all retries fail or circuit is open
        """
        # Check circuit breaker
        if not self._can_make_request():
            raise Exception(
                f"Circuit breaker is {self._circuit.state.value}. "
                "Too many recent failures, please wait before retrying."
            )

        last_error: Optional[LLMError] = None

        for attempt in range(1, self.max_retries + 1):
            try:
                response = await self._make_request(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    json_mode=json_mode,
                )
                # Success - reset circuit breaker
                self._record_success()
                return response

            except (APIError, APITimeoutError, RateLimitError) as e:
                last_error = self._handle_api_error(e, attempt)

                if last_error.is_retryable and attempt < self.max_retries:
                    # Wait with exponential backoff
                    wait_time = self._get_backoff_time(attempt)
                    logger.warning(
                        f"LLM request failed (attempt {attempt}/{self.max_retries}), "
                        f"retrying in {wait_time}s: {last_error.message}"
                    )
                    await asyncio.sleep(wait_time)
                else:
                    # Record failure for circuit breaker
                    self._record_failure()
                    raise Exception(
                        f"LLM request failed after {attempt} attempts: {last_error.message}"
                    )

            except Exception as e:
                # Unexpected error - record failure and raise
                self._record_failure()
                logger.error(f"Unexpected LLM error: {str(e)}")
                raise

        # Should not reach here, but just in case
        if last_error:
            raise Exception(f"LLM request failed: {last_error.message}")

    async def _make_request(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        max_tokens: int,
        json_mode: bool,
    ) -> LLMResponse:
        """Make the actual API request."""
        start_time = datetime.utcnow()

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        # Build request kwargs
        kwargs: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        # Add JSON mode if enabled
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        response = await self._client.chat.completions.create(**kwargs)

        end_time = datetime.utcnow()
        latency_ms = int((end_time - start_time).total_seconds() * 1000)

        # Extract response content
        content = response.choices[0].message.content or ""
        finish_reason = response.choices[0].finish_reason

        # Try to parse JSON if in JSON mode
        parsed_json = None
        if json_mode and content:
            try:
                parsed_json = json.loads(content)
            except json.JSONDecodeError:
                logger.warning("Failed to parse LLM response as JSON")

        return LLMResponse(
            content=content,
            parsed_json=parsed_json,
            model=response.model,
            prompt_tokens=response.usage.prompt_tokens if response.usage else 0,
            completion_tokens=response.usage.completion_tokens if response.usage else 0,
            total_tokens=response.usage.total_tokens if response.usage else 0,
            latency_ms=latency_ms,
            finish_reason=finish_reason,
        )

    def _handle_api_error(self, error: Exception, attempt: int) -> LLMError:
        """Convert API error to LLMError."""
        if isinstance(error, APITimeoutError):
            return LLMError(
                error_type="timeout",
                message=f"Request timed out after {self.timeout_seconds}s",
                is_retryable=True,
                attempt=attempt,
            )
        elif isinstance(error, RateLimitError):
            return LLMError(
                error_type="rate_limit",
                message="Rate limit exceeded",
                is_retryable=True,
                attempt=attempt,
            )
        elif isinstance(error, APIError):
            # Check if it's a retryable server error
            is_retryable = getattr(error, "status_code", 0) >= 500
            return LLMError(
                error_type="api_error",
                message=str(error),
                is_retryable=is_retryable,
                attempt=attempt,
            )
        else:
            return LLMError(
                error_type="unknown",
                message=str(error),
                is_retryable=False,
                attempt=attempt,
            )

    def _get_backoff_time(self, attempt: int) -> float:
        """Calculate exponential backoff time."""
        # Base delay of 1 second, doubling each attempt
        # With jitter to avoid thundering herd
        import random

        base_delay = 1.0
        max_delay = 30.0
        delay = min(base_delay * (2 ** (attempt - 1)), max_delay)

        # Add jitter (±25%)
        jitter = delay * 0.25 * (random.random() * 2 - 1)
        return delay + jitter

    def _can_make_request(self) -> bool:
        """Check if circuit breaker allows requests."""
        self._update_circuit_state()

        if self._circuit.state == CircuitState.CLOSED:
            return True
        elif self._circuit.state == CircuitState.HALF_OPEN:
            # Allow one test request
            return True
        else:  # OPEN
            return False

    def _update_circuit_state(self) -> None:
        """Update circuit breaker state based on time and failures."""
        now = datetime.utcnow()

        # Clean up old failures outside the window
        self._circuit.failures_in_window = [
            f for f in self._circuit.failures_in_window
            if now - f < self._circuit.window_size
        ]

        if self._circuit.state == CircuitState.OPEN:
            # Check if recovery timeout has passed
            if self._circuit.last_failure_time:
                time_since_failure = now - self._circuit.last_failure_time
                if time_since_failure >= self._circuit.recovery_timeout:
                    logger.info("Circuit breaker transitioning to HALF_OPEN")
                    self._circuit.state = CircuitState.HALF_OPEN

    def _record_success(self) -> None:
        """Record a successful request."""
        if self._circuit.state == CircuitState.HALF_OPEN:
            logger.info("Circuit breaker transitioning to CLOSED")
            self._circuit.state = CircuitState.CLOSED
            self._circuit.failure_count = 0
            self._circuit.failures_in_window = []

    def _record_failure(self) -> None:
        """Record a failed request."""
        now = datetime.utcnow()
        self._circuit.last_failure_time = now
        self._circuit.failures_in_window.append(now)
        self._circuit.failure_count += 1

        # Check if we should open the circuit
        if len(self._circuit.failures_in_window) >= self._circuit.failure_threshold:
            if self._circuit.state != CircuitState.OPEN:
                logger.warning(
                    f"Circuit breaker OPENING after {len(self._circuit.failures_in_window)} "
                    f"failures in {self._circuit.window_size}"
                )
                self._circuit.state = CircuitState.OPEN

        # If we were half-open and failed, go back to open
        if self._circuit.state == CircuitState.HALF_OPEN:
            logger.warning("Circuit breaker returning to OPEN after test failure")
            self._circuit.state = CircuitState.OPEN

    def get_circuit_state(self) -> Dict[str, Any]:
        """Get current circuit breaker state for monitoring."""
        self._update_circuit_state()
        return {
            "state": self._circuit.state.value,
            "failure_count": self._circuit.failure_count,
            "failures_in_window": len(self._circuit.failures_in_window),
            "last_failure_time": (
                self._circuit.last_failure_time.isoformat()
                if self._circuit.last_failure_time
                else None
            ),
        }

    def reset_circuit(self) -> None:
        """Manually reset the circuit breaker (for testing/admin)."""
        logger.info("Circuit breaker manually reset")
        self._circuit = CircuitBreakerState()
