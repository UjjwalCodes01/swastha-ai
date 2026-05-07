"""
Retry Handler — implements delayed retry via retry topics.

Three levels of retry with exponential backoff:
  retry.1 — 5 second delay
  retry.2 — 30 second delay
  retry.3 — 5 minute delay

After retry.3 fails → DLQ. No more retries.

Delay mechanism: the original message timestamp + delay window is checked.
If the window hasn't elapsed, the consumer sleeps for the remainder.
This avoids separate timer infrastructure — just sleep in the consumer.

Usage:
    handler = RetryHandler(level=1, bootstrap_servers=..., redis=..., event_store=...)
    await handler.start()  # runs until stop()
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

from app.messagebus.consumer import BaseConsumer
from app.messagebus.event_store import EventStore

# Delay in seconds per retry level
RETRY_DELAYS: dict[int, int] = {1: 5, 2: 30, 3: 300}

# Primary topics we handle retries for
_RETRY_PRIMARY_TOPICS = [
    "raw.documents.ingested",
    "documents.preprocessed",
    "documents.anonymised",
    "documents.summarised",
    "documents.classified",
    "reports.generated",
]


class RetryHandler:
    """
    Consumes retry.{level} topics and re-processes after the delay window.
    One instance per retry level.
    """

    def __init__(
        self,
        level: int,
        bootstrap_servers: str,
        redis_client: Any,
        event_store: EventStore,
        original_handler: Any,  # The original business handler callable
    ) -> None:
        if level not in RETRY_DELAYS:
            raise ValueError(f"Invalid retry level: {level}. Must be 1, 2, or 3.")
        self.level = level
        self.delay_seconds = RETRY_DELAYS[level]
        self._bootstrap = bootstrap_servers
        self._redis = redis_client
        self._event_store = event_store
        self._original_handler = original_handler  # What to call after delay

        self._consumer = BaseConsumer(
            bootstrap_servers=bootstrap_servers,
            group_id=f"rxflow-retry-{level}",
            redis_client=redis_client,
            event_store=event_store,
            max_retries=3 - level,  # Remaining retries after this level
        )

    async def start(self) -> None:
        """Start consuming all retry.{level} topics."""
        topics = [f"{t}.retry.{self.level}" for t in _RETRY_PRIMARY_TOPICS]
        logger.info(f"Retry handler level={self.level} ({self.delay_seconds}s delay) starting on {len(topics)} topics")
        await self._consumer.start(topics, self._handle_retry_message)

    def stop(self) -> None:
        self._consumer.stop()

    async def _handle_retry_message(self, record: Any, payload: dict) -> None:
        """
        Check if the delay window has elapsed; if not, sleep for the remainder.
        Then re-invoke the original handler.
        """
        retry_timestamp_ms = payload.get("_retry_timestamp")
        if retry_timestamp_ms is None:
            # Shouldn't happen — use record timestamp as fallback
            retry_timestamp_ms = record.timestamp

        retry_timestamp_sec = retry_timestamp_ms / 1000.0
        elapsed = time.time() - retry_timestamp_sec
        remaining = self.delay_seconds - elapsed

        if remaining > 0:
            logger.debug(
                f"Retry level={self.level}: sleeping {remaining:.2f}s before reprocessing "
                f"doc_id={payload.get('doc_id', 'unknown')}"
            )
            await asyncio.sleep(remaining)

        # Strip retry metadata before passing to original handler
        clean_payload = {
            k: v for k, v in payload.items()
            if not k.startswith("_retry_")
        }

        # Restore original topic (for error routing)
        original_topic = payload.get("_original_topic", record.topic.rsplit(".retry.", 1)[0])

        logger.info(
            f"Retry level={self.level} reprocessing: {original_topic} "
            f"doc_id={clean_payload.get('doc_id', 'unknown')}"
        )

        # Re-invoke the original business handler
        await self._original_handler(record, clean_payload)


class RetryConsumerManager:
    """
    Manages all 3 retry levels for a given service.
    Start all levels together in the service's lifespan.
    """

    def __init__(
        self,
        bootstrap_servers: str,
        redis_client: Any,
        event_store: EventStore,
        original_handler: Any,
    ) -> None:
        self._handlers = [
            RetryHandler(level, bootstrap_servers, redis_client, event_store, original_handler)
            for level in range(1, 4)
        ]
        self._tasks: list[asyncio.Task] = []

    async def start(self) -> None:
        """Start all 3 retry consumer levels as background tasks."""
        for handler in self._handlers:
            task = asyncio.create_task(
                handler.start(),
                name=f"retry-handler-level-{handler.level}",
            )
            self._tasks.append(task)
        logger.info("RetryConsumerManager started (levels 1, 2, 3)")

    async def stop(self) -> None:
        """Stop all retry consumers."""
        for handler in self._handlers:
            handler.stop()
        for task in self._tasks:
            task.cancel()
            try:
                await asyncio.wait_for(task, timeout=10.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
        self._tasks.clear()
        logger.info("RetryConsumerManager stopped")
