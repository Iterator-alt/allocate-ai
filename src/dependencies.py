"""Dependency injection for FastAPI."""

from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession

from src.db.session import async_session_factory


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency that provides a database session."""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
