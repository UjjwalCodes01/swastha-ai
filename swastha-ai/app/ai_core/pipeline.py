"""Layer 3 AI Core orchestration."""

from __future__ import annotations

import hashlib
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from app.ai_core.anonymisation import Anonymiser
from app.ai_core.classification import Classifier
from app.ai_core.comparison import Comparator
from app.ai_core.storage import chunks_to_text, load_json, save_json
from app.ai_core.summarisation import Summariser
from app.ai_core.topics import (
    DOCUMENTS_ANONYMISED,
    DOCUMENTS_CLASSIFIED,
    DOCUMENTS_SUMMARISED,
    NOTIFICATIONS_EVENTS,
    REPORTS_GENERATED,
)

logger = logging.getLogger(__name__)


class AICorePipeline:
    """Coordinates anonymisation, summarisation, classification, and comparison."""

    def __init__(
        self,
        minio_client: Any,
        producer: Any,
        db_session_factory: Any | None = None,
        chroma_client: Any | None = None,
    ) -> None:
        self.minio = minio_client
        self.producer = producer
        self.db_session_factory = db_session_factory
        self.chroma_client = chroma_client
        self.anonymiser = Anonymiser()
        self.summariser = Summariser()
        self.classifier = Classifier()
        self.comparator = Comparator()

    async def handle_preprocessed(self, event: dict[str, Any]) -> dict[str, Any]:
        processed = await load_json(self.minio, event["processed_storage_path"])
        text = chunks_to_text(processed)
        result = self.anonymiser.anonymise(event["doc_id"], text)
        path = self._artefact_path(event["submission_type"], event["doc_id"], "anonymised.json")
        await save_json(
            self.minio,
            path,
            {
                "doc_id": event["doc_id"],
                "submission_type": event["submission_type"],
                "anonymised_text": result.anonymised_text,
                "entities": [entity.model_dump() for entity in result.entities],
                "metadata": processed.get("metadata", {}),
                "source_processed_path": event["processed_storage_path"],
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        payload = {
            "event_version": "1.0",
            "doc_id": event["doc_id"],
            "submission_type": event["submission_type"],
            "anonymised_storage_path": path,
            "pii_entities_removed": result.entity_count,
            "anonymisation_method": result.method,
            "confidence": result.confidence,
            "model_version": result.model_version,
        }
        await self.producer.publish(DOCUMENTS_ANONYMISED, payload, key=event["doc_id"])
        await self._record_ai_decision(
            event["doc_id"],
            "anonymisation",
            {**result.model_dump(), "anonymised_storage_path": path},
        )
        return payload

    async def handle_anonymised(self, event: dict[str, Any]) -> dict[str, Any]:
        artefact = await load_json(self.minio, event["anonymised_storage_path"])
        text = artefact.get("anonymised_text", "")
        metadata = artefact.get("metadata", {})

        # Retrieve stored embedding for this doc (if available) for dedup
        embedding = await self._load_doc_embedding(event["doc_id"])

        # Run summarisation and classification concurrently
        import asyncio
        summary, scorecard = await asyncio.gather(
            self.summariser.async_summarise(event["doc_id"], event["submission_type"], text, metadata),
            self.classifier.async_classify(
                event["doc_id"],
                event["submission_type"],
                text,
                metadata,
                embedding=embedding,
                chroma_client=self.chroma_client,
            ),
        )

        summary_path = self._artefact_path(event["submission_type"], event["doc_id"], "summary.json")
        await save_json(self.minio, summary_path, summary.model_dump())
        await save_json(
            self.minio,
            self._artefact_path(event["submission_type"], event["doc_id"], "classification.json"),
            scorecard.model_dump(),
        )

        summary_payload = {
            "event_version": "1.0",
            "doc_id": event["doc_id"],
            "submission_type": event["submission_type"],
            "summary_storage_path": summary_path,
            "summary_word_count": summary.word_count,
            "key_findings": summary.key_findings,
            "model_id": summary.model_id,
            "model_version": summary.model_version,
            "prompt_tokens": summary.prompt_tokens,
            "completion_tokens": summary.completion_tokens,
            "confidence": summary.confidence,
        }
        classified_payload = {
            "event_version": "1.0",
            "doc_id": event["doc_id"],
            "submission_type": event["submission_type"],
            "classification": scorecard.classification.model_dump(),
            "model_id": scorecard.model_id,
            "model_version": scorecard.model_version,
        }
        await self.producer.publish(DOCUMENTS_SUMMARISED, summary_payload, key=event["doc_id"])
        await self.producer.publish(DOCUMENTS_CLASSIFIED, classified_payload, key=event["doc_id"])
        if scorecard.classification.priority in {"critical", "high"}:
            await self.producer.publish(
                NOTIFICATIONS_EVENTS,
                {
                    "event_id": str(uuid.uuid4()),
                    "event_version": "1.0",
                    "source_layer": "layer3",
                    "severity": "CRITICAL" if scorecard.classification.priority == "critical" else "WARNING",
                    "alert_type": "sae_critical" if scorecard.classification.sae_severity in {"death", "life_threatening"} else "review_required",
                    "title": f"{scorecard.classification.priority.title()} priority document {event['doc_id']}",
                    "message": f"AI Core flagged {event['doc_id']} for review: {scorecard.classification.labels}",
                    "doc_id": event["doc_id"],
                    "affected_topic": DOCUMENTS_CLASSIFIED,
                    "affected_group": "swastha-ai-core",
                    "metric_value": float(scorecard.classification.risk_score),
                    "metric_threshold": 0.65,
                    "extra": {
                        "priority": scorecard.classification.priority,
                        "requires_review": str(scorecard.classification.requires_review),
                    },
                    "timestamp": int(time.time() * 1000),
                },
                key=event["doc_id"],
            )
        await self._record_ai_decision(event["doc_id"], "summarisation", summary.model_dump())
        await self._record_ai_decision(event["doc_id"], "classification", scorecard.model_dump())
        return {"summary": summary_payload, "classification": classified_payload}

    async def _load_doc_embedding(self, doc_id: str) -> list[float] | None:
        """Try to retrieve the stored embedding for a document from the DB or ChromaDB."""
        if not self.db_session_factory:
            return None
        try:
            from sqlalchemy import text
            async with self.db_session_factory() as session:
                result = await session.execute(
                    text("""
                        SELECT embedding FROM document_chunks
                        WHERE doc_id = :doc_id AND chunk_index = 0
                        LIMIT 1
                    """),
                    {"doc_id": doc_id},
                )
                row = result.first()
                if row and row[0]:
                    import json
                    emb = row[0]
                    if isinstance(emb, str):
                        emb = json.loads(emb)
                    return emb if isinstance(emb, list) else None
        except Exception as exc:
            logger.debug("Could not load embedding for dedup", extra={"doc_id": doc_id, "error": str(exc)})
        return None

    async def handle_comparison_requested(self, event: dict[str, Any]) -> dict[str, Any]:
        start = time.perf_counter()
        doc_a = await self._load_latest_anonymised(event["doc_id_a"])
        doc_b = await self._load_latest_anonymised(event["doc_id_b"])
        result = await self.comparator.async_compare(
            event["comparison_id"],
            event["doc_id_a"],
            event["doc_id_b"],
            doc_a.get("anonymised_text", ""),
            doc_b.get("anonymised_text", ""),
            event.get("comparison_aspects", []),
        )
        report_path = f"reports/comparison/{result.report_id}.md"
        await save_json(
            self.minio,
            report_path,
            {
                "report_id": result.report_id,
                "report_markdown": result.report_markdown,
                "changed_sections": result.changed_sections,
                "regulatory_significance": result.regulatory_significance,
            },
        )
        payload = {
            "event_version": "1.0",
            "report_id": result.report_id,
            "doc_id": event["doc_id_a"],
            "submission_type": doc_a.get("submission_type", "drug"),
            "report_type": "comparison",
            "report_storage_path": report_path,
            "report_format": "json",
            "page_count": 1,
            "generated_for": event.get("requested_by", "system"),
            "model_ids_used": ["deterministic-diff", "offline-change-narrator"],
            "generation_duration_ms": int((time.perf_counter() - start) * 1000),
            "report_metadata": {
                "comparison_id": event["comparison_id"],
                "doc_id_b": event["doc_id_b"],
                "additions": str(result.additions),
                "deletions": str(result.deletions),
            },
        }
        await self.producer.publish(REPORTS_GENERATED, payload, key=event["doc_id_a"])
        await self._record_ai_decision(event["doc_id_a"], "comparison", result.model_dump())
        return payload

    async def _load_latest_anonymised(self, doc_id: str) -> dict[str, Any]:
        if not self.db_session_factory:
            raise RuntimeError("Comparison requires db_session_factory to resolve anonymised artefacts.")
        from sqlalchemy import text

        async with self.db_session_factory() as session:
            result = await session.execute(
                text("""
                    SELECT action_detail
                    FROM audit_log
                    WHERE doc_id = :doc_id AND event_type = 'ai_core.anonymisation'
                    ORDER BY created_at DESC
                    LIMIT 1
                """),
                {"doc_id": doc_id},
            )
            row = result.first()
            if row:
                detail = row[0]
                if isinstance(detail, str):
                    import json
                    detail = json.loads(detail)
                if detail.get("anonymised_storage_path"):
                    return await load_json(self.minio, detail["anonymised_storage_path"])
        raise RuntimeError(f"No anonymised artefact found for {doc_id}")

    def _artefact_path(self, submission_type: str, doc_id: str, filename: str) -> str:
        now = datetime.now(timezone.utc)
        return f"ai-core/{submission_type}/{now.year}/{now.month:02d}/{now.day:02d}/{doc_id}/{filename}"

    async def _record_ai_decision(self, doc_id: str, decision_type: str, detail: dict[str, Any]) -> None:
        if not self.db_session_factory:
            return
        from app.db.models import AuditLog

        try:
            async with self.db_session_factory() as session:
                entry = AuditLog(
                    event_type=f"ai_core.{decision_type}",
                    doc_id=doc_id,
                    action_detail=detail,
                    outcome="success",
                    entry_hash=hashlib.sha256(
                        f"ai-core:{decision_type}:{doc_id}:{time.time_ns()}".encode()
                    ).hexdigest(),
                    prev_hash="managed-by-audit-logger",
                )
                session.add(entry)
                await session.commit()
        except Exception:
            # Audit logger is authoritative elsewhere; Layer 3 must not fail a document
            # solely because a local audit mirror insert failed.
            return
