"""
Database setup for GreenPipe.

Uses SQLAlchemy 2.x with async support and Alembic for migrations.
Engine creation is lazy so the module can be imported without a running DB.
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


# Module-level engine/session are None until _init_engine() is called.
_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def _init_engine() -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    """Initialise the engine and session factory on first use."""
    global _engine, _session_factory
    if _engine is None:
        _engine = create_async_engine(
            _make_async_url(settings.database_url),
            echo=settings.app_env == "development",
            pool_pre_ping=True,
        )
        _session_factory = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine, _session_factory


def get_engine() -> AsyncEngine:
    engine, _ = _init_engine()
    return engine


async def get_session() -> AsyncSession:
    """FastAPI dependency: yields a database session."""
    _, factory = _init_engine()
    async with factory() as session:
        yield session


async def create_tables() -> None:
    """Create all tables (used for development; production uses Alembic)."""
    engine, _ = _init_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
