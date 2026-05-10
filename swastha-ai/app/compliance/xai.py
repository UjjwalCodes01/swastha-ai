"""Explainable AI decision logging."""

from __future__ import annotations

from app.compliance.schemas import XAIDecision


class XAILogger:
    """Builds compact explainability records for AI outputs."""

    def from_payload(self, topic: str, payload: dict) -> XAIDecision:
        module_name = self._module_from_topic(topic)
        model_id = str(payload.get("model_id") or payload.get("anonymisation_method") or "deterministic")
        model_version = str(payload.get("model_version") or "1.0.0")
        confidence = float(payload.get("confidence") or payload.get("classification", {}).get("confidence") or 0.0)
        rationale = self._rationale(topic, payload)
        return XAIDecision(
            doc_id=str(payload.get("doc_id", "")),
            module_name=module_name,
            model_id=model_id,
            model_version=model_version,
            confidence=confidence,
            decision_summary=self._summary(topic, payload),
            input_refs={"topic": topic},
            output_refs=self._output_refs(payload),
            rationale=rationale,
        )

    def _module_from_topic(self, topic: str) -> str:
        if "anonymised" in topic:
            return "anonymisation"
        if "summarised" in topic:
            return "summarisation"
        if "classified" in topic:
            return "classification"
        if "reports.generated" in topic:
            return "comparison_reporting"
        return "ai_core"

    def _summary(self, topic: str, payload: dict) -> str:
        if "classified" in topic:
            c = payload.get("classification", {})
            return f"{c.get('category', 'classification')} priority={c.get('priority')} review={c.get('requires_review')}"
        if "summarised" in topic:
            return f"Summary generated with {payload.get('summary_word_count', 0)} words."
        if "anonymised" in topic:
            return f"Removed {payload.get('pii_entities_removed', 0)} PII/PHI entities."
        if "reports.generated" in topic:
            return f"Generated {payload.get('report_type', 'report')} report."
        return "AI output processed."

    def _output_refs(self, payload: dict) -> dict[str, str]:
        refs = {}
        for key in ("anonymised_storage_path", "summary_storage_path", "report_storage_path"):
            if payload.get(key):
                refs[key] = str(payload[key])
        return refs

    def _rationale(self, topic: str, payload: dict) -> list[str]:
        if "classified" in topic:
            c = payload.get("classification", {})
            return [
                f"Risk score: {c.get('risk_score')}",
                f"Labels: {', '.join(c.get('labels', []))}",
            ]
        if "summarised" in topic:
            return [f"Key findings extracted: {len(payload.get('key_findings', []))}"]
        if "anonymised" in topic:
            return [f"Anonymisation method: {payload.get('anonymisation_method')}"]
        return ["Deterministic governance check applied."]
