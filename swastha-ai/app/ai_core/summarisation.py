"""Structured regulatory summarisation for Layer 3."""

from __future__ import annotations

import re

from app.ai_core.schemas import RegulatorySummary, SubmissionType


class Summariser:
    """Creates schema-valid summaries with an offline extractive fallback."""

    model_id = "offline-extractive-regulatory-summariser"
    model_version = "1.0.0"

    def summarise(self, doc_id: str, submission_type: SubmissionType, text: str, metadata: dict | None = None) -> RegulatorySummary:
        metadata = metadata or {}
        sentences = self._sentences(text)
        selected = sentences[:5] or ["No readable document content was available for summarisation."]
        key_findings = self._key_findings(sentences, submission_type)
        risks = self._risks(text, submission_type)
        missing = self._missing_information(text, submission_type, metadata)
        next_steps = self._next_steps(submission_type, missing, risks)
        summary_text = " ".join(selected)

        return RegulatorySummary(
            doc_id=doc_id,
            submission_type=submission_type,
            executive_summary=summary_text,
            key_findings=key_findings[:5],
            risks=risks,
            missing_information=missing,
            recommended_next_steps=next_steps,
            word_count=len(summary_text.split()),
            confidence=self._confidence(text, key_findings, missing),
            model_id=self.model_id,
            model_version=self.model_version,
            prompt_tokens=max(1, len(text.split())),
            completion_tokens=max(1, len(summary_text.split())),
        )

    def _sentences(self, text: str) -> list[str]:
        cleaned = re.sub(r"\s+", " ", text).strip()
        if not cleaned:
            return []
        return [s.strip() for s in re.split(r"(?<=[.!?])\s+", cleaned) if len(s.strip()) > 20]

    def _key_findings(self, sentences: list[str], submission_type: str) -> list[str]:
        keywords = {
            "drug": ("indication", "dosage", "contraindication", "stability", "manufacturing", "safety"),
            "medical_device": ("device", "risk", "sterile", "performance", "clinical", "manufacturing"),
            "clinical_trial": ("phase", "endpoint", "protocol", "eligibility", "adverse", "efficacy"),
            "sae": ("death", "life-threatening", "hospital", "serious", "causality", "adverse"),
        }.get(submission_type, ())
        findings = [s for s in sentences if any(k in s.lower() for k in keywords)]
        return findings[:5] or sentences[:3] or ["No key findings could be extracted from the available text."]

    def _risks(self, text: str, submission_type: str) -> list[str]:
        lower = text.lower()
        risks: list[str] = []
        risk_terms = ["death", "life-threatening", "contraindication", "serious adverse", "missing", "deviation"]
        for term in risk_terms:
            if term in lower:
                risks.append(f"Potential regulatory risk detected: {term}")
        if submission_type == "sae" and not risks:
            risks.append("SAE submission requires reviewer validation even when no critical keyword is present.")
        return risks or ["No explicit high-risk signal detected in the extracted text."]

    def _missing_information(self, text: str, submission_type: str, metadata: dict) -> list[str]:
        required = {
            "drug": ["applicant_name", "drug_name", "indication"],
            "medical_device": ["device_name", "risk_class", "manufacturer"],
            "clinical_trial": ["protocol_number", "phase", "sponsor"],
            "sae": ["patient_id", "event_date", "outcome"],
        }.get(submission_type, [])
        lower = text.lower()
        return [
            field
            for field in required
            if not metadata.get(field) and field.replace("_", " ") not in lower
        ]

    def _next_steps(self, submission_type: str, missing: list[str], risks: list[str]) -> list[str]:
        steps = []
        if missing:
            steps.append("Request applicant clarification for missing mandatory fields.")
        if risks and "No explicit" not in risks[0]:
            steps.append("Route to CDSCO reviewer for priority assessment.")
        if submission_type == "sae":
            steps.append("Verify SAE seriousness, causality, and mandatory reporting timeline.")
        return steps or ["Proceed to standard reviewer queue."]

    def _confidence(self, text: str, findings: list[str], missing: list[str]) -> float:
        if not text.strip():
            return 0.2
        score = 0.72 + min(len(findings), 5) * 0.04 - len(missing) * 0.03
        return round(max(0.3, min(0.95, score)), 3)
