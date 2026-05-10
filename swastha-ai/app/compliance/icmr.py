"""ICMR and clinical governance checks."""

from __future__ import annotations

from app.compliance.schemas import ComplianceFinding


class ICMRChecker:
    """Clinical safety rules that must not be delegated only to AI."""

    def check_classification(self, payload: dict) -> list[ComplianceFinding]:
        classification = payload.get("classification", {})
        findings: list[ComplianceFinding] = []
        sae_severity = classification.get("sae_severity")
        priority = classification.get("priority")
        requires_review = bool(classification.get("requires_review"))

        if sae_severity in {"death", "life_threatening"} and not requires_review:
            findings.append(
                ComplianceFinding(
                    rule_id="ICMR-SAE-MANDATORY-REVIEW",
                    framework="ICMR/Clinical Trial Governance",
                    severity="critical",
                    message="Death/life-threatening SAE must always be routed for human review.",
                    evidence={"sae_severity": str(sae_severity)},
                )
            )
        if priority in {"critical", "high"} and not requires_review:
            findings.append(
                ComplianceFinding(
                    rule_id="ICMR-HIGH-RISK-REVIEW",
                    framework="ICMR/Clinical Trial Governance",
                    severity="warning",
                    message="High-priority AI classification should require reviewer validation.",
                    evidence={"priority": str(priority)},
                )
            )
        return findings
