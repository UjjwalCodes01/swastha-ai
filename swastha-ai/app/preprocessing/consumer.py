"""
Kafka Consumer for Layer 1.

Listens to `raw.documents.ingested`.
Features:
  - Redis distributed locks to prevent double-processing.
  - Manual Kafka offset commits (at-least-once delivery).
  - Retry counter in Redis (max 3 retries).
  - Dead Letter Queue (DLQ) publishing on failure.
  - Graceful shutdown.
"""

from __future__ import annotations

import asyncio
import json
import logging
import signal
from typing import Any

from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)

try:
    from aiokafka import AIOKafkaConsumer
except ImportError:  # pragma: no cover
    AIOKafkaConsumer = None  # type: ignore[assignment]


from app.config import get_settings
from app.db.connection import get_session_factory
from app.dependencies import get_redis
from app.preprocessing.embedder.chroma_store import ChromaStore
from app.preprocessing.embedder.embedding_service import EmbeddingService
from app.preprocessing.pipeline import PreprocessingPipeline
from app.queue.kafka_producer import get_kafka_producer
from app.storage.minio_client import get_minio_client

_CONSUMER_GROUP = "rxflow-preprocessor"
_TOPIC = "raw.documents.ingested"
_DLQ_TOPIC = "raw.documents.ingested.dlq"
_MAX_RETRIES = 3
_LOCK_TTL = 600  # 10 minutes


class IngestedPayload(BaseModel):
    """Validation schema for incoming Kafka message."""
    doc_id: str
    raw_storage_path: str
    submission_type: str
    portal_source: str
    submitted_by: str | None = None
    checksum: str
    original_filename: str
    file_size_bytes: int
    timestamp: str


class PreprocessorConsumer:
    """Consumes raw document events and drives the pipeline."""

    def __init__(self) -> None:
        self.settings = get_settings()
        self.consumer: Any = None
        self.pipeline: PreprocessingPipeline | None = None
        self._running = False
        self._redis = None

    async def start(self) -> None:
        """Initialize dependencies and start the consumer loop."""
        if AIOKafkaConsumer is None:
            raise ImportError("aiokafka not installed")

        self._redis = await get_redis()
        minio_client = await get_minio_client()
        session_factory = get_session_factory()
        
        embedding_service = EmbeddingService(batch_size=self.settings.embedding_batch_size)
        await embedding_service.initialize(self._redis)
        
        chroma_host = self.settings.chroma_host
        chroma_port = self.settings.chroma_port
        chroma_store = ChromaStore(host=chroma_host, port=chroma_port)
        await chroma_store.initialize()

        self.pipeline = PreprocessingPipeline(
            minio_client=minio_client,
            db_session_factory=session_factory,
            embedding_service=embedding_service,
            chroma_store=chroma_store,
            ocr_concurrency=self.settings.ocr_concurrency,
        )

        self.consumer = AIOKafkaConsumer(
            _TOPIC,
            bootstrap_servers=self.settings.kafka_bootstrap_servers,
            group_id=_CONSUMER_GROUP,
            enable_auto_commit=False,  # VERY IMPORTANT: manual commits only
            auto_offset_reset="earliest",
        )

        await self.consumer.start()
        self._running = True
        logger.info(f"Consumer started for topic: {_TOPIC}")

        # Handle SIGTERM
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self.stop)
            except NotImplementedError:
                pass

        await self._consume_loop()

    def stop(self) -> None:
        """Trigger graceful shutdown."""
        logger.info("Shutdown signal received — stopping consumer")
        self._running = False

    async def _consume_loop(self) -> None:
        """Main event loop."""
        try:
            while self._running:
                # Use getmany to process in batches if needed, but we process sequentially
                # to respect the "one message at a time per partition" rule.
                result = await self.consumer.getmany(timeout_ms=1000, max_records=1)
                
                for tp, messages in result.items():
                    for msg in messages:
                        await self._process_message(msg, tp)
                        
                        if not self._running:
                            break
                    if not self._running:
                        break
        except Exception as exc:
            logger.critical("Consumer loop crashed", extra={"error": str(exc)}, exc_info=True)
        finally:
            await self.consumer.stop()
            logger.info("Consumer stopped cleanly")

    async def _process_message(self, msg: Any, tp: Any) -> None:
        """Process a single Kafka message."""
        raw_value = msg.value.decode("utf-8")
        try:
            data = json.loads(raw_value)
            payload = IngestedPayload(**data)
        except (json.JSONDecodeError, ValidationError) as exc:
            logger.error("Invalid Kafka payload — skipping and committing offset", 
                         extra={"error": str(exc), "value": raw_value})
            await self.consumer.commit({tp: msg.offset + 1})
            return

        doc_id = payload.doc_id
        lock_key = f"lock:preprocess:{doc_id}"
        
        # 1. Acquire Redis Lock
        lock_acquired = await self._redis.set(lock_key, "locked", nx=True, ex=_LOCK_TTL)
        if not lock_acquired:
            logger.warning(f"Document {doc_id} is already being processed — skipping")
            return

        try:
            # 2. Update status to 'preprocessing'
            await self._update_status(doc_id, "preprocessing")

            # 3. Download raw file from MinIO
            file_bytes = await self._download_file(payload.raw_storage_path)

            # 4. Run Pipeline
            await self.pipeline.process_document(
                doc_id=doc_id,
                file_bytes=file_bytes,
                submission_type=payload.submission_type,
                portal_source=payload.portal_source,
                submitted_by=payload.submitted_by,
                original_filename=payload.original_filename,
            )

            # 5. Success: Commit Offset
            await self.consumer.commit({tp: msg.offset + 1})
            await self._redis.delete(lock_key)
            await self._clear_retry_count(doc_id)

        except Exception as exc:
            logger.error(f"Processing failed for {doc_id}", extra={"error": str(exc)})
            await self._redis.delete(lock_key)
            
            # 6. Failure handling (Retries / DLQ)
            retry_count = await self._increment_retry_count(doc_id)
            
            if retry_count < _MAX_RETRIES:
                logger.info(f"Retry {retry_count}/{_MAX_RETRIES} for {doc_id}. Will reprocess later.")
                # Do NOT commit offset. The consumer will re-fetch it.
                # Delay slightly to prevent hot-looping
                await asyncio.sleep(5)
            else:
                logger.error(f"Max retries reached for {doc_id}. Sending to DLQ.")
                await self._publish_to_dlq(payload.model_dump(), str(exc))
                await self._update_status(doc_id, "failed")
                await self._clear_retry_count(doc_id)
                # Commit offset so we move past the poison pill
                await self.consumer.commit({tp: msg.offset + 1})

    async def _download_file(self, minio_path: str) -> bytes:
        minio = await get_minio_client()
        bucket = self.settings.minio_bucket_raw
        response = await minio.get_object(Bucket=bucket, Key=minio_path)
        return await response["Body"].read()

    async def _update_status(self, doc_id: str, status: str) -> None:
        from sqlalchemy import text
        session_factory = get_session_factory()
        async with session_factory() as session:
            await session.execute(
                text("UPDATE submissions SET status = :status WHERE doc_id = :doc_id"),
                {"status": status, "doc_id": doc_id}
            )
            await session.commit()

    async def _increment_retry_count(self, doc_id: str) -> int:
        key = f"retry:{doc_id}"
        count = await self._redis.incr(key)
        if count == 1:
            await self._redis.expire(key, 86400)  # 24 hours
        return int(count)

    async def _clear_retry_count(self, doc_id: str) -> None:
        await self._redis.delete(f"retry:{doc_id}")

    async def _publish_to_dlq(self, original_payload: dict, error: str) -> None:
        producer = await get_kafka_producer()
        dlq_payload = {
            **original_payload,
            "failed_at": datetime.now().isoformat(),
            "error_detail": error,
        }
        await producer.publish(_DLQ_TOPIC, dlq_payload, key=original_payload.get("doc_id"))
