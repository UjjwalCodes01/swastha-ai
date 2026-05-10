"""Kafka consumer entry point for Layer 3 AI Core."""

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

from app.ai_core.pipeline import AICorePipeline
from app.ai_core.topics import (
    AI_CORE_INPUT_TOPICS,
    DOCUMENTS_ANONYMISED,
    DOCUMENTS_COMPARISON_REQUESTED,
    DOCUMENTS_PREPROCESSED,
)
from app.config import get_settings
from app.db.connection import get_session_factory
from app.queue.kafka_producer import get_kafka_producer
from app.storage.minio_client import get_minio_client

logger = logging.getLogger(__name__)


class AICoreConsumer:
    """Consumes Layer 3 input events and dispatches to AI modules."""

    def __init__(self) -> None:
        self.settings = get_settings()
        self.consumer: Any = None
        self.pipeline: AICorePipeline | None = None
        self._running = False

    async def start(self) -> None:
        if AIOKafkaConsumer is None:
            raise ImportError("aiokafka not installed")

        minio = await get_minio_client()
        producer = await get_kafka_producer()
        self.pipeline = AICorePipeline(minio, producer, get_session_factory())
        self.consumer = AIOKafkaConsumer(
            *AI_CORE_INPUT_TOPICS,
            bootstrap_servers=self.settings.kafka_bootstrap_servers,
            group_id="swastha-ai-core",
            enable_auto_commit=False,
            auto_offset_reset="earliest",
            value_deserializer=lambda value: json.loads(value.decode("utf-8")),
        )
        await self.consumer.start()
        self._running = True
        logger.info("Layer 3 AI Core consumer started", extra={"topics": AI_CORE_INPUT_TOPICS})

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
                await self._dispatch(message)
                await self.consumer.commit()
        finally:
            if self.consumer:
                await self.consumer.stop()

    async def _dispatch(self, message: Any) -> None:
        if not self.pipeline:
            raise RuntimeError("AI Core pipeline not initialised")
        payload = message.value
        if message.topic == DOCUMENTS_PREPROCESSED:
            await self.pipeline.handle_preprocessed(payload)
        elif message.topic == DOCUMENTS_ANONYMISED:
            await self.pipeline.handle_anonymised(payload)
        elif message.topic == DOCUMENTS_COMPARISON_REQUESTED:
            await self.pipeline.handle_comparison_requested(payload)
        else:
            logger.warning("Ignoring unsupported AI Core topic", extra={"topic": message.topic})
