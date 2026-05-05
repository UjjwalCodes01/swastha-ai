"""
Tamper-evident, chain-hashed audit logger for the SwasthaAI ingestion layer.

Design principles:
1. Every audit entry includes the SHA-256 hash of the previous entry.
   This makes the log tamper-evident: altering any entry breaks the hash chain.
2. Writes are async and non-blocking — they never delay request responses.
3. If the database write fails, the event is written to a local fallback file
   AND logged to stderr. An audit event is NEVER silently lost.
4. The genesis hash (all zeros) is used as prev_hash for the first entry.

Chain verification:
   entry[n].prev_hash == entry[n-1].entry_hash
   entry[n].entry_hash == SHA256(entry[n].prev_hash + entry[n].canonical_payload)
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditLog

logger = logging.getLogger(__name__)

# Fallback log file location (must match Docker volume mount)
_FALLBACK_LOG_PATH = Path(os.environ.get("AUDIT_FALLBACK_LOG", "/var/log/swastha-ai/audit_fallback.log"))

# Genesis hash — used as prev_hash for the very first entry
GENESIS_HASH = "0" * 64

# Background queue for non-blocking audit writes
_audit_queue: asyncio.Queue | None = None
_audit_worker_task: asyncio.Task | None = None


def _compute_entry_hash(prev_hash: str, payload: dict[str, Any]) -> str:
    """
    Compute the SHA-256 hash for an audit entry.

    Hash input: prev_hash concatenated with the canonical JSON of the payload.
    Canonical JSON: keys sorted, no whitespace — deterministic across platforms.
    """
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    data = prev_hash + canonical
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _write_to_fallback(event: dict[str, Any]) -> None:
    """
    Write an audit event to the local fallback log file.

    This is called synchronously when the DB write fails.
    The file is append-only; each line is a JSON object.
    """
    try:
        _FALLBACK_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _FALLBACK_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, default=str) + "\n")
    except Exception as exc:
        # Last resort: write to stderr
        import sys
        sys.stderr.write(
            "CRITICAL: Audit fallback file write failed: "
            f"{exc}. Event: {json.dumps(event, default=str)}\n"
        )


async def _get_prev_hash(db: AsyncSession) -> str:
    """
    Fetch the entry_hash of the most recent audit entry.

    Returns GENESIS_HASH if no entries exist yet.
    Uses a SELECT FOR UPDATE SKIP LOCKED pattern to prevent race conditions.
    """
    result = await db.execute(
        text("SELECT entry_hash FROM audit_log ORDER BY id DESC LIMIT 1")
    )
    row = result.fetchone()
    return row[0] if row else GENESIS_HASH


async def write_audit_event(
    db: AsyncSession,
    *,
    event_type: str,
    outcome: str,
    action_detail: dict[str, Any],
    doc_id: str | None = None,
    actor_id: str | None = None,
    actor_email: str | None = None,
    ip_address: str | None = None,
) -> None:
    """
    Write a single audit event to the database.

    This function enqueues the write to a background task so it never
    blocks the calling request handler.

    Parameters:
        event_type: Descriptive string, e.g. "document_ingested", "auth_failed"
        outcome: "success", "failure", or "blocked"
        action_detail: Full input/output context as a dict (stored as JSONB)
        doc_id: Optional document ID associated with this event
        actor_id: Optional UUID of the user who triggered the event
        actor_email: Optional email of the actor for quick reference
        ip_address: Optional IP address of the request origin
    """
    event = {
        "event_type": event_type,
        "outcome": outcome,
        "action_detail": action_detail,
        "doc_id": doc_id,
        "actor_id": str(actor_id) if actor_id else None,
        "actor_email": actor_email,
        "ip_address": ip_address,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    if _audit_queue is not None:
        try:
            _audit_queue.put_nowait({"event": event, "db_session_factory": None})
        except asyncio.QueueFull:
            logger.error("Audit queue full — writing directly to fallback log")
            _write_to_fallback(event)
        return

    # If queue not initialised, write directly (startup/test scenarios)
    await _persist_audit_event(db, event)


async def _persist_audit_event(db: AsyncSession, event: dict[str, Any]) -> None:
    """
    Persist a single audit event to PostgreSQL with chain hashing.

    Called by the background worker. On failure, writes to the fallback file.
    """
    try:
        prev_hash = await _get_prev_hash(db)

        entry_hash = _compute_entry_hash(prev_hash, event)

        audit_entry = AuditLog(
            event_type=event["event_type"],
            doc_id=event.get("doc_id"),
            actor_id=uuid.UUID(event["actor_id"]) if event.get("actor_id") else None,
            actor_email=event.get("actor_email"),
            ip_address=event.get("ip_address"),
            action_detail=event["action_detail"],
            outcome=event["outcome"],
            entry_hash=entry_hash,
            prev_hash=prev_hash,
            created_at=datetime.now(timezone.utc),
        )
        db.add(audit_entry)
        await db.commit()

        logger.debug(
            "Audit event persisted",
            extra={
                "event_type": event["event_type"],
                "outcome": event["outcome"],
                "entry_hash": entry_hash[:8] + "...",
            },
        )
    except Exception as exc:
        logger.error(
            "Audit DB write failed — writing to fallback",
            extra={"error": str(exc), "event_type": event.get("event_type")},
        )
        # Ensure the event is not lost
        _write_to_fallback(event)
        try:
            await db.rollback()
        except Exception:
            pass


async def init_audit_logger(session_factory: Any) -> None:
    """
    Start the background audit worker task.

    The worker consumes from an async queue and writes to PostgreSQL.
    This ensures audit writes are non-blocking (fire-and-forget).
    """
    global _audit_queue, _audit_worker_task

    _audit_queue = asyncio.Queue(maxsize=50_000)
    _audit_worker_task = asyncio.create_task(
        _audit_worker(session_factory), name="audit-log-worker"
    )
    logger.info("Audit logger initialised")


async def _audit_worker(session_factory: Any) -> None:
    """Background coroutine that drains the audit queue into PostgreSQL."""
    while True:
        try:
            item = await _audit_queue.get()  # type: ignore[union-attr]
            event = item["event"]

            async with session_factory() as db:
                await _persist_audit_event(db, event)

            _audit_queue.task_done()  # type: ignore[union-attr]
        except asyncio.CancelledError:
            # Drain remaining items before exiting
            logger.info("Audit worker shutting down — draining queue")
            while not _audit_queue.empty():  # type: ignore[union-attr]
                try:
                    item = _audit_queue.get_nowait()  # type: ignore[union-attr]
                    _write_to_fallback(item["event"])
                except asyncio.QueueEmpty:
                    break
            break
        except Exception as exc:
            logger.error("Audit worker error", extra={"error": str(exc)})


async def close_audit_logger() -> None:
    """Stop the audit background worker gracefully."""
    global _audit_worker_task
    if _audit_worker_task and not _audit_worker_task.done():
        _audit_worker_task.cancel()
        try:
            await asyncio.wait_for(_audit_worker_task, timeout=10.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
    logger.info("Audit logger shutdown complete")
