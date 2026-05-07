"""
Consumer Lag Monitor — tracks consumer group lag across all topics.

Runs as a background asyncio task every 30 seconds.
Stores results in Redis (TTL 60s).
Exposes Prometheus metrics.
Publishes alerts to notifications.events when lag thresholds are exceeded.

Prometheus metrics exposed:
  kafka_consumer_lag{group, topic, partition}
  kafka_consumer_last_heartbeat_seconds{group}
  kafka_messages_processed_total{group, topic}
  kafka_messages_failed_total{group, topic}
  kafka_dlq_depth{topic}
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

try:
    from kafka import KafkaAdminClient, KafkaConsumer
    from kafka.admin import NewTopic
    from kafka.structs import OffsetAndMetadata, TopicPartition
except ImportError:  # pragma: no cover
    KafkaAdminClient = None  # type: ignore[assignment, misc]
    KafkaConsumer = None  # type: ignore[assignment, misc]
    TopicPartition = None  # type: ignore[assignment, misc]

try:
    from prometheus_client import Counter, Gauge, Histogram
    _METRICS_AVAILABLE = True
except ImportError:  # pragma: no cover
    _METRICS_AVAILABLE = False

# Prometheus metrics (initialized only if prometheus_client is available)
if _METRICS_AVAILABLE:
    _LAG_GAUGE = Gauge(
        "kafka_consumer_lag",
        "Consumer lag per partition",
        ["group", "topic", "partition"],
    )
    _HEARTBEAT_GAUGE = Gauge(
        "kafka_consumer_last_heartbeat_seconds",
        "Unix timestamp of last heartbeat per consumer group",
        ["group"],
    )
    _PROCESSED_COUNTER = Counter(
        "kafka_messages_processed_total",
        "Total messages processed",
        ["group", "topic"],
    )
    _FAILED_COUNTER = Counter(
        "kafka_messages_failed_total",
        "Total messages failed",
        ["group", "topic"],
    )
    _DLQ_GAUGE = Gauge(
        "kafka_dlq_depth",
        "Number of pending DLQ events per topic",
        ["topic"],
    )
else:
    _LAG_GAUGE = _HEARTBEAT_GAUGE = _PROCESSED_COUNTER = _FAILED_COUNTER = _DLQ_GAUGE = None  # type: ignore


_REDIS_LAG_PREFIX = "consumer_lag:"
_REDIS_LAG_TTL = 60
_REDIS_HEARTBEAT_PREFIX = "consumer_heartbeat:"
_REDIS_HEARTBEAT_TTL = 600
_CHECK_INTERVAL_SECONDS = 30
_LAG_WARN_THRESHOLD = 1000
_LAG_CRIT_THRESHOLD = 10000
_HEARTBEAT_DEAD_SECONDS = 300  # 5 minutes


class ConsumerLagMonitor:
    """
    Background task that polls consumer group offsets and stores lag metrics.
    """

    def __init__(
        self,
        bootstrap_servers: str,
        redis_client: Any,
        db_factory: Any,
        producer_func: Any,
        consumer_groups: list[str],
    ) -> None:
        self._bootstrap = bootstrap_servers
        self._redis = redis_client
        self._db = db_factory
        self._producer_func = producer_func
        self._groups = consumer_groups
        self._task: asyncio.Task | None = None
        self._running = False

    def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._run_loop(), name="consumer-lag-monitor")
        logger.info(f"ConsumerLagMonitor started for {len(self._groups)} consumer groups")

    def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()

    async def get_lag_for_group(self, group_id: str) -> dict[str, Any]:
        """Return cached lag data from Redis for a given consumer group."""
        raw = await self._redis.get(f"{_REDIS_LAG_PREFIX}{group_id}")
        if raw:
            return json.loads(raw)
        return {"group_id": group_id, "partitions": [], "last_checked": None}

    async def get_all_lags(self) -> dict[str, Any]:
        """Return all consumer group lags."""
        result = {}
        for group in self._groups:
            result[group] = await self.get_lag_for_group(group)
        return result

    # ── internals ─────────────────────────────────────────────────────────────

    async def _run_loop(self) -> None:
        while self._running:
            try:
                await self._check_all_groups()
            except Exception as exc:
                logger.error(f"Lag monitor check failed: {exc}", exc_info=True)
            await asyncio.sleep(_CHECK_INTERVAL_SECONDS)

    async def _check_all_groups(self) -> None:
        """Run in thread pool (kafka-python is sync)."""
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._check_sync)

    def _check_sync(self) -> None:
        """Sync implementation of lag calculation (runs in thread pool)."""
        if KafkaAdminClient is None:
            return

        try:
            admin = KafkaAdminClient(
                bootstrap_servers=self._bootstrap,
                client_id="swastha-lag-monitor",
                request_timeout_ms=10_000,
            )
        except Exception as exc:
            logger.error(f"Lag monitor cannot connect to Kafka: {exc}")
            return

        try:
            for group_id in self._groups:
                self._check_group(admin, group_id)
        finally:
            admin.close()

    def _check_group(self, admin: Any, group_id: str) -> None:
        """Calculate lag for a single consumer group."""
        try:
            offsets = admin.list_consumer_group_offsets(group_id)
        except Exception as exc:
            logger.debug(f"Cannot fetch offsets for {group_id}: {exc}")
            return

        if not offsets:
            return

        # Get end offsets for all topic-partitions this group is subscribed to
        consumer = KafkaConsumer(bootstrap_servers=self._bootstrap)
        try:
            end_offsets = consumer.end_offsets(list(offsets.keys()))
        except Exception as exc:
            logger.debug(f"Cannot fetch end offsets: {exc}")
            return
        finally:
            consumer.close()

        partitions_data = []
        total_lag = 0

        for tp, committed_offset_meta in offsets.items():
            committed = committed_offset_meta.offset if committed_offset_meta else 0
            end = end_offsets.get(tp, committed)
            lag = max(0, end - committed)
            total_lag += lag

            partitions_data.append({
                "topic": tp.topic,
                "partition": tp.partition,
                "committed_offset": committed,
                "end_offset": end,
                "lag": lag,
            })

            if _METRICS_AVAILABLE and _LAG_GAUGE:
                _LAG_GAUGE.labels(
                    group=group_id,
                    topic=tp.topic,
                    partition=str(tp.partition),
                ).set(lag)

        # Store in Redis
        lag_data = {
            "group_id": group_id,
            "total_lag": total_lag,
            "partitions": partitions_data,
            "last_checked": time.time(),
        }

        # Use asyncio.run_coroutine_threadsafe for Redis calls from sync thread
        import asyncio as _asyncio
        loop = _asyncio.get_event_loop()
        future = asyncio.run_coroutine_threadsafe(
            self._store_lag(group_id, lag_data), loop
        )
        try:
            future.result(timeout=5)
        except Exception:
            pass

        # Update heartbeat
        heartbeat_future = asyncio.run_coroutine_threadsafe(
            self._update_heartbeat(group_id), loop
        )
        try:
            heartbeat_future.result(timeout=5)
        except Exception:
            pass

        # Check thresholds
        if total_lag >= _LAG_CRIT_THRESHOLD:
            alert_future = asyncio.run_coroutine_threadsafe(
                self._alert(group_id, total_lag, "CRITICAL"), loop
            )
            try:
                alert_future.result(timeout=5)
            except Exception:
                pass
        elif total_lag >= _LAG_WARN_THRESHOLD:
            alert_future = asyncio.run_coroutine_threadsafe(
                self._alert(group_id, total_lag, "WARNING"), loop
            )
            try:
                alert_future.result(timeout=5)
            except Exception:
                pass

        # Update PostgreSQL consumer_group_state
        loop.run_until_complete(self._update_db(group_id, partitions_data))

    async def _store_lag(self, group_id: str, data: dict) -> None:
        await self._redis.set(
            f"{_REDIS_LAG_PREFIX}{group_id}",
            json.dumps(data),
            ex=_REDIS_LAG_TTL,
        )

    async def _update_heartbeat(self, group_id: str) -> None:
        await self._redis.set(
            f"{_REDIS_HEARTBEAT_PREFIX}{group_id}",
            str(time.time()),
            ex=_REDIS_HEARTBEAT_TTL,
        )
        if _METRICS_AVAILABLE and _HEARTBEAT_GAUGE:
            _HEARTBEAT_GAUGE.labels(group=group_id).set(time.time())

    async def _update_db(self, group_id: str, partitions: list[dict]) -> None:
        """Upsert consumer_group_state in PostgreSQL."""
        from sqlalchemy import text

        try:
            async with self._db() as session:
                for p in partitions:
                    await session.execute(
                        text("""
                            INSERT INTO consumer_group_state
                                (group_id, topic, partition, committed_offset, lag, last_heartbeat)
                            VALUES (:gid, :topic, :part, :committed, :lag, NOW())
                            ON CONFLICT (group_id, topic, partition)
                            DO UPDATE SET
                                committed_offset = EXCLUDED.committed_offset,
                                lag = EXCLUDED.lag,
                                last_heartbeat = NOW(),
                                updated_at = NOW()
                        """),
                        {
                            "gid": group_id,
                            "topic": p["topic"],
                            "part": p["partition"],
                            "committed": p["committed_offset"],
                            "lag": p["lag"],
                        },
                    )
                await session.commit()
        except Exception as exc:
            logger.error(f"Failed to update consumer_group_state: {exc}")

    async def _alert(self, group_id: str, lag: int, severity: str) -> None:
        """Publish lag alert to notifications.events."""
        try:
            producer = await self._producer_func()
            await producer.publish(
                "notifications.events",
                {
                    "event_id": str(uuid.uuid4()),
                    "event_version": "1.0",
                    "source_layer": "layer2",
                    "severity": severity,
                    "alert_type": "consumer_lag_high",
                    "title": f"Consumer Lag [{severity}]: {group_id}",
                    "message": f"Consumer group '{group_id}' has lag={lag}",
                    "affected_group": group_id,
                    "metric_value": float(lag),
                    "metric_threshold": float(
                        _LAG_CRIT_THRESHOLD if severity == "CRITICAL" else _LAG_WARN_THRESHOLD
                    ),
                    "extra": {},
                    "timestamp": int(time.time() * 1000),
                },
            )
        except Exception as exc:
            logger.error(f"Failed to publish lag alert: {exc}")

    def record_processed(self, group: str, topic: str) -> None:
        """Call from consumer after successful processing."""
        if _METRICS_AVAILABLE and _PROCESSED_COUNTER:
            _PROCESSED_COUNTER.labels(group=group, topic=topic).inc()

    def record_failed(self, group: str, topic: str) -> None:
        """Call from consumer after processing failure."""
        if _METRICS_AVAILABLE and _FAILED_COUNTER:
            _FAILED_COUNTER.labels(group=group, topic=topic).inc()
