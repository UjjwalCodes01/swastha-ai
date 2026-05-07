"""
Kafka Publisher for the Preprocessing Layer.

Publishes two events:
  1. `documents.preprocessed`: Full metadata summary of the processed document.
  2. `documents.chunks.ready`: Signals to Layer 3 which ChromaDB collection to query.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from app.queue.kafka_producer import get_kafka_producer

logger = logging.getLogger(__name__)


class PreprocessingPublisher:
    """Publishes completion events to Kafka."""

    def __init__(self) -> None:
        pass

    async def publish_completion(
        self,
        doc_id: str,
        processed_storage_path: str,
        submission_type: str,
        portal_source: str,
        submitted_by: str | None,
        original_filename: str,
        chunk_count: int,
        table_count: int,
        page_count: int,
        word_count: int,
        extraction_confidence: float,
        language: str,
        language_confidence: float,
        is_multilingual: bool,
        ocr_used: bool,
        ocr_pages: int,
        extractors_used: list[str],
        metadata: dict[str, Any],
        flags: dict[str, bool],
        processing_duration_ms: int,
        chroma_collection: str,
        chunk_ids: list[str],
    ) -> None:
        """
        Publish the two events required by Layer 3.
        """
        try:
            producer = await get_kafka_producer()
        except RuntimeError:
            logger.error("Cannot publish: Kafka producer not initialised")
            return

        now_iso = datetime.now(timezone.utc).isoformat()

        # 1. Main preprocessed event
        main_payload = {
            "doc_id": doc_id,
            "processed_storage_path": processed_storage_path,
            "submission_type": submission_type,
            "portal_source": portal_source,
            "submitted_by": submitted_by,
            "original_filename": original_filename,
            "chunk_count": chunk_count,
            "table_count": table_count,
            "page_count": page_count,
            "word_count": word_count,
            "extraction_confidence": float(extraction_confidence),
            "language": language,
            "language_confidence": float(language_confidence),
            "is_multilingual": is_multilingual,
            "ocr_used": ocr_used,
            "ocr_pages": ocr_pages,
            "extractors_used": extractors_used,
            "metadata": metadata,
            "flags": flags,
            "processing_duration_ms": processing_duration_ms,
            "preprocessed_at": now_iso,
        }

        await producer.publish(
            topic="documents.preprocessed",
            payload=main_payload,
            key=doc_id,  # ensures all events for this doc go to same partition
        )

        # 2. Chunks ready event (specifically for RAG trigger)
        chunks_payload = {
            "doc_id": doc_id,
            "submission_type": submission_type,
            "chroma_collection": chroma_collection,
            "chunk_ids": chunk_ids,
            "chunk_count": chunk_count,
            "preprocessed_at": now_iso,
        }

        await producer.publish(
            topic="documents.chunks.ready",
            payload=chunks_payload,
            key=doc_id,
        )

        logger.debug(
            "Published preprocessing completion events",
            extra={"doc_id": doc_id, "chunks": chunk_count}
        )
