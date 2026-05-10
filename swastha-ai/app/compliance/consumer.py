"""Kafka consumer for Layer 4 compliance and governance."""

from __future__ import annotations

import asyncio
import json
import logging
import signal
from typing import Any

try:
    from aiokafka import AIOKafkaConsumer
except ImportError:  # pragma: no cover
    AIOKafkaConsumer = None  # type: ignore[assignment]

from app.compliance.pipeline import CompliancePipeline
from app.config import get_settings
from app.db.connection import get_session_factory
from app.queue.kafka_producer import get_kafka_producer
from app.storage.minio_client import get_minio_client

logger = logging.getLogger(__name__)

COMPLIANCE_TOPICS = [
    "documents.anonymised",
    "documents.summarised",
    "documents.classified",
    "reports.generated",
]


class ComplianceConsumer:
    """Consumes AI outputs and applies mandatory governance checks."""

    def __init__(self) -> None:
        self.settings = get_settings()
        self.consumer: Any = None
        self.pipeline: CompliancePipeline | None = None
        self._running = False

    async def start(self) -> None:
        if AIOKafkaConsumer is None:
            raise ImportError("aiokafka not installed")

        self.pipeline = CompliancePipeline(
            await get_minio_client(),
            await get_kafka_producer(),
            get_session_factory(),
        )
        self.consumer = AIOKafkaConsumer(
            *COMPLIANCE_TOPICS,
            bootstrap_servers=self.settings.kafka_bootstrap_servers,
            group_id="swastha-compliance",
            enable_auto_commit=False,
            auto_offset_reset="earliest",
            value_deserializer=lambda value: json.loads(value.decode("utf-8")),
        )
        await self.consumer.start()
        self._running = True
        logger.info("Layer 4 Compliance consumer started", extra={"topics": COMPLIANCE_TOPICS})

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self.stop)
            except NotImplementedError:
                pass
        await self._consume_loop()

    def stop(self) -> None:
        self._running = False

    async def _consume_loop(self) -> None:
        try:
            async for message in self.consumer:
                if not self._running:
                    break
                await self.pipeline.assess(message.topic, message.value)
                await self.consumer.commit()
        finally:
            if self.consumer:
                await self.consumer.stop()
