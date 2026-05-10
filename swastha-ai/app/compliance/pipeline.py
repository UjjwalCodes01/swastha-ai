"""Layer 4 compliance and governance orchestration."""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text

from app.ai_core.storage import load_json
from app.compliance.dpdp import DPDPChecker
from app.compliance.icmr import ICMRChecker
from app.compliance.ndhm import NDHMChecker
from app.compliance.pii_leak_detector import PIILeakDetector
from app.compliance.schemas import ComplianceDecision, ComplianceFinding
from app.compliance.xai import XAILogger


class CompliancePipeline:
    """Runs DPDP, ICMR, NDHM, PII-leak, and XAI checks."""

    frameworks = ["DPDP Act 2023", "ICMR/Clinical Trial Governance", "NDHM Health Data Standards", "XAI"]

    def __init__(self, minio_client: Any, producer: Any, db_session_factory: Any) -> None:
        self.minio = minio_client
        self.producer = producer
        self.db_session_factory = db_session_factory
        self.pii = PIILeakDetector()
        self.dpdp = DPDPChecker()
        self.icmr = ICMRChecker()
        self.ndhm = NDHMChecker()
        self.xai = XAILogger()

    async def assess(self, topic: str, payload: dict[str, Any]) -> ComplianceDecision:
        findings: list[ComplianceFinding] = []
        findings.extend(self.ndhm.check_event(payload))

        if topic == "documents.anonymised":
            artefact = await load_json(self.minio, payload["anonymised_storage_path"])
            leak_findings = self.pii.detect(artefact.get("anonymised_text", ""))
            findings.extend(self.dpdp.check_anonymised(artefact, leak_findings))
        elif topic == "documents.summarised":
            summary = await load_json(self.minio, payload["summary_storage_path"])
            findings.extend(self.dpdp.check_summary(summary))
        elif topic == "documents.classified":
            findings.extend(self.icmr.check_classification(payload))
        elif topic == "reports.generated":
            findings.extend(self._check_report(payload))

        xai_record = self.xai.from_payload(topic, payload)
        decision = self._decision(payload.get("doc_id", ""), topic, findings)
        await self._persist_decision(decision, xai_record.model_dump())
        if decision.blocked or decision.human_review_required:
            await self._publish_notification(decision)
        return decision

    def _check_report(self, payload: dict) -> list[ComplianceFinding]:
        findings = []
        if payload.get("report_type") in {"comparison", "compliance_check"} and not payload.get("report_storage_path"):
            findings.append(
                ComplianceFinding(
                    rule_id="GOV-REPORT-LINEAGE",
                    framework="XAI",
                    severity="critical",
                    message="Generated report is missing storage lineage.",
                    evidence={"report_id": str(payload.get("report_id", ""))},
                )
            )
        return findings

    def _decision(self, doc_id: str, topic: str, findings: list[ComplianceFinding]) -> ComplianceDecision:
        blocked = any(f.severity == "critical" and f.rule_id.startswith("DPDP") for f in findings)
        review = blocked or any(f.severity in {"critical", "warning"} for f in findings)
        if blocked:
            decision = "blocked"
        elif review:
            decision = "review_required"
        else:
            decision = "pass"
        confidence = 0.98 if findings else 0.93
        return ComplianceDecision(
            doc_id=doc_id,
            source_event=topic,
            decision=decision,
            frameworks_checked=self.frameworks,
            findings=findings,
            human_review_required=review,
            blocked=blocked,
            confidence=confidence,
        )

    async def _persist_decision(self, decision: ComplianceDecision, xai_record: dict) -> None:
        from app.db.models import ComplianceAssessment, XAIDecisionLog
        async with self.db_session_factory() as session:
            assessment = ComplianceAssessment(
                doc_id=decision.doc_id,
                source_event=decision.source_event,
                decision=decision.decision,
                frameworks_checked=decision.frameworks_checked,
                findings=[finding.model_dump() for finding in decision.findings],
                human_review_required=decision.human_review_required,
                blocked=decision.blocked,
                confidence=decision.confidence,
                assessed_at=datetime.now(timezone.utc),
            )
            session.add(assessment)

            xai_log = XAIDecisionLog(
                doc_id=xai_record["doc_id"],
                module_name=xai_record["module_name"],
                model_id=xai_record["model_id"],
                model_version=xai_record["model_version"],
                confidence=xai_record["confidence"],
                decision_summary=xai_record["decision_summary"],
                input_refs=xai_record["input_refs"],
                output_refs=xai_record["output_refs"],
                rationale=xai_record["rationale"],
                created_at=datetime.now(timezone.utc),
            )
            session.add(xai_log)
            
            await session.commit()

    async def _publish_notification(self, decision: ComplianceDecision) -> None:
        severity = "CRITICAL" if decision.blocked else "WARNING"
        await self.producer.publish(
            "notifications.events",
            {
                "event_id": str(uuid.uuid4()),
                "event_version": "1.0",
                "source_layer": "layer4",
                "severity": severity,
                "alert_type": "compliance_blocked" if decision.blocked else "human_review_required",
                "title": f"Compliance decision: {decision.decision}",
                "message": self._notification_message(decision),
                "doc_id": decision.doc_id or None,
                "affected_topic": decision.source_event,
                "affected_group": "swastha-compliance",
                "metric_value": float(len(decision.findings)),
                "metric_threshold": 0.0,
                "extra": {finding.rule_id: finding.severity for finding in decision.findings[:10]},
                "timestamp": int(time.time() * 1000),
            },
            key=decision.doc_id,
        )

    def _notification_message(self, decision: ComplianceDecision) -> str:
        first = decision.findings[0].message if decision.findings else "No finding detail available."
        return f"{decision.source_event} for {decision.doc_id} requires governance action. First finding: {first}"
