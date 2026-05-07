"""
Database connection management using SQLAlchemy 2.0 async with asyncpg driver.

The engine and session factory are module-level singletons.
FastAPI lifespan handlers call init_db() on startup and close_db() on shutdown.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.config import get_settings

logger = logging.getLogger(__name__)

# Module-level engine and session factory — created once on startup
_engine: AsyncEngine | None = None
_async_session_factory: async_sessionmaker[AsyncSession] | None = None


def _build_engine(database_url: str, pool_size: int = 10) -> AsyncEngine:
    """
    Create an async SQLAlchemy engine with asyncpg.

    Supabase uses PgBouncer in transaction-pooling mode (port 6543).
    PgBouncer does NOT support prepared statements, so statement_cache_size=0
    prevents asyncpg from caching them. Safe for direct connections too.
    """
    settings = get_settings()

    connect_args: dict = {
        "command_timeout": 30,
        # Required for Supabase transaction pooler (PgBouncer).
        "statement_cache_size": 0,
        "server_settings": {
            "application_name": "swastha-ai",
        },
    }

    # Use NullPool in test environments to avoid connection sharing across tests
    if settings.environment == "development" and database_url.endswith("_test"):
        return create_async_engine(
            database_url,
            echo=False,
            poolclass=NullPool,
            connect_args=connect_args,
        )

    return create_async_engine(
        database_url,
        echo=settings.environment == "development",
        pool_size=pool_size,
        max_overflow=20,
        pool_pre_ping=True,
        pool_recycle=3600,
        connect_args=connect_args,
    )


async def init_db(database_url: str | None = None) -> None:
    """
    Initialise the database engine and session factory.

    Called from the FastAPI lifespan context manager on app startup.
    """
    global _engine, _async_session_factory

    settings = get_settings()
    url = database_url or settings.database_url

    _engine = _build_engine(url)
    _async_session_factory = async_sessionmaker(
        bind=_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
        autocommit=False,
    )

    logger.info("Database engine initialised", extra={"url": url.split("@")[-1]})


async def close_db() -> None:
    """
    Dispose of the database engine pool.

    Called from the FastAPI lifespan context manager on app shutdown.
    """
    global _engine
    if _engine is not None:
        await _engine.dispose()
        logger.info("Database engine disposed")
        _engine = None


def get_engine() -> AsyncEngine:
    if _engine is None:
        raise RuntimeError("Database engine not initialised. Call init_db() first.")
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    if _async_session_factory is None:
        raise RuntimeError("Session factory not initialised. Call init_db() first.")
    return _async_session_factory


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency that yields an AsyncSession and ensures it's closed.

    Usage:
        db: AsyncSession = Depends(get_db_session)
    """
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
