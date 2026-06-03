"""Database session configuration."""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config import get_settings

settings = get_settings()

# Create async engine
engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
    pool_size=5,
    max_overflow=10,
    pool_timeout=10,
    pool_recycle=90,
    pool_pre_ping=True,
)

# Session factory - used by FastAPI dependencies and background tasks
AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)

# Alias for backward compatibility
async_session_factory = AsyncSessionLocal


async def get_async_session() -> AsyncSession:
    """Create a new async session."""
    async with async_session_factory() as session:
        yield session
