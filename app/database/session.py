"""
Database Engine & Session Factory — app/database/session.py

Sets up the async SQLAlchemy engine and session factory.
Uses asyncpg as the async PostgreSQL driver.

Design decisions:
- AsyncSession is used throughout to keep FastAPI fully non-blocking.
- The session is provided via FastAPI's dependency injection system.
- Connection pool settings are configurable via environment variables
  to tune for different deployment scales (development vs. production).
- get_db() yields a session per-request and commits/rolls back automatically.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


def _build_engine_kwargs(settings: Any) -> dict[str, Any]:
    """
    Build keyword arguments for create_async_engine based on settings.

    NullPool is used in test environments to avoid connection pool issues
    with pytest-asyncio's event loop management.
    """
    kwargs: dict[str, Any] = {
        "echo": settings.database_echo,
        "future": True,
    }

    # In tests, override DB_URL will use NullPool to prevent leaks
    if settings.app_env == "development" or settings.app_env == "production":
        kwargs["pool_size"] = settings.database_pool_size
        kwargs["max_overflow"] = settings.database_max_overflow
        kwargs["pool_timeout"] = settings.database_pool_timeout
        kwargs["pool_pre_ping"] = True  # Detect stale connections

    return kwargs


def create_engine(database_url: str | None = None):
    """
    Create and return the async SQLAlchemy engine.

    Args:
        database_url: Override the URL from settings (used in tests).

    Returns:
        AsyncEngine instance.
    """
    settings = get_settings()
    url = database_url or settings.database_url
    kwargs = _build_engine_kwargs(settings)
    return create_async_engine(url, **kwargs)


# Module-level engine and session factory singletons
# These are created once at import time and reused for the app lifetime.
_engine = None
_AsyncSessionLocal = None


def get_engine():
    """Return the module-level engine, creating it if necessary."""
    global _engine
    if _engine is None:
        _engine = create_engine()
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return the module-level async session factory."""
    global _AsyncSessionLocal
    if _AsyncSessionLocal is None:
        _AsyncSessionLocal = async_sessionmaker(
            bind=get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,  # Avoid lazy-load issues after commit
            autocommit=False,
            autoflush=False,
        )
    return _AsyncSessionLocal


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency that yields an async database session per request.

    The session is committed on success and rolled back on any exception.
    Always closed after the response is sent.

    Usage in route::

        @router.get("/example")
        async def example(db: AsyncSession = Depends(get_db)):
            ...
    """
    AsyncSessionLocal = get_session_factory()
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
