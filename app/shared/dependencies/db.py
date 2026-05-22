"""Database dependencies - Async."""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session_factory

SessionLocal = get_session_factory()


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Provide async database session."""

    async with SessionLocal() as session:
        yield session