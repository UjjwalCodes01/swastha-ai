"""NDHM-style health data protocol checks."""

from __future__ import annotations

from app.compliance.schemas import ComplianceFinding


class NDHMChecker:
    """Checks health-data interoperability and traceability metadata."""

    def check_event(self, payload: dict) -> list[ComplianceFinding]:
        findings: list[ComplianceFinding] = []
        if not payload.get("doc_id"):
            findings.append(
                ComplianceFinding(
                    rule_id="NDHM-DOC-ID-MISSING",
                    framework="NDHM Health Data Standards",
                    severity="critical",
                    message="AI output is missing the canonical document identifier.",
                    evidence={},
                )
            )
        if not payload.get("submission_type"):
            findings.append(
                ComplianceFinding(
                    rule_id="NDHM-SUBMISSION-TYPE-MISSING",
                    framework="NDHM Health Data Standards",
                    severity="warning",
                    message="AI output is missing submission type metadata.",
                    evidence={"doc_id": str(payload.get("doc_id", ""))},
                )
            )
        return findings
