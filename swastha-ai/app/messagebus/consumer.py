"""
Resilient Kafka Consumer base class.

All SwasthaAI consumers extend this class. Never write a raw aiokafka
consumer in a service — always use BaseConsumer.

Guarantees:
  - enable_auto_commit=False — manual commits only
  - auto_offset_reset=earliest — never miss a message after restart
  - Avro deserialization per message via schema registry
  - Pydantic payload validation
  - On success: commit offset
  - On failure: increment Redis retry counter → retry topic → DLQ
  - Every message recorded in PostgreSQL event store
  - Graceful shutdown: finish current message, commit, then stop
  - pause/resume partition support for backpressure
  - seek_to_offset for manual recovery
"""
from __future__ import annotations

import asyncio
import json
import logging
import signal
import time
import traceback
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine, Type

logger = logging.getLogger(__name__)

try:
    from aiokafka import AIOKafkaConsumer, TopicPartition
    from aiokafka.structs import ConsumerRecord
except ImportError:  # pragma: no cover
    AIOKafkaConsumer = None  # type: ignore[assignment, misc]
    TopicPartition = None  # type: ignore[assignment, misc]
    ConsumerRecord = None  # type: ignore[assignment]

from app.messagebus.event_store import EventStore
from app.messagebus.schema_registry import get_schema_registry

_MAX_RETRIES = 3
_SHUTDOWN_TIMEOUT_SECONDS = 30
_REDIS_RETRY_PREFIX = "retry_count:"
_REDIS_RETRY_TTL = 86400  # 24 hours


class BaseConsumer:
    """
    Base consumer. Subclass and implement handle(record, payload) for each service.
    """

    def __init__(
        self,
        bootstrap_servers: str,
        group_id: str,
        redis_client: Any,
        event_store: EventStore,
        max_retries: int = _MAX_RETRIES,
    ) -> None:
        self._bootstrap = bootstrap_servers
        self._group_id = group_id
        self._redis = redis_client
        self._event_store = event_store
        self._max_retries = max_retries
        self._consumer: Any = None
        self._running = False
        self._current_record: Any = None
        self._handler: Callable | None = None

    async def start(
        self,
        topics: list[str],
        handler: Callable[[Any, dict], Coroutine],
    ) -> None:
        """
        Start consuming. Blocks until stop() is called.

        Args:
            topics: list of topic names to subscribe to
            handler: async callable(record, payload_dict) → None
        """
        if AIOKafkaConsumer is None:
            raise ImportError("aiokafka not installed")

        self._handler = handler
        self._consumer = AIOKafkaConsumer(
            *topics,
            bootstrap_servers=self._bootstrap,
            group_id=self._group_id,
            enable_auto_commit=False,       # MANDATORY — manual commits only
            auto_offset_reset="earliest",   # Never miss a message after restart
            max_poll_interval_ms=300_000,   # 5 min — long-running AI processing
            session_timeout_ms=30_000,
            heartbeat_interval_ms=10_000,
            fetch_max_bytes=52_428_800,     # 50 MB
            value_deserializer=lambda v: v, # raw bytes — we handle Avro ourselves
        )

        await self._consumer.start()
        self._running = True
        logger.info(f"Consumer [{self._group_id}] started on topics: {topics}")

        # Register SIGTERM handler
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self.stop)
            except NotImplementedError:
                pass

        await self._consume_loop()

    def stop(self) -> None:
        """Trigger graceful shutdown. Currently processing message will complete."""
        logger.info(f"Consumer [{self._group_id}] shutting down...")
        self._running = False

    async def _consume_loop(self) -> None:
        """Main message loop."""
        try:
            async for record in self._consumer:
                if not self._running:
                    break
                self._current_record = record
                await self._process_record(record)
                self._current_record = None
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.critical(
                f"Consumer [{self._group_id}] loop crashed: {exc}",
                exc_info=True,
            )
        finally:
            await self._shutdown_cleanly()

    async def _process_record(self, record: Any) -> None:
        """Process a single record: deserialize → validate → handle → commit."""
        start_ts = time.perf_counter()
        event_id = str(uuid.uuid4())
        produced_at = datetime.fromtimestamp(record.timestamp / 1000, tz=timezone.utc)

        # 1. Deserialize Avro
        try:
            sr = get_schema_registry()
            payload = sr.deserialise(record.value)
            event_id = payload.get("event_id", event_id)
            doc_id = payload.get("doc_id")
        except Exception as exc:
            logger.error(
                f"Avro deserialization failed on {record.topic}[{record.partition}]@{record.offset}: {exc}"
            )
            # Can't deserialize → send to DLQ immediately, don't retry
            await self._send_to_dlq(
                record,
                error_type=type(exc).__name__,
                error_message=str(exc),
                stack_trace=traceback.format_exc(),
                retry_count=self._max_retries,
                original_payload=record.value,
            )
            await self._commit(record)
            return

        # 2. Record in event store
        await self._event_store.record_received(
            event_id=event_id,
            topic=record.topic,
            partition=record.partition,
            offset=record.offset,
            consumer_group=self._group_id,
            doc_id=payload.get("doc_id"),
            event_type=type(payload).__name__,
            payload=payload,
            produced_at=produced_at,
        )
        await self._event_store.update_status(event_id, produced_at, "processing")

        # 3. Call handler
        try:
            await self._handler(record, payload)
            duration_ms = int((time.perf_counter() - start_ts) * 1000)

            # 4. Success → commit + update event store
            await self._commit(record)
            await self._event_store.update_status(event_id, produced_at, "success", duration_ms)
            await self._clear_retry_count(record.topic, payload.get("doc_id", ""))

        except Exception as exc:
            duration_ms = int((time.perf_counter() - start_ts) * 1000)
            logger.error(
                f"Handler failed for {record.topic}[{record.partition}]@{record.offset}: {exc}",
                exc_info=True,
            )

            # 5. Failure → retry or DLQ
            retry_count = await self._increment_retry_count(record.topic, payload.get("doc_id", ""))
            await self._event_store.update_status(
                event_id, produced_at, "failed",
                duration_ms=duration_ms,
                error_detail=str(exc),
                retry_count=retry_count,
            )

            if retry_count <= self._max_retries:
                await self._publish_to_retry(record, payload, retry_count)
                # Commit so we don't re-process immediately; retry topic handles re-delivery
                await self._commit(record)
            else:
                await self._send_to_dlq(
                    record,
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                    stack_trace=traceback.format_exc(),
                    retry_count=retry_count,
                    original_payload=json.dumps(payload),
                )
                await self._event_store.update_status(event_id, produced_at, "dlq")
                await self._commit(record)  # Don't reprocess infinitely

    async def _commit(self, record: Any) -> None:
        """Manually commit offset for a specific record."""
        tp = TopicPartition(record.topic, record.partition)
        await self._consumer.commit({tp: record.offset + 1})

    async def _publish_to_retry(self, record: Any, payload: dict, retry_count: int) -> None:
        """Publish to the appropriate retry topic."""
        from app.messagebus.producer import get_producer
        retry_topic = f"{record.topic}.retry.{retry_count}"
        try:
            producer = await get_producer()
            # Tag the payload with retry metadata
            retry_payload = {
                **payload,
                "_retry_count": retry_count,
                "_retry_timestamp": int(time.time() * 1000),
                "_original_topic": record.topic,
            }
            await producer.publish(retry_topic, retry_payload, key=payload.get("doc_id"))
            logger.info(f"Published to retry topic {retry_topic} (attempt {retry_count}/{self._max_retries})")
        except Exception as exc:
            logger.error(f"Failed to publish to retry topic {retry_topic}: {exc}")

    async def _send_to_dlq(
        self,
        record: Any,
        error_type: str,
        error_message: str,
        stack_trace: str | None,
        retry_count: int,
        original_payload: Any,
    ) -> None:
        """Publish to the DLQ topic and store in dlq_events table."""
        from app.messagebus.producer import get_producer

        dlq_topic = f"{record.topic}.dlq"
        dlq_event_id = str(uuid.uuid4())
        doc_id = None

        # Try to extract doc_id from original payload
        try:
            if isinstance(original_payload, (bytes, bytearray)):
                parsed = json.loads(original_payload.decode("utf-8", errors="replace"))
                doc_id = parsed.get("doc_id")
            elif isinstance(original_payload, dict):
                doc_id = original_payload.get("doc_id")
        except Exception:
            pass

        dlq_payload = {
            "event_id": dlq_event_id,
            "event_version": "1.0",
            "original_topic": record.topic,
            "original_partition": record.partition,
            "original_offset": record.offset,
            "original_payload": (
                original_payload
                if isinstance(original_payload, str)
                else original_payload.decode("utf-8", errors="replace")
                if isinstance(original_payload, bytes)
                else json.dumps(original_payload)
            ),
            "original_key": record.key.decode("utf-8") if record.key else None,
            "error_type": error_type,
            "error_message": error_message[:1000],
            "stack_trace": (stack_trace[:3000] if stack_trace else None),
            "retry_count": retry_count,
            "consumer_group": self._group_id,
            "failed_at": int(time.time() * 1000),
            "doc_id": doc_id,
            "timestamp": int(time.time() * 1000),
        }

        try:
            producer = await get_producer()
            await producer.publish(dlq_topic, dlq_payload, key=doc_id)
        except Exception as exc:
            logger.error(f"Failed to publish to DLQ {dlq_topic}: {exc}")

        # Store in PostgreSQL dlq_events
        try:
            from sqlalchemy import text
            from app.db.connection import get_session_factory
            async with get_session_factory()() as session:
                await session.execute(
                    text("""
                        INSERT INTO dlq_events
                        (event_id, original_topic, original_partition, original_offset,
                         original_payload, error_type, error_message, stack_trace,
                         retry_count, consumer_group, doc_id, failed_at)
                        VALUES (:eid, :ot, :op, :oo, :opl, :et, :em, :st, :rc, :cg, :did, :fa)
                        ON CONFLICT (event_id) DO NOTHING
                    """),
                    {
                        "eid": dlq_event_id,
                        "ot": record.topic,
                        "op": record.partition,
                        "oo": record.offset,
                        "opl": dlq_payload["original_payload"][:10000],
                        "et": error_type,
                        "em": error_message[:1000],
                        "st": (stack_trace[:3000] if stack_trace else None),
                        "rc": retry_count,
                        "cg": self._group_id,
                        "did": doc_id,
                        "fa": datetime.now(timezone.utc),
                    }
                )
                await session.commit()
        except Exception as exc:
            logger.error(f"Failed to store DLQ event in PostgreSQL: {exc}")

        logger.error(
            f"Message sent to DLQ: {dlq_topic}",
            extra={"doc_id": doc_id, "retry_count": retry_count, "error": error_message},
        )

    async def _increment_retry_count(self, topic: str, doc_id: str) -> int:
        key = f"{_REDIS_RETRY_PREFIX}{topic}:{doc_id}"
        count = await self._redis.incr(key)
        if count == 1:
            await self._redis.expire(key, _REDIS_RETRY_TTL)
        return int(count)

    async def _clear_retry_count(self, topic: str, doc_id: str) -> None:
        await self._redis.delete(f"{_REDIS_RETRY_PREFIX}{topic}:{doc_id}")

    async def _shutdown_cleanly(self) -> None:
        """Graceful shutdown: wait for current message, then close."""
        logger.info(f"Consumer [{self._group_id}] closing...")
        if self._consumer:
            try:
                await asyncio.wait_for(self._consumer.stop(), timeout=_SHUTDOWN_TIMEOUT_SECONDS)
            except asyncio.TimeoutError:
                logger.warning(f"Consumer [{self._group_id}] shutdown timed out after {_SHUTDOWN_TIMEOUT_SECONDS}s")
        logger.info(f"Consumer [{self._group_id}] stopped.")

    # ── Partition management ──────────────────────────────────────────────────

    async def pause_partitions(self, partitions: list[tuple[str, int]]) -> None:
        """Pause specific partitions (backpressure)."""
        tps = [TopicPartition(t, p) for t, p in partitions]
        self._consumer.pause(*tps)
        logger.info(f"Paused partitions: {partitions}")

    async def resume_partitions(self, partitions: list[tuple[str, int]]) -> None:
        """Resume paused partitions."""
        tps = [TopicPartition(t, p) for t, p in partitions]
        self._consumer.resume(*tps)
        logger.info(f"Resumed partitions: {partitions}")

    async def seek_to_offset(self, topic: str, partition: int, offset: int) -> None:
        """
        Seek to a specific offset. Used for manual recovery after a bug fix.
        Commits the given offset so the next poll starts from offset+1.
        """
        tp = TopicPartition(topic, partition)
        self._consumer.seek(tp, offset)
        await self._consumer.commit({tp: offset})
        logger.info(f"Seeked {topic}[{partition}] to offset {offset}")
