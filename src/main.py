"""FastAPI application entry point."""

import logging
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.dependencies import get_db
from src.api.middleware import (
    SessionValidationMiddleware,
    limiter,
    rate_limit_exceeded_handler,
)

logger = logging.getLogger(__name__)
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler for startup/shutdown events."""
    # Startup
    logger.info("Allocate.AI Backend starting up...")
    yield
    # Shutdown
    logger.info("Allocate.AI Backend shutting down...")


app = FastAPI(
    title="Allocate.AI",
    description="AI Backend for media budget allocation",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs" if settings.debug else None,
    redoc_url="/redoc" if settings.debug else None,
)

# Add rate limiter state
app.state.limiter = limiter

# Rate limit exceeded handler
app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)

# Session validation middleware
app.add_middleware(SessionValidationMiddleware)

# CORS middleware for internal JS Backend communication
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Restrict in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health_check():
    """Basic health check endpoint.

    Returns 200 if the application is running.
    Does not check external dependencies.
    """
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "version": "0.1.0",
    }


@app.get("/ready")
async def readiness_check(db: AsyncSession = Depends(get_db)):
    """Readiness check with dependency verification.

    Checks:
    - Database connectivity
    - Returns detailed status for orchestrators (K8s, Docker)
    """
    checks = {
        "database": False,
        "timestamp": datetime.utcnow().isoformat(),
    }

    # Check database connectivity
    try:
        result = await db.execute(text("SELECT 1"))
        result.scalar()
        checks["database"] = True
    except Exception as e:
        logger.error(f"Database health check failed: {e}")
        checks["database_error"] = str(e)

    # Overall status
    all_healthy = all(
        checks.get(k) is True
        for k in ["database"]
    )

    return {
        "status": "ready" if all_healthy else "degraded",
        "checks": checks,
    }


# Import and include API routers
from src.api.v1 import runs, competitors, results, traces

app.include_router(runs.router, prefix="/api/v1")
app.include_router(competitors.router, prefix="/api/v1")
app.include_router(results.router, prefix="/api/v1")
app.include_router(traces.router, prefix="/api/v1")
