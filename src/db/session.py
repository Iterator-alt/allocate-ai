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
    pool_recycle=90,  # Recycle connections every 90 seconds to prevent stale connections
    connect_args={
        "server_settings": {
            "tcp_keepalives_idle": "30",      # Start keepalive after 30s idle
            "tcp_keepalives_interval": "10",  # Send keepalive every 10s
            "tcp_keepalives_count": "5",      # Fail after 5 missed keepalives
        }
    },
)

# Session factory
async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


async def get_async_session() -> AsyncSession:
    """Create a new async session."""
    async with async_session_factory() as session:
        yield session
