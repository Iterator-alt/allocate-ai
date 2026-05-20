"""Rate limiting middleware using slowapi.

Implements two rate limits as per scope:
- 20 allocation generation requests per user per hour
- 100 req/min general on all AI backend routes
"""

from typing import Callable

from fastapi import Request, Response
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from starlette.responses import JSONResponse

from src.config import get_settings

settings = get_settings()


def get_session_token(request: Request) -> str:
    """Get rate limit key from session token or IP address.

    Uses session token if available (for per-user limits),
    falls back to IP address for unauthenticated requests.
    """
    # Try to get session token from header
    session_token = request.headers.get("X-Session-Token")
    if session_token:
        return f"session:{session_token}"

    # Fall back to IP address
    return f"ip:{get_remote_address(request)}"


def get_user_id_or_session(request: Request) -> str:
    """Get rate limit key prioritizing user ID.

    For generation limits, we want to track by user ID if available,
    then session token, then IP.
    """
    user_id = request.headers.get("X-User-ID")
    if user_id:
        return f"user:{user_id}"

    session_token = request.headers.get("X-Session-Token")
    if session_token:
        return f"session:{session_token}"

    return f"ip:{get_remote_address(request)}"


# Create limiter instance
limiter = Limiter(
    key_func=get_session_token,
    default_limits=["100/minute"],  # General rate limit
    storage_uri="memory://",  # In-memory for MVP, use Redis for production
)


# Rate limit decorators for specific endpoints
GENERATION_LIMIT = f"{settings.rate_limit_generations_per_hour}/hour"


def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> Response:
    """Custom handler for rate limit exceeded errors."""
    return JSONResponse(
        status_code=429,
        content={
            "error": "Rate limit exceeded",
            "detail": str(exc.detail),
            "retry_after": getattr(exc, "retry_after", None),
        },
        headers={
            "Retry-After": str(getattr(exc, "retry_after", 60)),
            "X-RateLimit-Limit": str(exc.detail).split()[0] if exc.detail else "unknown",
        },
    )


def get_limiter() -> Limiter:
    """Get the limiter instance for use in route decorators."""
    return limiter


# Convenience decorators for common rate limits
def generation_rate_limit():
    """Decorator for generation endpoint rate limiting.

    Applies: 20 requests per user per hour
    """
    return limiter.limit(
        GENERATION_LIMIT,
        key_func=get_user_id_or_session,
        error_message="Generation rate limit exceeded. Maximum 20 generations per hour.",
    )


def general_rate_limit():
    """Decorator for general API rate limiting.

    Applies: 100 requests per minute
    """
    return limiter.limit(
        "100/minute",
        key_func=get_session_token,
        error_message="API rate limit exceeded. Please slow down.",
    )
