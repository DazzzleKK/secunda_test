"""Async engine and session factory."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

DEFAULT_POOL_SIZE = 5
DEFAULT_MAX_OVERFLOW = 10


def build_engine(
    database_url: str,
    *,
    echo: bool = False,
    pool_size: int = DEFAULT_POOL_SIZE,
    max_overflow: int = DEFAULT_MAX_OVERFLOW,
) -> AsyncEngine:
    """Create the async engine used by one process."""
    return create_async_engine(
        database_url,
        echo=echo,
        pool_pre_ping=True,
        pool_size=pool_size,
        max_overflow=max_overflow,
    )


def build_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Create a session factory; sessions never expire objects on commit."""
    return async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


__all__ = ["build_engine", "build_session_factory"]
