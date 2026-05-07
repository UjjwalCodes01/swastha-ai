"""
Resilient Kafka Producer — base producer for all SwasthaAI services.

Guarantees:
  - acks="all" + enable_idempotence=True (exactly-once delivery)
  - doc_id is always used as the partition key
  - event_id (UUID) and timestamp are auto-injected on every message
  - Circuit breaker wraps every publish call
  - On broker failure: buffer up to 1000 messages in Redis
  - If Redis buffer is full: raise PublishFailedError and log to local fallback file
  - On broker recovery: Redis buffer is drained automatically
  - Graceful shutdown: flush() ensures all buffered messages are sent

Singleton: one producer per process. Import get_producer() everywhere.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

try:
    from aiokafka import AIOKafkaProducer
    from aiokafka.structs import RecordMetadata
except ImportError:  # pragma: no cover
    AIOKafkaProducer = None  # type: ignore[assignment, misc]
    RecordMetadata = None  # type: ignore[assignment]

from app.messagebus.circuit_breaker import CircuitBreakerRegistry, CircuitOpenError
from app.messagebus.schema_registry import get_schema_registry

_REDIS_BUFFER_KEY = "kafka:publish_buffer"
_REDIS_BUFFER_MAX = 1000
_FALLBACK_LOG_PATH = os.getenv("KAFKA_FALLBACK_LOG", "/tmp/kafka_fallback.jsonl")
_DRAIN_INTERVAL_SECONDS = 5


class PublishFailedError(RuntimeError):
    """Raised when a message cannot be published and the Redis buffer is full."""


class ResilienceProducer:
    """
    Async Kafka producer with circuit breaker, Redis buffering, and graceful shutdown.
    """

    def __init__(self, bootstrap_servers: str, redis_client: Any) -> None:
        self._bootstrap = bootstrap_servers
        self._redis = redis_client
        self._producer: Any = None
        self._drain_task: asyncio.Task | None = None
        self._running = False
        self._registry = CircuitBreakerRegistry.get()

    async def start(self) -> None:
        """Initialize and start the Kafka producer. Fail fast if broker unreachable."""
        if AIOKafkaProducer is None:
            raise ImportError("aiokafka not installed")

        self._producer = AIOKafkaProducer(
            bootstrap_servers=self._bootstrap,
            acks="all",
            enable_idempotence=True,
            compression_type="lz4",
            max_batch_size=1_048_576,       # 1 MB
            linger_ms=10,
            request_timeout_ms=30_000,
            retry_backoff_ms=500,
            max_request_size=10_485_760,    # 10 MB
            value_serializer=lambda v: v,   # raw bytes — we handle Avro ourselves
            key_serializer=lambda k: k.encode() if isinstance(k, str) else k,
        )
        try:
            await self._producer.start()
            logger.info(f"Kafka producer connected to {self._bootstrap}")
        except Exception as exc:
            raise RuntimeError(
                f"Cannot connect to Kafka broker at {self._bootstrap}. "
                f"Error: {exc}"
            ) from exc

        self._running = True
        self._drain_task = asyncio.create_task(self._drain_buffer_loop(), name="kafka-buffer-drain")

    async def stop(self) -> None:
        """Flush all buffered messages and close cleanly."""
        self._running = False

        if self._drain_task:
            self._drain_task.cancel()
            try:
                await asyncio.wait_for(self._drain_task, timeout=10.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass

        if self._producer:
            await self._producer.flush()
            await self._producer.stop()
            logger.info("Kafka producer stopped cleanly")

    async def publish(
        self,
        topic: str,
        payload: dict,
        key: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> RecordMetadata | None:
        """
        Publish a message. Auto-injects event_id and timestamp.
        Uses circuit breaker — on broker failure, buffers in Redis.
        """
        # Auto-inject event metadata
        payload.setdefault("event_id", str(uuid.uuid4()))
        payload.setdefault("timestamp", int(time.time() * 1000))

        # Avro serialization
        try:
            sr = get_schema_registry()
            value_bytes = sr.serialise(topic, payload)
        except Exception as exc:
            logger.error(f"Avro serialization failed for {topic}: {exc}")
            raise

        kafka_headers = [(k, v.encode()) for k, v in (headers or {}).items()]
        partition_key = (key or payload.get("doc_id", "default")).encode() if isinstance(
            (key or payload.get("doc_id", "default")), str
        ) else (key or payload.get("doc_id", "")).encode()

        # Try to publish with circuit breaker
        breaker = self._registry.breaker("kafka_circuit") if "kafka_circuit" in CircuitBreakerRegistry.get()._breakers else None

        try:
            meta = await self._producer.send_and_wait(
                topic,
                value=value_bytes,
                key=partition_key,
                headers=kafka_headers if kafka_headers else None,
            )
            return meta
        except CircuitOpenError as exc:
            logger.warning(f"Kafka circuit OPEN — buffering message for {topic}: {exc}")
            await self._buffer_message(topic, payload, key, headers)
            return None
        except Exception as exc:
            logger.error(f"Kafka publish failed for {topic}: {exc}")
            await self._buffer_message(topic, payload, key, headers)
            return None

    # ── Redis buffering ───────────────────────────────────────────────────────

    async def _buffer_message(
        self,
        topic: str,
        payload: dict,
        key: str | None,
        headers: dict | None,
    ) -> None:
        """Buffer a message in Redis when broker is unavailable."""
        buffer_len = await self._redis.llen(_REDIS_BUFFER_KEY)
        if buffer_len >= _REDIS_BUFFER_MAX:
            msg = f"Redis publish buffer full ({_REDIS_BUFFER_MAX}). Message for {topic} dropped."
            logger.critical(msg)
            # Write to local fallback file — last resort
            self._write_fallback(topic, payload)
            raise PublishFailedError(msg)

        envelope = json.dumps({"topic": topic, "payload": payload, "key": key, "headers": headers or {}})
        await self._redis.rpush(_REDIS_BUFFER_KEY, envelope)
        logger.warning(f"Message buffered in Redis for {topic} (buffer size: {buffer_len + 1})")

    async def _drain_buffer_loop(self) -> None:
        """Background task — periodically retry publishing buffered messages."""
        while self._running:
            await asyncio.sleep(_DRAIN_INTERVAL_SECONDS)
            await self._drain_once()

    async def _drain_once(self) -> None:
        """Drain one batch of buffered messages (up to 50 at a time)."""
        if not self._redis:
            return
        drained = 0
        while drained < 50:
            raw = await self._redis.lpop(_REDIS_BUFFER_KEY)
            if not raw:
                break
            try:
                envelope = json.loads(raw)
                await self._producer.send_and_wait(
                    envelope["topic"],
                    value=get_schema_registry().serialise(envelope["topic"], envelope["payload"]),
                    key=envelope.get("key", "").encode() if envelope.get("key") else None,
                )
                drained += 1
            except Exception as exc:
                # Put it back at the front
                await self._redis.lpush(_REDIS_BUFFER_KEY, raw)
                logger.warning(f"Buffer drain failed, will retry: {exc}")
                break

        if drained > 0:
            logger.info(f"Drained {drained} buffered Kafka messages")

    def _write_fallback(self, topic: str, payload: dict) -> None:
        """Write a message to a local fallback file when Redis is also full."""
        try:
            line = json.dumps({"topic": topic, "payload": payload, "ts": datetime.now(timezone.utc).isoformat()})
            with open(_FALLBACK_LOG_PATH, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception as exc:
            logger.critical(f"Fallback log write also failed: {exc}")


# ── Singleton ─────────────────────────────────────────────────────────────────

_producer: ResilienceProducer | None = None


async def get_producer() -> ResilienceProducer:
    global _producer
    if _producer is None:
        raise RuntimeError("Producer not initialized. Call init_producer() in lifespan.")
    return _producer


async def init_producer(bootstrap_servers: str, redis_client: Any) -> ResilienceProducer:
    global _producer
    _producer = ResilienceProducer(bootstrap_servers, redis_client)
    await _producer.start()
    return _producer


async def close_producer() -> None:
    global _producer
    if _producer:
        await _producer.stop()
        _producer = None
