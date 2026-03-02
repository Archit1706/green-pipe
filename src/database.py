"""
Database setup for GreenPipe.

Uses SQLAlchemy 2.x with async support and Alembic for migrations.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from src.config import settings


class Base(DeclarativeBase):
    pass


def _make_async_url(url: str) -> str:
    """Convert a sync postgres:// URL to asyncpg driver URL."""
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+asyncpg://", 1)
    return url


engine: AsyncEngine = create_async_engine(
    _make_async_url(settings.database_url),
    echo=settings.app_env == "development",
    pool_pre_ping=True,
)

AsyncSessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    engine,
    expire_on_commit=False,
)


async def get_session() -> AsyncSession:
    """FastAPI dependency: yields a database session."""
    async with AsyncSessionLocal() as session:
        yield session


async def create_tables() -> None:
    """Create all tables (used for development; production uses Alembic)."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
