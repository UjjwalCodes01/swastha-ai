"""Post-anonymisation PII leak detection."""

from __future__ import annotations

from app.ai_core.anonymisation import Anonymiser
from app.compliance.schemas import ComplianceFinding


class PIILeakDetector:
    """Reuses Layer 3 recognisers to verify anonymised outputs are clean."""

    def __init__(self) -> None:
        self._anonymiser = Anonymiser()

    def detect(self, text: str) -> list[ComplianceFinding]:
        result = self._anonymiser.anonymise("compliance-scan", text)
        findings = []
        for entity in result.entities:
            findings.append(
                ComplianceFinding(
                    rule_id="DPDP-PII-LEAK",
                    framework="DPDP Act 2023",
                    severity="critical",
                    message=f"Potential direct identifier remained after anonymisation: {entity.entity_type}",
                    evidence={"entity_type": entity.entity_type, "value": entity.original[:80]},
                )
            )
        return findings
