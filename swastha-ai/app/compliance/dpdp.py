"""DPDP Act 2023 compliance checks."""

from __future__ import annotations

from app.compliance.schemas import ComplianceFinding


class DPDPChecker:
    """Checks data-minimisation and privacy constraints for AI outputs."""

    def check_anonymised(self, artefact: dict, leak_findings: list[ComplianceFinding]) -> list[ComplianceFinding]:
        findings = list(leak_findings)
        if not artefact.get("entities") and len(artefact.get("anonymised_text", "")) > 500:
            findings.append(
                ComplianceFinding(
                    rule_id="DPDP-AUDIT-ENTITY-MAP",
                    framework="DPDP Act 2023",
                    severity="warning",
                    message="No anonymisation entity map was stored for a non-trivial document.",
                    evidence={"doc_id": str(artefact.get("doc_id", ""))},
                )
            )
        if not artefact.get("source_processed_path"):
            findings.append(
                ComplianceFinding(
                    rule_id="DPDP-LINEAGE-MISSING",
                    framework="DPDP Act 2023",
                    severity="warning",
                    message="Anonymised artefact is missing source lineage to the processed document.",
                    evidence={},
                )
            )
        return findings

    def check_summary(self, summary: dict) -> list[ComplianceFinding]:
        findings: list[ComplianceFinding] = []
        text = " ".join(
            str(summary.get(field, ""))
            for field in ("executive_summary", "key_findings", "risks", "recommended_next_steps")
        )
        if any(marker in text for marker in ("Aadhaar", "PAN", "@")):
            findings.append(
                ComplianceFinding(
                    rule_id="DPDP-SUMMARY-IDENTIFIER",
                    framework="DPDP Act 2023",
                    severity="critical",
                    message="Summary appears to contain direct personal identifiers.",
                    evidence={"snippet": text[:120]},
                )
            )
        return findings
