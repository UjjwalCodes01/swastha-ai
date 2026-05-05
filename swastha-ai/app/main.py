"""
SwasthaAI Layer 0 — Portal Ingestion Layer

FastAPI application entry point.

Startup sequence:
1. Configure structured JSON logging
2. Initialise database engine
3. Initialise MinIO client (create buckets, set policies)
4. Initialise Kafka producer (connect with local queue fallback)
5. Initialise Redis client
6. Initialise audit logger background worker
7. Start portal adapter background tasks (SUGAM, MD Online)
8. Register middleware (request ID, security headers)
9. Mount the ingestion router

Shutdown sequence (SIGTERM):
1. Stop portal adapter tasks
2. Stop audit logger (drain queue)
3. Flush Kafka producer (send pending messages)
4. Close database connection pool
5. Close Redis connection
6. Close MinIO client
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.adapters.md_online_adapter import MDOnlineAdapter
from app.adapters.sugam_adapter import SUGAMAdapter
from app.audit.logger import close_audit_logger, init_audit_logger
from app.config import get_settings
from app.db.connection import close_db, get_session_factory, init_db
from app.dependencies import close_redis, init_redis
from app.ingestion.router import router as ingestion_router
from app.middleware.request_id import RequestIDMiddleware
from app.middleware.security_headers import SecurityHeadersMiddleware
from app.queue.kafka_producer import close_kafka, init_kafka
from app.storage.minio_client import close_minio, init_minio

# ── Logging Configuration ─────────────────────────────────────────────────────


def configure_logging(log_level: str) -> None:
    """
    Configure structured JSON logging.

    Every log line includes: timestamp, level, logger name, message,
    request_id (from contextvar), and any extra fields passed to the logger.
    """
    import json
    from logging import LogRecord

    class SwasthaAIJSONFormatter(logging.Formatter):
        """JSON log formatter that injects request_id from contextvar."""

        def format(self, record: LogRecord) -> str:
            from app.middleware.request_id import get_request_id

            log_obj: dict[str, Any] = {
                "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S.%f"),
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
                "request_id": get_request_id() or None,
            }

            # Merge any extra fields passed via logger.info(..., extra={...})
            for key, value in record.__dict__.items():
                if key not in (
                    "name", "msg", "args", "levelname", "levelno", "pathname",
                    "filename", "module", "exc_info", "exc_text", "stack_info",
                    "lineno", "funcName", "created", "msecs", "relativeCreated",
                    "thread", "threadName", "processName", "process", "message",
                    "taskName",
                ):
                    log_obj[key] = value

            if record.exc_info:
                log_obj["exception"] = self.formatException(record.exc_info)

            return json.dumps(log_obj, default=str)

    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, log_level, logging.INFO))

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(SwasthaAIJSONFormatter())

    root_logger.handlers.clear()
    root_logger.addHandler(handler)

    # Silence noisy third-party loggers
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("aiokafka").setLevel(logging.WARNING)
    logging.getLogger("botocore").setLevel(logging.WARNING)


logger = logging.getLogger(__name__)

# ── Lifespan ──────────────────────────────────────────────────────────────────

# Background adapter tasks (kept so we can cancel them on shutdown)
_adapter_tasks: list[asyncio.Task] = []


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI lifespan context manager.

    All startup logic runs before `yield`.
    All shutdown logic runs after `yield`.
    """
    settings = get_settings()
    configure_logging(settings.log_level)

    logger.info(
        "SwasthaAI Layer 0 starting",
        extra={"environment": settings.environment, "version": "1.0.0"},
    )

    # ── Startup ────────────────────────────────────────────────────────────────

    # 1. Database
    await init_db()
    logger.info("PostgreSQL engine ready")

    # 2. MinIO
    await init_minio()
    logger.info("MinIO client ready")

    # 3. Kafka
    await init_kafka()
    logger.info("Kafka producer ready")

    # 4. Redis
    try:
        await init_redis()
        logger.info("Redis client ready")
    except Exception as exc:
        logger.error(
            "Redis connection failed on startup — auth caching disabled",
            extra={"error": str(exc)},
        )

    # 5. Audit logger
    session_factory = get_session_factory()
    await init_audit_logger(session_factory)
    logger.info("Audit logger started")

    # 6. Portal adapters (background tasks)
    sugam = SUGAMAdapter()
    md_online = MDOnlineAdapter()

    _adapter_tasks.append(asyncio.create_task(sugam.run(), name="sugam-adapter"))
    _adapter_tasks.append(asyncio.create_task(md_online.run(), name="md-online-adapter"))
    logger.info("Portal adapters started")

    logger.info("SwasthaAI Layer 0 startup complete — ready to serve requests")

    yield  # Application is now running

    # ── Shutdown ───────────────────────────────────────────────────────────────

    logger.info("SwasthaAI Layer 0 shutting down")

    # Stop portal adapters
    for task in _adapter_tasks:
        task.cancel()
        try:
            await asyncio.wait_for(task, timeout=5.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
    _adapter_tasks.clear()

    # Drain audit queue and close
    await close_audit_logger()

    # Flush Kafka and close
    await close_kafka()

    # Close MinIO
    await close_minio()

    # Close Redis
    await close_redis()

    # Close database
    await close_db()

    logger.info("SwasthaAI Layer 0 shutdown complete")


# ── FastAPI Application ────────────────────────────────────────────────────────

settings = get_settings()

app = FastAPI(
    title="SwasthaAI — Portal Ingestion Layer",
    description="""
## SwasthaAI Layer 0 — CDSCO Document Ingestion API

Production-grade regulatory document ingestion for CDSCO SwasthaAI.

### Features
- **Multi-portal ingestion**: SUGAM, MD Online, manual upload, SAE feed
- **Document validation**: MIME type (magic bytes), zip bomb detection, XXE injection, PDF encryption
- **Deduplication**: SHA-256 checksum-based duplicate detection
- **Virus scanning**: ClamAV daemon or webhook integration
- **Tamper-evident audit**: Chain-hashed audit log (append-only)
- **Object storage**: MinIO with 10-year retention, versioning, SSE-S3
- **Event streaming**: Apache Kafka with at-least-once delivery
- **Rate limiting**: Sliding window per IP + per user, with IP blocking

### Authentication
All endpoints (except `/health`) require a valid Keycloak JWT Bearer token
or an X-API-Key header for machine-to-machine calls.

### Roles
- `admin`: Full access
- `reviewer`: Read-only access to assigned submissions
- `portal_operator`: Upload and view own submissions
- `api_client`: Upload via API, rate-limited aggressively
    """,
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan,
)

# ── Middleware ─────────────────────────────────────────────────────────────────
# Order matters: middleware is applied in reverse registration order.
# First registered = outermost = first to see request, last to see response.

app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RequestIDMiddleware)

# CORS: restrict in production, permissive in development
if settings.environment == "production":
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.keycloak_url],
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Authorization", "X-API-Key", "X-Request-ID", "Content-Type"],
    )
else:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# ── Routers ────────────────────────────────────────────────────────────────────

app.include_router(ingestion_router)


# ── Global Exception Handlers ─────────────────────────────────────────────────


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """
    Catch-all handler for unhandled exceptions.

    Returns a generic 500 response without exposing internal details.
    The full exception is logged with request context.
    """
    from app.middleware.request_id import get_request_id

    request_id = get_request_id()
    logger.error(
        "Unhandled exception",
        extra={
            "request_id": request_id,
            "path": str(request.url.path),
            "method": request.method,
            "error": str(exc),
        },
        exc_info=True,
    )
    return JSONResponse(
        status_code=500,
        content={
            "error": "Internal server error",
            "request_id": request_id,
        },
    )


@app.get("/", include_in_schema=False)
async def root() -> dict:
    """Redirect hint — the actual API is at /api/v1/."""
    return {
        "service": "SwasthaAI Portal Ingestion Layer",
        "version": "1.0.0",
        "docs": "/docs",
        "health": "/api/v1/ingest/health",
    }
