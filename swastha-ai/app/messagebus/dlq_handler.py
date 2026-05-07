"""
DLQ Handler — FastAPI router + auto-alerting for Dead Letter Queue events.

Endpoints:
  GET  /dlq/events                      — list DLQ events (paginated, filterable)
  GET  /dlq/events/{event_id}           — single DLQ event detail
  POST /dlq/events/{event_id}/reprocess — republish to original topic
  POST /dlq/events/bulk-reprocess       — bulk reprocess with rate limiting
  POST /dlq/events/{event_id}/dismiss   — mark as dismissed with reason
  GET  /dlq/summary                     — aggregate stats (Prometheus-scrapeable)

Auto-alerting: runs as a background task every 60 seconds.
  > 10 DLQ events → WARNING notification
  > 50 DLQ events → CRITICAL notification
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dlq", tags=["DLQ"])

# ── Pydantic Models ───────────────────────────────────────────────────────────


class DLQEvent(BaseModel):
    event_id: str
    original_topic: str
    original_partition: int | None
    original_offset: int | None
    original_payload: str
    error_type: str | None
    error_message: str | None
    stack_trace: str | None
    retry_count: int
    consumer_group: str | None
    doc_id: str | None
    status: str
    dismissal_reason: str | None
    failed_at: datetime
    created_at: datetime


class ReprocessRequest(BaseModel):
    pass  # event_id is path param


class BulkReprocessRequest(BaseModel):
    event_ids: list[str] | None = None
    topic: str | None = None
    from_date: datetime | None = None
    to_date: datetime | None = None


class DismissRequest(BaseModel):
    dismissal_reason: str


# ── Dependency helpers (injected by lifespan setup) ───────────────────────────

_db_factory: Any = None
_producer_func: Any = None
_redis: Any = None


def init_dlq_handler(db_factory: Any, producer_func: Any, redis_client: Any) -> None:
    global _db_factory, _producer_func, _redis
    _db_factory = db_factory
    _producer_func = producer_func
    _redis = redis_client


async def _get_db():
    if _db_factory is None:
        raise HTTPException(503, "Database not ready")
    return _db_factory


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.get("/events", summary="List DLQ events")
async def list_dlq_events(
    topic: str | None = Query(None),
    from_date: datetime | None = Query(None),
    to_date: datetime | None = Query(None),
    error_type: str | None = Query(None),
    status: str | None = Query(None, description="pending|reprocessing|resolved|dismissed"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db=Depends(_get_db),
) -> dict:
    from sqlalchemy import text

    conditions = ["1=1"]
    params: dict[str, Any] = {
        "limit": page_size,
        "offset": (page - 1) * page_size,
    }

    if topic:
        conditions.append("original_topic = :topic")
        params["topic"] = topic
    if from_date:
        conditions.append("failed_at >= :from_date")
        params["from_date"] = from_date
    if to_date:
        conditions.append("failed_at <= :to_date")
        params["to_date"] = to_date
    if error_type:
        conditions.append("error_type = :error_type")
        params["error_type"] = error_type
    if status:
        conditions.append("status = :status")
        params["status"] = status

    where = " AND ".join(conditions)

    async with db() as session:
        result = await session.execute(
            text(f"""
                SELECT event_id, original_topic, original_partition, original_offset,
                       original_payload, error_type, error_message, stack_trace,
                       retry_count, consumer_group, doc_id, status, dismissal_reason,
                       failed_at, created_at
                FROM dlq_events
                WHERE {where}
                ORDER BY failed_at DESC
                LIMIT :limit OFFSET :offset
            """),
            params,
        )
        rows = [dict(r._mapping) for r in result.fetchall()]

        count_result = await session.execute(
            text(f"SELECT COUNT(*) FROM dlq_events WHERE {where}"),
            {k: v for k, v in params.items() if k not in ("limit", "offset")},
        )
        total = count_result.scalar()

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "events": rows,
    }


@router.get("/events/{event_id}", summary="Get single DLQ event")
async def get_dlq_event(event_id: str, db=Depends(_get_db)) -> dict:
    from sqlalchemy import text

    async with db() as session:
        result = await session.execute(
            text("SELECT * FROM dlq_events WHERE event_id = :eid"),
            {"eid": event_id},
        )
        row = result.fetchone()

    if not row:
        raise HTTPException(404, f"DLQ event '{event_id}' not found")
    return dict(row._mapping)


@router.post("/events/{event_id}/reprocess", summary="Reprocess single DLQ event")
async def reprocess_event(event_id: str, db=Depends(_get_db)) -> dict:
    from sqlalchemy import text
    from app.messagebus.producer import get_producer

    async with db() as session:
        result = await session.execute(
            text("SELECT * FROM dlq_events WHERE event_id = :eid AND status = 'pending'"),
            {"eid": event_id},
        )
        row = result.fetchone()

    if not row:
        raise HTTPException(404, f"Pending DLQ event '{event_id}' not found")

    event = dict(row._mapping)
    original_topic = event["original_topic"]

    # Republish
    try:
        original_payload = json.loads(event["original_payload"])
        producer = await get_producer()
        await producer.publish(original_topic, original_payload, key=event.get("doc_id"))
    except Exception as exc:
        raise HTTPException(500, f"Failed to republish: {exc}")

    # Reset retry count in Redis
    if _redis and event.get("doc_id"):
        await _redis.delete(f"retry_count:{original_topic}:{event['doc_id']}")

    # Update status
    async with db() as session:
        await session.execute(
            text("UPDATE dlq_events SET status = 'reprocessing' WHERE event_id = :eid"),
            {"eid": event_id},
        )
        await session.commit()

    logger.info(f"DLQ event {event_id} reprocessed to {original_topic} by admin")
    return {"status": "reprocessing", "republished_to": original_topic}


@router.post("/events/bulk-reprocess", summary="Bulk reprocess DLQ events (rate limited)")
async def bulk_reprocess(request: BulkReprocessRequest, db=Depends(_get_db)) -> dict:
    from sqlalchemy import text
    from app.messagebus.producer import get_producer

    if not request.event_ids and not request.topic:
        raise HTTPException(400, "Provide either event_ids or topic filter")

    async with db() as session:
        if request.event_ids:
            result = await session.execute(
                text("SELECT * FROM dlq_events WHERE event_id = ANY(:ids) AND status = 'pending'"),
                {"ids": request.event_ids},
            )
        else:
            conditions = ["status = 'pending'", "original_topic = :topic"]
            params: dict[str, Any] = {"topic": request.topic}
            if request.from_date:
                conditions.append("failed_at >= :from_date")
                params["from_date"] = request.from_date
            if request.to_date:
                conditions.append("failed_at <= :to_date")
                params["to_date"] = request.to_date
            result = await session.execute(
                text(f"SELECT * FROM dlq_events WHERE {' AND '.join(conditions)}"),
                params,
            )
        events = [dict(r._mapping) for r in result.fetchall()]

    if not events:
        return {"reprocessed": 0, "errors": 0}

    producer = await get_producer()
    reprocessed = 0
    errors = 0
    _RATE = 10  # max 10 per second

    for i, event in enumerate(events):
        try:
            payload = json.loads(event["original_payload"])
            await producer.publish(event["original_topic"], payload, key=event.get("doc_id"))
            async with db() as session:
                await session.execute(
                    text("UPDATE dlq_events SET status = 'reprocessing' WHERE event_id = :eid"),
                    {"eid": event["event_id"]},
                )
                await session.commit()
            reprocessed += 1
        except Exception as exc:
            logger.error(f"Bulk reprocess failed for {event['event_id']}: {exc}")
            errors += 1

        # Rate limiting: 10 per second
        if (i + 1) % _RATE == 0:
            await asyncio.sleep(1.0)

    logger.info(f"Bulk reprocess: {reprocessed} reprocessed, {errors} errors")
    return {"reprocessed": reprocessed, "errors": errors}


@router.post("/events/{event_id}/dismiss", summary="Dismiss a DLQ event")
async def dismiss_event(
    event_id: str,
    request: DismissRequest,
    db=Depends(_get_db),
) -> dict:
    from sqlalchemy import text

    async with db() as session:
        result = await session.execute(
            text("UPDATE dlq_events SET status = 'dismissed', dismissal_reason = :reason, "
                 "resolved_at = NOW() WHERE event_id = :eid AND status = 'pending' RETURNING event_id"),
            {"reason": request.dismissal_reason, "eid": event_id},
        )
        row = result.fetchone()
        await session.commit()

    if not row:
        raise HTTPException(404, f"Pending DLQ event '{event_id}' not found")

    logger.info(f"DLQ event {event_id} dismissed: {request.dismissal_reason}")
    return {"status": "dismissed", "event_id": event_id}


@router.get("/summary", summary="DLQ aggregate stats (no auth — Prometheus target)")
async def dlq_summary(db=Depends(_get_db)) -> dict:
    from sqlalchemy import text

    async with db() as session:
        result = await session.execute(
            text("""
                SELECT
                    original_topic,
                    COUNT(*) FILTER (WHERE status = 'pending') AS pending_count,
                    COUNT(*) FILTER (WHERE status = 'reprocessing') AS reprocessing_count,
                    COUNT(*) FILTER (WHERE status = 'dismissed') AS dismissed_count,
                    COUNT(*) FILTER (WHERE status = 'resolved') AS resolved_count,
                    MIN(failed_at) FILTER (WHERE status = 'pending') AS oldest_pending_at,
                    MODE() WITHIN GROUP (ORDER BY error_type) AS most_common_error
                FROM dlq_events
                GROUP BY original_topic
            """),
        )
        rows = [dict(r._mapping) for r in result.fetchall()]

    return {
        "topics": rows,
        "total_pending": sum(r["pending_count"] for r in rows),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


# ── Auto-alerting Background Task ────────────────────────────────────────────


class DLQAlertTask:
    """
    Background task that monitors DLQ depth and publishes notifications.
    """

    _WARNING_THRESHOLD = 10
    _CRITICAL_THRESHOLD = 50
    _CHECK_INTERVAL = 60  # seconds

    def __init__(self, db_factory: Any, producer_func: Any) -> None:
        self._db = db_factory
        self._producer_func = producer_func
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="dlq-alert-task")

    def stop(self) -> None:
        if self._task:
            self._task.cancel()

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(self._CHECK_INTERVAL)
            try:
                await self._check()
            except Exception as exc:
                logger.error(f"DLQ alert check failed: {exc}")

    async def _check(self) -> None:
        from sqlalchemy import text

        async with self._db() as session:
            result = await session.execute(
                text("""
                    SELECT original_topic, COUNT(*) as pending_count
                    FROM dlq_events WHERE status = 'pending'
                    GROUP BY original_topic
                """)
            )
            rows = result.fetchall()

        for row in rows:
            topic = row.original_topic
            count = row.pending_count

            if count >= self._CRITICAL_THRESHOLD:
                await self._alert("CRITICAL", topic, count)
            elif count >= self._WARNING_THRESHOLD:
                await self._alert("WARNING", topic, count)

    async def _alert(self, severity: str, topic: str, count: int) -> None:
        try:
            producer = await self._producer_func()
            await producer.publish(
                "notifications.events",
                {
                    "event_id": str(uuid.uuid4()),
                    "event_version": "1.0",
                    "source_layer": "layer2",
                    "severity": severity,
                    "alert_type": "dlq_depth_high",
                    "title": f"DLQ Alert: {severity}",
                    "message": f"DLQ topic '{topic}' has {count} pending events",
                    "affected_topic": topic,
                    "metric_value": float(count),
                    "metric_threshold": float(
                        self._CRITICAL_THRESHOLD if severity == "CRITICAL" else self._WARNING_THRESHOLD
                    ),
                    "extra": {},
                    "timestamp": int(time.time() * 1000),
                },
            )
            logger.warning(f"DLQ alert [{severity}]: {topic} has {count} pending events")
        except Exception as exc:
            logger.error(f"Failed to send DLQ alert: {exc}")
