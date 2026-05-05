"""
FastAPI dependency injection providers.

All shared resources (database sessions, Redis client, MinIO, Kafka)
are provided as FastAPI dependencies so they can be injected into
route handlers and also mocked easily in tests.
"""

from __future__ import annotations

import logging
from typing import Any, AsyncGenerator

import redis.asyncio as aioredis
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.connection import get_db_session
from app.queue.kafka_producer import KafkaProducerClient, get_kafka_producer
from app.storage.document_store import DocumentStore
from app.storage.minio_client import MinIOClient, get_minio_client

logger = logging.getLogger(__name__)

# Module-level Redis client singleton (created during startup)
_redis_client: aioredis.Redis | None = None


# ── Redis ──────────────────────────────────────────────────────────────────────


async def init_redis() -> aioredis.Redis:
    """
    Initialise the Redis client singleton.

    Called from the FastAPI lifespan handler on app startup.
    """
    global _redis_client
    from app.config import get_settings
    settings = get_settings()

    _redis_client = aioredis.from_url(
        settings.redis_url,
        encoding="utf-8",
        decode_responses=True,
        socket_connect_timeout=5,
        socket_timeout=5,
        retry_on_timeout=True,
        health_check_interval=30,
    )
    # Verify connectivity
    await _redis_client.ping()
    logger.info("Redis client initialised")
    return _redis_client


async def close_redis() -> None:
    """Close the Redis client connection pool."""
    global _redis_client
    if _redis_client:
        await _redis_client.aclose()
        logger.info("Redis client closed")


async def get_redis() -> aioredis.Redis:
    """
    FastAPI dependency that provides the Redis client.

    Usage:
        redis: Redis = Depends(get_redis)
    """
    if _redis_client is None:
        raise RuntimeError("Redis client not initialised. Call init_redis() first.")
    return _redis_client


# ── Database ───────────────────────────────────────────────────────────────────


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency that provides an AsyncSession.

    Each request gets its own session that is closed after the response.
    """
    async for session in get_db_session():
        yield session


# ── MinIO ──────────────────────────────────────────────────────────────────────


async def get_minio() -> MinIOClient:
    """FastAPI dependency that provides the MinIO client singleton."""
    return await get_minio_client()


async def get_document_store(
    minio: MinIOClient = Depends(get_minio),
) -> DocumentStore:
    """FastAPI dependency that provides a DocumentStore backed by MinIO."""
    return DocumentStore(minio)


# ── Kafka ──────────────────────────────────────────────────────────────────────


async def get_kafka() -> KafkaProducerClient:
    """FastAPI dependency that provides the Kafka producer singleton."""
    return await get_kafka_producer()
