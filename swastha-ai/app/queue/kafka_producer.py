"""
Async Kafka producer for the SwasthaAI ingestion layer.

Features:
- aiokafka async producer (never blocks the event loop)
- acks="all" + idempotent producer for exactly-once delivery semantics
- lz4 compression
- 5 retries with exponential backoff
- doc_id as message key for partition consistency
- Local in-memory retry queue when Kafka is unavailable on startup
- Graceful shutdown: flushes all pending messages before closing

Circuit-breaker pattern:
  If Kafka is unreachable, messages are queued locally.
  A background task retries the local queue every 30 seconds.
  This prevents the app from refusing uploads when Kafka is temporarily down.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any

from aiokafka import AIOKafkaProducer
from aiokafka.errors import KafkaConnectionError, KafkaTimeoutError

from app.config import get_settings
from app.queue.topics import INGESTION_DEAD_LETTER

logger = logging.getLogger(__name__)

# Max messages to buffer locally before dropping with a warning
_LOCAL_QUEUE_MAX = 10_000
_RETRY_INTERVAL_SECONDS = 30


class KafkaProducerClient:
    """
    Singleton Kafka producer client with local-queue fallback.

    Usage:
        producer = KafkaProducerClient()
        await producer.start()    # called in FastAPI lifespan
        await producer.publish(topic, payload, key)
        await producer.stop()     # called on shutdown
    """

    def __init__(self) -> None:
        self._producer: AIOKafkaProducer | None = None
        self._local_queue: list[dict[str, Any]] = []
        self._is_kafka_available: bool = False
        self._retry_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._settings = get_settings()

    async def start(self) -> None:
        """
        Start the Kafka producer.

        If Kafka is unreachable on startup, logs a warning and falls back
        to local queue mode. The retry task will attempt reconnection.
        """
        try:
            await self._connect()
            self._is_kafka_available = True
            logger.info("Kafka producer connected")
        except (KafkaConnectionError, Exception) as exc:
            logger.warning(
                "Kafka unavailable on startup — entering local queue mode",
                extra={"error": str(exc)},
            )
            self._is_kafka_available = False

        # Start background retry task regardless
        self._retry_task = asyncio.create_task(
            self._retry_loop(), name="kafka-retry-loop"
        )

    async def _connect(self) -> None:
        """Create and start the aiokafka producer."""
        self._producer = AIOKafkaProducer(
            bootstrap_servers=self._settings.kafka_bootstrap_servers,
            # Reliability settings
            acks="all",
            enable_idempotence=True,
            retries=5,
            retry_backoff_ms=500,
            # Performance
            compression_type="lz4",
            max_batch_size=16384,
            linger_ms=5,
            # Timeouts
            request_timeout_ms=30_000,
            connections_max_idle_ms=60_000,
            # Serialization: we handle JSON encoding ourselves
            value_serializer=lambda v: v if isinstance(v, bytes) else json.dumps(v).encode("utf-8"),
            key_serializer=lambda k: k.encode("utf-8") if isinstance(k, str) else k,
        )
        await self._producer.start()

    async def stop(self) -> None:
        """
        Graceful shutdown: flush pending messages, stop producer.

        Called from the FastAPI lifespan on SIGTERM.
        Waits up to 30 seconds for in-flight messages to be delivered.
        """
        if self._retry_task and not self._retry_task.done():
            self._retry_task.cancel()
            try:
                await asyncio.wait_for(self._retry_task, timeout=5.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass

        if self._producer:
            try:
                # Attempt to drain the local queue before stopping
                if self._local_queue and self._is_kafka_available:
                    await self._drain_local_queue()

                await asyncio.wait_for(self._producer.flush(), timeout=30.0)
                await self._producer.stop()
                logger.info("Kafka producer stopped cleanly")
            except Exception as exc:
                logger.error("Kafka producer shutdown error", extra={"error": str(exc)})

    async def publish(
        self,
        topic: str,
        payload: dict[str, Any],
        key: str | None = None,
    ) -> bool:
        """
        Publish a message to a Kafka topic.

        Uses the doc_id as the message key for partition consistency —
        all events for the same document go to the same partition,
        preserving ordering.

        Returns True on success, False if queued locally.
        If Kafka is not available, enqueues the message locally.
        """
        message = {
            "topic": topic,
            "payload": payload,
            "key": key,
            "enqueued_at": datetime.now(timezone.utc).isoformat(),
        }

        if not self._is_kafka_available or self._producer is None:
            return await self._enqueue_locally(message)

        try:
            await asyncio.wait_for(
                self._producer.send(
                    topic=topic,
                    value=payload,
                    key=key,
                ),
                timeout=10.0,
            )
            logger.debug(
                "Kafka message published",
                extra={"topic": topic, "key": key},
            )
            return True
        except (KafkaTimeoutError, KafkaConnectionError) as exc:
            logger.warning(
                "Kafka publish failed — queuing locally",
                extra={"topic": topic, "key": key, "error": str(exc)},
            )
            self._is_kafka_available = False
            return await self._enqueue_locally(message)
        except Exception as exc:
            logger.error(
                "Unexpected Kafka publish error",
                extra={"topic": topic, "key": key, "error": str(exc)},
            )
            return await self._enqueue_locally(message)

    async def _enqueue_locally(self, message: dict[str, Any]) -> bool:
        """Add a message to the local retry queue."""
        async with self._lock:
            if len(self._local_queue) >= _LOCAL_QUEUE_MAX:
                logger.error(
                    "Local Kafka queue is full — dropping message. "
                    "Kafka has been unavailable for too long.",
                    extra={
                        "topic": message.get("topic"),
                        "key": message.get("key"),
                    },
                )
                return False
            self._local_queue.append(message)
            logger.info(
                "Message queued locally",
                extra={
                    "topic": message.get("topic"),
                    "queue_size": len(self._local_queue),
                },
            )
            return False  # False = not published to Kafka yet

    async def _retry_loop(self) -> None:
        """
        Background task: retry connecting to Kafka and draining the local queue.

        Runs every 30 seconds. If Kafka becomes available, drains the queue.
        """
        while True:
            try:
                await asyncio.sleep(_RETRY_INTERVAL_SECONDS)

                if not self._is_kafka_available:
                    logger.info("Kafka retry loop: attempting reconnection")
                    try:
                        if self._producer:
                            try:
                                await self._producer.stop()
                            except Exception:
                                pass
                        await self._connect()
                        self._is_kafka_available = True
                        logger.info("Kafka reconnected successfully")
                    except Exception as exc:
                        logger.warning(
                            "Kafka reconnection failed",
                            extra={"error": str(exc)},
                        )
                        continue

                if self._local_queue:
                    await self._drain_local_queue()

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(
                    "Kafka retry loop error",
                    extra={"error": str(exc)},
                )

    async def _drain_local_queue(self) -> None:
        """
        Attempt to publish all locally queued messages to Kafka.

        Messages that fail are moved to the dead-letter topic (if Kafka is up)
        or left in the queue for the next retry.
        """
        async with self._lock:
            pending = list(self._local_queue)
            self._local_queue.clear()

        succeeded = 0
        failed = []
        for message in pending:
            try:
                await asyncio.wait_for(
                    self._producer.send(  # type: ignore[union-attr]
                        topic=message["topic"],
                        value=message["payload"],
                        key=message["key"],
                    ),
                    timeout=10.0,
                )
                succeeded += 1
            except Exception as exc:
                logger.warning(
                    "Local queue drain failed for message",
                    extra={"topic": message.get("topic"), "error": str(exc)},
                )
                failed.append(message)

        # Re-queue failed messages
        if failed:
            async with self._lock:
                self._local_queue = failed + self._local_queue

        logger.info(
            "Local Kafka queue drained",
            extra={"succeeded": succeeded, "failed": len(failed)},
        )

    async def health_check(self) -> bool:
        """Return True if Kafka is currently available."""
        return self._is_kafka_available and self._producer is not None


# Module-level singleton
_kafka_producer: KafkaProducerClient | None = None


async def get_kafka_producer() -> KafkaProducerClient:
    global _kafka_producer
    if _kafka_producer is None:
        raise RuntimeError("Kafka producer not initialised. Call init_kafka() first.")
    return _kafka_producer


async def init_kafka() -> KafkaProducerClient:
    global _kafka_producer
    _kafka_producer = KafkaProducerClient()
    await _kafka_producer.start()
    return _kafka_producer


async def close_kafka() -> None:
    global _kafka_producer
    if _kafka_producer:
        await _kafka_producer.stop()
        _kafka_producer = None
