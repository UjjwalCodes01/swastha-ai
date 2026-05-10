"""Assessment and classification module for Layer 3."""

from __future__ import annotations

from app.ai_core.schemas import ClassificationResult, Scorecard, SubmissionType


class Classifier:
    """Rule-backed regulatory classification with offline SAE severity heuristics."""

    model_id = "swastha-rule-bert-fallback-classifier"
    model_version = "1.0.0"

    REQUIRED_FIELDS = {
        "drug": ["applicant_name", "drug_name", "indication", "dosage_form"],
        "medical_device": ["device_name", "risk_class", "manufacturer", "intended_use"],
        "clinical_trial": ["protocol_number", "phase", "sponsor", "primary_endpoint"],
        "sae": ["patient_id", "event_date", "outcome", "causality"],
    }

    def classify(
        self,
        doc_id: str,
        submission_type: SubmissionType,
        text: str,
        metadata: dict | None = None,
        duplicate_candidates: list[dict] | None = None,
    ) -> Scorecard:
        metadata = metadata or {}
        duplicate_candidates = duplicate_candidates or []
        missing = self._missing_required(submission_type, text, metadata)
        completeness = round((len(self.REQUIRED_FIELDS[submission_type]) - len(missing)) / len(self.REQUIRED_FIELDS[submission_type]), 3)
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

    def _missing_required(self, submission_type: str, text: str, metadata: dict) -> list[str]:
        lower = text.lower()
        missing = []
        for field in self.REQUIRED_FIELDS[submission_type]:
            readable = field.replace("_", " ")
            if not metadata.get(field) and readable not in lower:
                missing.append(field)
        return missing

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
            sae_severity, severity_score = self._sae_severity(lower)
            risk_score = max(risk_score, severity_score)
            labels.append(f"sae:{sae_severity}")

        if duplicate_candidates:
            risk_score = max(risk_score, 0.72)
            labels.append("possible_duplicate")

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
            confidence=0.86 if completeness >= 0.75 else 0.72,
        )

    def _sae_severity(self, lower_text: str) -> tuple[str, float]:
        if any(term in lower_text for term in ("death", "fatal", "expired")):
            return "death", 0.98
        if any(term in lower_text for term in ("life-threatening", "life threatening", "icu", "ventilator")):
            return "life_threatening", 0.92
        if any(term in lower_text for term in ("hospitalisation", "hospitalization", "admitted")):
            return "hospitalisation", 0.76
        if any(term in lower_text for term in ("disability", "congenital anomaly")):
            return "disability", 0.72
        return "other", 0.45

    def _category(self, submission_type: str) -> str:
        return {
            "drug": "Drug regulatory submission",
            "medical_device": "Medical device regulatory submission",
            "clinical_trial": "Clinical trial submission",
            "sae": "Serious adverse event report",
        }[submission_type]
