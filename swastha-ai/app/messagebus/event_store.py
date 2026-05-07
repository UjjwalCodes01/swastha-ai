"""
Event Store — PostgreSQL audit log for all Kafka events.

Every message that flows through SwasthaAI is recorded here.
Provides fast lookup by doc_id, topic, and status without touching Kafka.
Full payloads remain in Kafka (7-30 day retention) and MinIO (permanent).
Only payload_summary (first 1KB) is stored here.

Table: kafka_events (range-partitioned by month — see migration 0003)
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

logger = logging.getLogger(__name__)

_PAYLOAD_SUMMARY_MAX_BYTES = 1024  # 1 KB


class EventStore:
    """
    Writes and updates kafka_events records.
    Uses raw SQL (no ORM) to avoid circular imports and support
    the partitioned table structure cleanly.
    """

    def __init__(self, session_factory: Any) -> None:
        self._factory = session_factory

    async def record_received(
        self,
        event_id: str,
        topic: str,
        partition: int,
        offset: int,
        consumer_group: str,
        doc_id: str | None,
        event_type: str,
        payload: dict,
        produced_at: datetime,
    ) -> None:
        """Insert a new kafka_events record when a message is consumed."""
        summary = self._make_summary(payload)
        consumed_at = datetime.now(timezone.utc)

        async with self._factory() as session:
            await session.execute(
                _INSERT_EVENT_SQL,
                {
                    "event_id": event_id,
                    "topic": topic,
                    "partition": partition,
                    "offset": offset,
                    "consumer_group": consumer_group,
                    "doc_id": doc_id,
                    "event_type": event_type,
                    "payload_summary": json.dumps(summary),
                    "processing_status": "received",
                    "produced_at": produced_at,
                    "consumed_at": consumed_at,
                },
            )
            await session.commit()

    async def update_status(
        self,
        event_id: str,
        produced_at: datetime,
        status: str,
        duration_ms: int | None = None,
        error_detail: str | None = None,
        retry_count: int | None = None,
    ) -> None:
        """Update the processing_status (and optionally duration/error)."""
        completed_at = datetime.now(timezone.utc) if status in ("success", "failed", "dlq", "dismissed") else None

        async with self._factory() as session:
            await session.execute(
                _UPDATE_STATUS_SQL,
                {
                    "event_id": event_id,
                    "produced_at": produced_at,
                    "status": status,
                    "duration_ms": duration_ms,
                    "error_detail": error_detail,
                    "retry_count": retry_count,
                    "completed_at": completed_at,
                },
            )
            await session.commit()

    async def query_by_doc_id(self, doc_id: str) -> list[dict]:
        """Return all events for a given doc_id ordered by produced_at."""
        async with self._factory() as session:
            result = await session.execute(
                _QUERY_BY_DOC_SQL, {"doc_id": doc_id}
            )
            rows = result.fetchall()
        return [dict(row._mapping) for row in rows]

    async def query_by_topic_status(
        self,
        topic: str,
        status: str | None = None,
        from_date: datetime | None = None,
        to_date: datetime | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """Query events by topic, optional status filter, and date range."""
        conditions = ["topic = :topic"]
        params: dict[str, Any] = {"topic": topic, "limit": limit}

        if status:
            conditions.append("processing_status = :status")
            params["status"] = status
        if from_date:
            conditions.append("produced_at >= :from_date")
            params["from_date"] = from_date
        if to_date:
            conditions.append("produced_at <= :to_date")
            params["to_date"] = to_date

        where = " AND ".join(conditions)
        sql = f"""
            SELECT event_id, topic, partition, "offset", consumer_group,
                   doc_id, event_type, processing_status, processing_duration_ms,
                   error_detail, retry_count, produced_at, consumed_at, completed_at
            FROM kafka_events
            WHERE {where}
            ORDER BY produced_at DESC
            LIMIT :limit
        """

        async with self._factory() as session:
            result = await session.execute(_raw_text(sql), params)
            rows = result.fetchall()
        return [dict(row._mapping) for row in rows]

    # ── helpers ───────────────────────────────────────────────────────────────

    def _make_summary(self, payload: dict) -> dict:
        """Truncate payload to first 1KB for summary storage."""
        try:
            full = json.dumps(payload, ensure_ascii=False)
            if len(full) <= _PAYLOAD_SUMMARY_MAX_BYTES:
                return payload
            # Truncate to 1KB
            truncated_str = full[:_PAYLOAD_SUMMARY_MAX_BYTES]
            return {"_truncated": True, "_preview": truncated_str}
        except Exception:
            return {"_error": "payload not serializable"}


# ── SQL constants ─────────────────────────────────────────────────────────────

def _raw_text(sql: str) -> Any:
    from sqlalchemy import text
    return text(sql)


_INSERT_EVENT_SQL = _raw_text("""
    INSERT INTO kafka_events
        (event_id, topic, partition, "offset", consumer_group, doc_id,
         event_type, payload_summary, processing_status,
         produced_at, consumed_at)
    VALUES
        (:event_id, :topic, :partition, :offset, :consumer_group, :doc_id,
         :event_type, :payload_summary::jsonb, :processing_status,
         :produced_at, :consumed_at)
    ON CONFLICT (event_id, produced_at) DO NOTHING
""")

_UPDATE_STATUS_SQL = _raw_text("""
    UPDATE kafka_events
    SET
        processing_status       = :status,
        processing_duration_ms  = COALESCE(:duration_ms, processing_duration_ms),
        error_detail            = COALESCE(:error_detail, error_detail),
        retry_count             = COALESCE(:retry_count, retry_count),
        completed_at            = COALESCE(:completed_at, completed_at)
    WHERE
        event_id = :event_id
        AND produced_at = :produced_at
""")

_QUERY_BY_DOC_SQL = _raw_text("""
    SELECT event_id, topic, partition, "offset", consumer_group,
           doc_id, event_type, processing_status, processing_duration_ms,
           error_detail, retry_count, produced_at, consumed_at, completed_at
    FROM kafka_events
    WHERE doc_id = :doc_id
    ORDER BY produced_at ASC
""")
