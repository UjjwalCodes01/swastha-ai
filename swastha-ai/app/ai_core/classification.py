"""Assessment and classification module for Layer 3.

Three sub-tasks:
1. Completeness checker     — rule engine per form type (deterministic)
2. SAE severity classifier  — keyword heuristics with BERT upgrade path
3. Duplicate detector       — FAISS similarity search via ChromaDB stored embeddings

All three run independently and are aggregated into a single Scorecard.
"""

from __future__ import annotations

import logging
from typing import Any

from app.ai_core.schemas import ClassificationResult, Scorecard, SubmissionType

logger = logging.getLogger(__name__)

# Similarity threshold for flagging a submission as a potential duplicate
_DUPLICATE_SIMILARITY_THRESHOLD = 0.92
_DUPLICATE_MAX_CANDIDATES = 5


class Classifier:
    """Rule-backed regulatory classification with ChromaDB-powered duplicate detection."""

    model_id = "swastha-rule-bert-fallback-classifier"
    model_version = "1.1.0"

    REQUIRED_FIELDS = {
        "drug": ["applicant_name", "drug_name", "indication", "dosage_form"],
        "medical_device": ["device_name", "risk_class", "manufacturer", "intended_use"],
        "clinical_trial": ["protocol_number", "phase", "sponsor", "primary_endpoint"],
        "sae": ["patient_id", "event_date", "outcome", "causality"],
    }

    def __init__(self) -> None:
        self._bert = self._load_bert()

    def classify(
        self,
        doc_id: str,
        submission_type: SubmissionType,
        text: str,
        metadata: dict | None = None,
        duplicate_candidates: list[dict] | None = None,
    ) -> Scorecard:
        """Synchronous classify — accepts pre-computed duplicate_candidates."""
        metadata = metadata or {}
        duplicate_candidates = duplicate_candidates or []
        missing = self._missing_required(submission_type, text, metadata)
        total = len(self.REQUIRED_FIELDS[submission_type])
        completeness = round((total - len(missing)) / total, 3)
        classification = self._classification(submission_type, text, completeness, duplicate_candidates)
        return Scorecard(
            doc_id=doc_id,
            completeness_score=completeness,
            missing_required_fields=missing,
            duplicate_candidates=duplicate_candidates,
            classification=classification,
            model_id=self.model_id,
            model_version=self.model_version,
        )

    async def async_classify(
        self,
        doc_id: str,
        submission_type: SubmissionType,
        text: str,
        metadata: dict | None = None,
        embedding: list[float] | None = None,
        chroma_client: Any | None = None,
    ) -> Scorecard:
        """Async classify that performs ChromaDB similarity search for duplicate detection."""
        metadata = metadata or {}
        missing = self._missing_required(submission_type, text, metadata)
        total = len(self.REQUIRED_FIELDS[submission_type])
        completeness = round((total - len(missing)) / total, 3)

        duplicate_candidates = await self._find_duplicates(
            doc_id, submission_type, embedding, chroma_client
        )

        classification = self._classification(submission_type, text, completeness, duplicate_candidates)
        return Scorecard(
            doc_id=doc_id,
            completeness_score=completeness,
            missing_required_fields=missing,
            duplicate_candidates=duplicate_candidates,
            classification=classification,
            model_id=self.model_id,
            model_version=self.model_version,
        )

    # ── Duplicate detection via ChromaDB ──────────────────────────────────────

    async def _find_duplicates(
        self,
        doc_id: str,
        submission_type: SubmissionType,
        embedding: list[float] | None,
        chroma_client: Any | None,
    ) -> list[dict]:
        """Query ChromaDB for similar documents using the document embedding."""
        if embedding is None or chroma_client is None:
            return []

        collection_name = {
            "drug": "drug_submissions",
            "medical_device": "medical_devices",
            "clinical_trial": "clinical_trials",
            "sae": "sae_reports",
        }.get(submission_type, "misc_documents")

        try:
            collection = await chroma_client.get_collection(name=collection_name)
            results = await collection.query(
                query_embeddings=[embedding],
                n_results=_DUPLICATE_MAX_CANDIDATES + 1,   # +1 because self will be returned
                include=["metadatas", "distances"],
            )

            candidates = []
            metadatas = results.get("metadatas", [[]])[0]
            distances = results.get("distances", [[]])[0]

            for meta, dist in zip(metadatas, distances):
                candidate_doc_id = meta.get("doc_id", "")
                if candidate_doc_id == doc_id:
                    continue  # skip self
                similarity = 1.0 - float(dist)  # ChromaDB returns L2 distance by default
                if similarity >= _DUPLICATE_SIMILARITY_THRESHOLD:
                    candidates.append({
                        "doc_id": candidate_doc_id,
                        "similarity": round(similarity, 4),
                        "chunk_type": meta.get("chunk_type", "unknown"),
                    })

            if candidates:
                logger.info(
                    "Duplicate candidates found",
                    extra={"doc_id": doc_id, "count": len(candidates)},
                )
            return candidates

        except Exception as exc:
            logger.warning(
                "ChromaDB duplicate search failed — skipping deduplication",
                extra={"doc_id": doc_id, "error": str(exc)},
            )
            return []

    # ── BERT upgrade path ─────────────────────────────────────────────────────

    def _load_bert(self) -> Any:
        """Attempt to load a fine-tuned BERT classifier for SAE severity."""
        try:
            from transformers import pipeline  # type: ignore[import]
            # Fine-tuned model path — set SWASTHA_SAE_BERT_MODEL env var in production
            import os
            model_path = os.environ.get("SWASTHA_SAE_BERT_MODEL", "")
            if not model_path:
                return None
            clf = pipeline("text-classification", model=model_path, tokenizer=model_path)
            logger.info("BERT SAE severity classifier loaded", extra={"model": model_path})
            return clf
        except Exception:
            return None

    def _sae_severity_bert(self, text: str) -> tuple[str, float] | None:
        """Run BERT classifier if available. Returns (label, confidence) or None."""
        if self._bert is None:
            return None
        try:
            result = self._bert(text[:512], truncation=True)[0]
            label: str = result["label"].lower()
            score: float = float(result["score"])
            return label, score
        except Exception:
            return None

    # ── Rule-based classification ─────────────────────────────────────────────

    def _missing_required(self, submission_type: str, text: str, metadata: dict) -> list[str]:
        lower = text.lower()
        return [
            field for field in self.REQUIRED_FIELDS[submission_type]
            if not metadata.get(field) and field.replace("_", " ") not in lower
        ]

    def _classification(
        self,
        submission_type: str,
        text: str,
        completeness: float,
        duplicate_candidates: list[dict],
    ) -> ClassificationResult:
        lower = text.lower()
        labels = [submission_type, f"completeness:{completeness:.2f}"]
        sae_severity = None
        risk_score = 1.0 - completeness

        if submission_type == "sae":
            # Try BERT first, fall back to keywords
            bert_result = self._sae_severity_bert(text)
            if bert_result:
                sae_severity, severity_score = bert_result
                labels.append(f"sae:{sae_severity}(bert)")
            else:
                sae_severity, severity_score = self._sae_severity_keywords(lower)
                labels.append(f"sae:{sae_severity}(keyword)")
            risk_score = max(risk_score, severity_score)

        if duplicate_candidates:
            highest_sim = max(c["similarity"] for c in duplicate_candidates)
            risk_score = max(risk_score, highest_sim * 0.8)
            labels.append(f"possible_duplicate(sim={highest_sim:.2f})")

        if risk_score >= 0.85:
            priority = "critical"
        elif risk_score >= 0.65:
            priority = "high"
        elif risk_score >= 0.4:
            priority = "medium"
        elif risk_score >= 0.2:
            priority = "low"
        else:
            priority = "informational"

        requires_review = priority in {"critical", "high"} or sae_severity in {"death", "life_threatening"}
        return ClassificationResult(
            category=self._category(submission_type),
            subcategory=sae_severity if submission_type == "sae" else None,
            priority=priority,
            risk_score=round(min(1.0, risk_score), 3),
            sae_severity=sae_severity,
            requires_review=requires_review,
            labels=labels,
            confidence=0.92 if self._bert else (0.86 if completeness >= 0.75 else 0.72),
        )

    def _sae_severity_keywords(self, lower_text: str) -> tuple[str, float]:
        if any(t in lower_text for t in ("death", "fatal", "expired")):
            return "death", 0.98
        if any(t in lower_text for t in ("life-threatening", "life threatening", "icu", "ventilator")):
            return "life_threatening", 0.92
        if any(t in lower_text for t in ("hospitalisation", "hospitalization", "admitted")):
            return "hospitalisation", 0.76
        if any(t in lower_text for t in ("disability", "congenital anomaly")):
            return "disability", 0.72
        return "other", 0.45

    def _category(self, submission_type: str) -> str:
        return {
            "drug": "Drug regulatory submission",
            "medical_device": "Medical device regulatory submission",
            "clinical_trial": "Clinical trial submission",
            "sae": "Serious adverse event report",
        }[submission_type]
