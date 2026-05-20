"""Session validation middleware.

Validates session tokens passed from the JS Backend and extracts user context.
The AI Backend does not manage sessions directly - it trusts tokens validated
by the JS Backend.
"""

from typing import Optional, Callable
from dataclasses import dataclass

from fastapi import Request, HTTPException, status
from fastapi.security import APIKeyHeader
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

# Header name for session token from JS Backend
SESSION_TOKEN_HEADER = "X-Session-Token"
USER_ID_HEADER = "X-User-ID"
USER_ROLE_HEADER = "X-User-Role"

# API key header for extracting session token
session_token_header = APIKeyHeader(name=SESSION_TOKEN_HEADER, auto_error=False)


@dataclass
class SessionContext:
    """Context extracted from session headers."""

    session_token: str
    user_id: Optional[int] = None
    user_role: str = "user"
    is_owner: bool = False

    @property
    def can_view_traces(self) -> bool:
        """Check if user can view prompt traces (Owner only)."""
        return self.is_owner or self.user_role == "owner"


async def get_session_context(request: Request) -> SessionContext:
    """Extract session context from request headers.

    The JS Backend is responsible for session management and authentication.
    This middleware trusts headers set by the JS Backend after it has
    validated the user's session.

    Headers expected:
    - X-Session-Token: Required. Session identifier.
    - X-User-ID: Optional. Numeric user ID.
    - X-User-Role: Optional. User role (user/admin/owner).

    Raises:
        HTTPException: If session token is missing or invalid.
    """
    session_token = request.headers.get(SESSION_TOKEN_HEADER)

    if not session_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing session token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Validate token format (basic sanity check)
    if len(session_token) < 10 or len(session_token) > 500:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid session token format",
        )

    # Extract optional user context
    user_id_str = request.headers.get(USER_ID_HEADER)
    user_id = int(user_id_str) if user_id_str and user_id_str.isdigit() else None

    user_role = request.headers.get(USER_ROLE_HEADER, "user").lower()
    if user_role not in ("user", "admin", "owner"):
        user_role = "user"

    return SessionContext(
        session_token=session_token,
        user_id=user_id,
        user_role=user_role,
        is_owner=(user_role == "owner"),
    )


class SessionValidationMiddleware(BaseHTTPMiddleware):
    """Middleware that validates session tokens on protected routes.

    Routes that don't require authentication (health checks, etc.) are skipped.
    """

    # Routes that don't require session validation
    PUBLIC_ROUTES = {
        "/health",
        "/ready",
        "/docs",
        "/redoc",
        "/openapi.json",
    }

    async def dispatch(self, request: Request, call_next: Callable):
        """Process the request and validate session if needed."""
        path = request.url.path

        # Skip validation for public routes
        if path in self.PUBLIC_ROUTES or path.startswith("/docs") or path.startswith("/redoc"):
            return await call_next(request)

        # Skip validation for non-API routes
        if not path.startswith("/api/"):
            return await call_next(request)

        # Validate session and attach context to request state
        try:
            session_context = await get_session_context(request)
            request.state.session = session_context
        except HTTPException as e:
            # Return proper JSON response for HTTP exceptions
            return JSONResponse(
                status_code=e.status_code,
                content={"detail": e.detail},
            )
        except Exception as e:
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={"detail": f"Session validation error: {str(e)}"},
            )

        return await call_next(request)


def require_owner(request: Request) -> SessionContext:
    """Dependency that requires owner role.

    Use this for endpoints that should only be accessible to owners,
    such as prompt trace inspection.
    """
    session: SessionContext = getattr(request.state, "session", None)

    if not session:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )

    if not session.can_view_traces:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Owner access required",
        )

    return session
