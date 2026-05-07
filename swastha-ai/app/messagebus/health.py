"""
Message Bus Admin API + Health Endpoints — port 8003.

Routes:
  GET  /health                          — quick health check
  GET  /health/detailed                 — full system detail (admin)
  GET  /metrics                         — Prometheus metrics
  GET  /admin/topics                    — list all topics with stats
  GET  /admin/consumer-groups           — list all consumer groups with lag
  POST /admin/topics/{topic}/reset-offset
  POST /admin/consumer-groups/{group_id}/pause
  POST /admin/consumer-groups/{group_id}/resume
  GET  /admin/events                    — event history by doc_id

Plus: DLQ endpoints from dlq_handler.py router
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)

# ── Lifespan ──────────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    import os
    from app.config import get_settings
    from app.db.connection import init_db, get_session_factory
    from app.dependencies import init_redis, get_redis
    from app.messagebus.admin import AdminClient
    from app.messagebus.circuit_breaker import CircuitBreakerRegistry
    from app.messagebus.consumer_monitor import ConsumerLagMonitor
    from app.messagebus.dlq_handler import init_dlq_handler, DLQAlertTask
    from app.messagebus.producer import init_producer, get_producer
    from app.messagebus.schema_registry import init_schema_registry

    settings = get_settings()

    await init_db()
    await init_redis()
    redis = await get_redis()

    CircuitBreakerRegistry.get().initialize(redis)

    # Schema registry
    schema_paths = {
        "raw.documents.ingested":          "kafka/schemas/raw_document_ingested.avsc",
        "documents.preprocessed":         "kafka/schemas/document_preprocessed.avsc",
        "documents.chunks.ready":         "kafka/schemas/document_chunks_ready.avsc",
        "documents.anonymised":            "kafka/schemas/document_anonymised.avsc",
        "documents.summarised":           "kafka/schemas/document_summarised.avsc",
        "documents.classified":           "kafka/schemas/document_classified.avsc",
        "documents.comparison.requested": "kafka/schemas/document_comparison_requested.avsc",
        "reports.generated":              "kafka/schemas/report_generated.avsc",
        "notifications.events":           "kafka/schemas/notification_event.avsc",
        **{f"{t}.dlq": "kafka/schemas/dlq_event.avsc" for t in [
            "raw.documents.ingested", "documents.preprocessed",
            "documents.anonymised", "documents.summarised",
            "documents.classified", "reports.generated"
        ]},
    }
    schema_registry_url = os.getenv("SCHEMA_REGISTRY_URL", "http://localhost:8081")
    init_schema_registry(schema_registry_url, schema_paths)

    bootstrap = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
    await init_producer(bootstrap, redis)

    session_factory = get_session_factory()
    init_dlq_handler(session_factory, get_producer, redis)

    # Consumer lag monitor
    _groups = ["rxflow-preprocessor", "rxflow-ai-core", "rxflow-compliance", "rxflow-output"]
    monitor = ConsumerLagMonitor(
        bootstrap_servers=bootstrap,
        redis_client=redis,
        db_factory=session_factory,
        producer_func=get_producer,
        consumer_groups=_groups,
    )
    monitor.start()
    app.state.monitor = monitor

    # DLQ alert background task
    dlq_task = DLQAlertTask(session_factory, get_producer)
    dlq_task.start()
    app.state.dlq_task = dlq_task

    logger.info("Message Bus API started")

    yield

    # Shutdown
    monitor.stop()
    dlq_task.stop()
    from app.messagebus.producer import close_producer
    await close_producer()
    from app.db.connection import close_db
    await close_db()
    from app.dependencies import close_redis
    await close_redis()

    logger.info("Message Bus API shut down")


# ── FastAPI Application ────────────────────────────────────────────────────────

app = FastAPI(
    title="SwasthaAI — Message Bus Admin API",
    description="Kafka administration, health checks, DLQ management",
    version="1.0.0",
    lifespan=lifespan,
)

# Mount DLQ router
from app.messagebus.dlq_handler import router as dlq_router
app.include_router(dlq_router)


# ── Health Endpoints ──────────────────────────────────────────────────────────


@app.get("/health", summary="Quick health check")
async def health() -> dict:
    import os
    import requests as req

    checks: dict[str, Any] = {}

    # Kafka broker
    try:
        from kafka import KafkaAdminClient
        admin = KafkaAdminClient(
            bootstrap_servers=os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"),
            client_id="health-check",
            request_timeout_ms=5_000,
        )
        topics = admin.list_topics()
        admin.close()
        checks["kafka"] = {"status": "ok", "topic_count": len(topics)}
    except Exception as exc:
        checks["kafka"] = {"status": "error", "error": str(exc)}

    # Schema Registry
    try:
        schema_url = os.getenv("SCHEMA_REGISTRY_URL", "http://localhost:8081")
        r = req.get(f"{schema_url}/subjects", timeout=3)
        checks["schema_registry"] = {"status": "ok" if r.status_code == 200 else "error"}
    except Exception as exc:
        checks["schema_registry"] = {"status": "error", "error": str(exc)}

    overall = "healthy" if all(c["status"] == "ok" for c in checks.values()) else "degraded"
    return {"status": overall, "checks": checks, "timestamp": datetime.now(timezone.utc).isoformat()}


@app.get("/health/detailed", summary="Detailed health (admin)")
async def health_detailed() -> dict:
    basic = await health()

    # Consumer group lags
    monitor = getattr(app.state, "monitor", None)
    lags = {}
    if monitor:
        lags = await monitor.get_all_lags()

    # Circuit breaker states
    from app.messagebus.circuit_breaker import CircuitBreakerRegistry
    cb_states = await CircuitBreakerRegistry.get().get_all_states()

    return {
        **basic,
        "consumer_lags": lags,
        "circuit_breakers": cb_states,
    }


@app.get("/metrics", response_class=PlainTextResponse, summary="Prometheus metrics")
async def metrics() -> str:
    try:
        from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
        return generate_latest().decode("utf-8")
    except ImportError:
        return "# prometheus_client not installed\n"


# ── Admin Endpoints ───────────────────────────────────────────────────────────


@app.get("/admin/topics", summary="List all Kafka topics with stats")
async def list_topics() -> dict:
    import os
    from app.messagebus.admin import AdminClient
    admin = AdminClient(os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"))
    from kafka.config.topic_configs import ALL_TOPICS
    return {
        "topics": [
            {"name": t.name, "partitions": t.partitions, "replication_factor": t.replication_factor}
            for t in ALL_TOPICS
        ]
    }


@app.get("/admin/consumer-groups", summary="List consumer groups with lag")
async def list_consumer_groups() -> dict:
    monitor = getattr(app.state, "monitor", None)
    if not monitor:
        raise HTTPException(503, "Monitor not initialized")
    return await monitor.get_all_lags()


class ResetOffsetRequest(BaseModel):
    group_id: str
    offset_strategy: str  # earliest | latest | specific_offset | specific_timestamp
    partition: int = 0
    specific_offset: int | None = None
    specific_timestamp_ms: int | None = None


@app.post("/admin/topics/{topic}/reset-offset", summary="Reset consumer group offset")
async def reset_offset(topic: str, request: ResetOffsetRequest) -> dict:
    import os
    from app.messagebus.admin import AdminClient
    admin = AdminClient(os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"))
    new_offset = admin.reset_consumer_offset(
        topic=topic,
        group_id=request.group_id,
        partition=request.partition,
        offset_strategy=request.offset_strategy,
        specific_offset=request.specific_offset,
        specific_timestamp_ms=request.specific_timestamp_ms,
    )
    return {"topic": topic, "group_id": request.group_id, "new_offset": new_offset}


@app.post("/admin/consumer-groups/{group_id}/pause", summary="Pause a consumer group")
async def pause_group(group_id: str) -> dict:
    # This signals via Redis that consumers in this group should pause
    from app.dependencies import get_redis
    redis = await get_redis()
    await redis.set(f"consumer_pause:{group_id}", "1", ex=3600)
    return {"group_id": group_id, "status": "pause_signal_sent"}


@app.post("/admin/consumer-groups/{group_id}/resume", summary="Resume a consumer group")
async def resume_group(group_id: str) -> dict:
    from app.dependencies import get_redis
    redis = await get_redis()
    await redis.delete(f"consumer_pause:{group_id}")
    return {"group_id": group_id, "status": "resume_signal_sent"}


@app.get("/admin/events", summary="Query event history for a document")
async def get_events(
    doc_id: str | None = Query(None),
    topic: str | None = Query(None),
    status: str | None = Query(None),
    from_date: datetime | None = Query(None),
    to_date: datetime | None = Query(None),
) -> dict:
    from app.db.connection import get_session_factory
    from app.messagebus.event_store import EventStore

    store = EventStore(get_session_factory())
    if doc_id:
        events = await store.query_by_doc_id(doc_id)
    elif topic:
        events = await store.query_by_topic_status(topic, status, from_date, to_date)
    else:
        raise HTTPException(400, "Provide doc_id or topic filter")

    return {"events": events, "count": len(events)}
