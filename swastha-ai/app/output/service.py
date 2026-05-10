"""Layer 6 output service — real DB queries, Kafka events, PDF generation.

All methods query PostgreSQL directly via async SQLAlchemy.
No mock data anywhere.
"""

from __future__ import annotations

import hashlib
import io
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditLog, ComplianceAssessment, Submission, SubmissionStatusEnum
from app.output.schemas import (
    DashboardMetrics,
    ReviewerActionRequest,
    SubmissionDetail,
    SubmissionQueueResponse,
    SubmissionSummary,
)

logger = logging.getLogger(__name__)

# Kafka topic for reviewer decisions
_TOPIC_DOCUMENTS_REVIEWED = "swastha.documents.reviewed"


class OutputService:
    def __init__(self, db: AsyncSession, minio: Any, producer: Any) -> None:
        self._db = db
        self._minio = minio
        self._producer = producer

    # ── Dashboard metrics ──────────────────────────────────────────────────────

    async def get_dashboard_metrics(self) -> DashboardMetrics:
        """Aggregate real metrics from the submissions and compliance_assessments tables."""

        # Total processed (status = processed or rejected — 'reviewed' is not in DB enum)
        total_q = await self._db.execute(
            text("SELECT COUNT(*) FROM submissions WHERE status IN ('processed', 'rejected')")
        )
        total_processed: int = total_q.scalar_one() or 0

        # Pending review (status = ingested or queued or preprocessing)
        pending_q = await self._db.execute(
            text("SELECT COUNT(*) FROM submissions WHERE status IN ('ingested', 'queued', 'preprocessing')")
        )
        pending_review: int = pending_q.scalar_one() or 0

        # Critical SAEs — classified as critical SAE in compliance assessments
        critical_q = await self._db.execute(
            text("""
                SELECT COUNT(DISTINCT doc_id)
                FROM compliance_assessments
                WHERE human_review_required = true
                  AND source_event LIKE '%sae%'
                  AND assessed_at > NOW() - INTERVAL '30 days'
            """)
        )
        critical_saes: int = critical_q.scalar_one() or 0

        # Auto-approved (processed without human review flag)
        auto_q = await self._db.execute(
            text("""
                SELECT COUNT(*)
                FROM submissions s
                WHERE s.status = 'processed'
                  AND NOT EXISTS (
                    SELECT 1 FROM compliance_assessments ca
                    WHERE ca.doc_id = s.doc_id AND ca.human_review_required = true
                  )
            """)
        )
        auto_approved: int = auto_q.scalar_one() or 0

        # Throughput by day (last 7 days)
        throughput_q = await self._db.execute(
            text("""
                SELECT
                    TO_CHAR(created_at AT TIME ZONE 'Asia/Kolkata', 'Dy') AS day_name,
                    COUNT(*) AS submissions
                FROM submissions
                WHERE created_at > NOW() - INTERVAL '7 days'
                GROUP BY DATE(created_at AT TIME ZONE 'Asia/Kolkata'), day_name
                ORDER BY DATE(created_at AT TIME ZONE 'Asia/Kolkata')
            """)
        )
        throughput = [
            {"name": row.day_name, "submissions": row.submissions}
            for row in throughput_q.fetchall()
        ]

        # Priority breakdown by submission type
        priority_q = await self._db.execute(
            text("""
                SELECT
                    s.submission_type,
                    COUNT(*) FILTER (WHERE ca.blocked = true) AS critical,
                    COUNT(*) AS total
                FROM submissions s
                LEFT JOIN compliance_assessments ca ON ca.doc_id = s.doc_id
                WHERE s.created_at > NOW() - INTERVAL '30 days'
                GROUP BY s.submission_type
            """)
        )
        priority_breakdown = [
            {
                "name": row.submission_type.replace("_", " ").title(),
                "critical": row.critical or 0,
                "processed": row.total,
            }
            for row in priority_q.fetchall()
        ]

        return DashboardMetrics(
            total_processed=total_processed,
            pending_review=pending_review,
            critical_saes=critical_saes,
            auto_approved=auto_approved,
            throughput=throughput or [{"name": "No data", "submissions": 0}],
            priority_breakdown=priority_breakdown or [{"name": "No data", "critical": 0, "processed": 0}],
        )

    # ── Submission queue ───────────────────────────────────────────────────────

    async def get_queue(self, page: int = 1, size: int = 20, status_filter: str | None = None) -> SubmissionQueueResponse:
        """Return paginated real submissions with their compliance status."""
        offset = (page - 1) * size

        where_clause = ""
        if status_filter:
            where_clause = f"WHERE s.status = '{status_filter}'"

        rows_q = await self._db.execute(
            text(f"""
                SELECT
                    s.doc_id,
                    s.submission_type,
                    s.original_filename,
                    s.status,
                    s.portal_source,
                    s.created_at,
                    s.file_size_bytes,
                    ca.decision AS compliance_decision,
                    ca.human_review_required,
                    ca.blocked,
                    xai.confidence AS ai_confidence,
                    xai.model_id
                FROM submissions s
                LEFT JOIN LATERAL (
                    SELECT decision, human_review_required, blocked
                    FROM compliance_assessments
                    WHERE doc_id = s.doc_id
                    ORDER BY assessed_at DESC
                    LIMIT 1
                ) ca ON true
                LEFT JOIN LATERAL (
                    SELECT confidence, model_id
                    FROM xai_decision_log
                    WHERE doc_id = s.doc_id
                    ORDER BY created_at DESC
                    LIMIT 1
                ) xai ON true
                {where_clause}
                ORDER BY s.created_at DESC
                LIMIT :size OFFSET :offset
            """),
            {"size": size, "offset": offset},
        )

        total_q = await self._db.execute(
            text(f"SELECT COUNT(*) FROM submissions s {where_clause}")
        )
        total: int = total_q.scalar_one() or 0

        items = []
        for row in rows_q.fetchall():
            # Map DB state to display state
            if row.blocked:
                display_status = "blocked"
            elif row.human_review_required:
                display_status = "review_required"
            elif row.status in ("processed", "reviewed"):
                display_status = "processed"
            else:
                display_status = str(row.status)

            # Map compliance decision to priority
            if row.blocked:
                priority = "critical"
            elif row.human_review_required:
                priority = "high"
            elif row.compliance_decision == "pass":
                priority = "low"
            else:
                priority = "medium"

            age = _human_age(row.created_at)
            items.append(
                SubmissionSummary(
                    id=row.doc_id,
                    applicant=row.original_filename.split(".")[0].replace("_", " ").title()[:40],
                    type=str(row.submission_type),
                    received=age,
                    status=display_status,
                    priority=priority,
                    score=round(float(row.ai_confidence or 0.0), 2),
                )
            )

        return SubmissionQueueResponse(items=items, total=total, page=page, size=size)

    # ── Submission detail ──────────────────────────────────────────────────────

    async def get_submission_detail(self, doc_id: str) -> SubmissionDetail | None:
        """Return full AI analysis for a single submission."""
        row_q = await self._db.execute(
            text("""
                SELECT
                    s.doc_id, s.submission_type, s.original_filename,
                    s.status, s.checksum_sha256, s.portal_source,
                    s.created_at, s.file_size_bytes,
                    s.metadata
                FROM submissions s
                WHERE s.doc_id = :doc_id
                LIMIT 1
            """),
            {"doc_id": doc_id},
        )
        sub = row_q.first()
        if not sub:
            return None

        # Latest AI summary
        summary_q = await self._db.execute(
            text("""
                SELECT action_detail
                FROM audit_log
                WHERE doc_id = :doc_id AND event_type = 'ai_core.summarisation'
                ORDER BY created_at DESC LIMIT 1
            """),
            {"doc_id": doc_id},
        )
        summary_row = summary_q.first()
        summary_data = _unpack_json(summary_row.action_detail if summary_row else None)

        # Latest classification
        class_q = await self._db.execute(
            text("""
                SELECT action_detail
                FROM audit_log
                WHERE doc_id = :doc_id AND event_type = 'ai_core.classification'
                ORDER BY created_at DESC LIMIT 1
            """),
            {"doc_id": doc_id},
        )
        class_row = class_q.first()
        class_data = _unpack_json(class_row.action_detail if class_row else None)

        # Compliance findings
        comp_q = await self._db.execute(
            text("""
                SELECT findings, decision, human_review_required, blocked, confidence, assessed_at
                FROM compliance_assessments
                WHERE doc_id = :doc_id
                ORDER BY assessed_at DESC LIMIT 5
            """),
            {"doc_id": doc_id},
        )
        compliance_rows = comp_q.fetchall()

        # Anonymisation entities
        anon_q = await self._db.execute(
            text("""
                SELECT action_detail
                FROM audit_log
                WHERE doc_id = :doc_id AND event_type = 'ai_core.anonymisation'
                ORDER BY created_at DESC LIMIT 1
            """),
            {"doc_id": doc_id},
        )
        anon_row = anon_q.first()
        anon_data = _unpack_json(anon_row.action_detail if anon_row else None)

        # XAI log
        xai_q = await self._db.execute(
            text("""
                SELECT module_name, model_id, model_version, confidence, decision_summary, rationale, created_at
                FROM xai_decision_log
                WHERE doc_id = :doc_id
                ORDER BY created_at DESC LIMIT 10
            """),
            {"doc_id": doc_id},
        )
        xai_rows = xai_q.fetchall()

        return SubmissionDetail(
            id=sub.doc_id,
            submission_type=str(sub.submission_type),
            filename=sub.original_filename,
            status=str(sub.status),
            checksum_sha256=sub.checksum_sha256,
            portal_source=str(sub.portal_source),
            received_at=sub.created_at.isoformat() if sub.created_at else "",
            file_size_bytes=sub.file_size_bytes,
            executive_summary=summary_data.get("executive_summary", "") if summary_data else "",
            key_findings=summary_data.get("key_findings", []) if summary_data else [],
            risks=summary_data.get("risks", []) if summary_data else [],
            missing_information=summary_data.get("missing_information", []) if summary_data else [],
            recommended_next_steps=summary_data.get("recommended_next_steps", []) if summary_data else [],
            summary_model_id=summary_data.get("model_id", "") if summary_data else "",
            summary_confidence=float(summary_data.get("confidence", 0.0)) if summary_data else 0.0,
            classification=_unpack_json(class_data.get("classification") if class_data else None) or {},
            completeness_score=float(class_data.get("completeness_score", 0.0)) if class_data else 0.0,
            missing_required_fields=class_data.get("missing_required_fields", []) if class_data else [],
            duplicate_candidates=class_data.get("duplicate_candidates", []) if class_data else [],
            pii_entities_removed=int(anon_data.get("entity_count", 0)) if anon_data else 0,
            anonymisation_method=str(anon_data.get("method", "")) if anon_data else "",
            compliance_findings=[
                {
                    "findings": _unpack_json(r.findings) if r.findings else [],
                    "decision": r.decision,
                    "human_review_required": r.human_review_required,
                    "blocked": r.blocked,
                    "confidence": float(r.confidence),
                    "assessed_at": r.assessed_at.isoformat() if r.assessed_at else "",
                }
                for r in compliance_rows
            ],
            xai_log=[
                {
                    "module": r.module_name,
                    "model_id": r.model_id,
                    "model_version": r.model_version,
                    "confidence": float(r.confidence),
                    "summary": r.decision_summary,
                    "rationale": _unpack_json(r.rationale) if r.rationale else [],
                    "created_at": r.created_at.isoformat() if r.created_at else "",
                }
                for r in xai_rows
            ],
        )

    # ── Reviewer action ────────────────────────────────────────────────────────

    async def process_reviewer_action(
        self,
        doc_id: str,
        action: str,
        notes: str,
        override_reason: str | None,
        user: dict,
    ) -> bool:
        """
        Persist a reviewer decision and publish a Kafka event.

        - Updates submission status to 'reviewed' or 'rejected'
        - Writes an immutable audit log entry
        - Publishes `swastha.documents.reviewed` event to Kafka
        """
        username = user.get("preferred_username", "unknown")
        user_id = user.get("sub", str(uuid.uuid4()))

        new_status = "reviewed" if action == "approve" else "rejected"

        # 1. Update submission status
        await self._db.execute(
            text("""
                UPDATE submissions
                SET status = :status, updated_at = NOW()
                WHERE doc_id = :doc_id
            """),
            {"status": new_status, "doc_id": doc_id},
        )

        # 2. Write audit log entry (append-only — UPDATE/DELETE revoked at DB level)
        entry_hash = hashlib.sha256(
            f"reviewer:{action}:{doc_id}:{user_id}:{time.time_ns()}".encode()
        ).hexdigest()
        await self._db.execute(
            text("""
                INSERT INTO audit_log
                (event_type, doc_id, actor_id, actor_email, action_detail, outcome, entry_hash, prev_hash)
                VALUES (:event_type, :doc_id, :actor_id, :actor_email, :detail, :outcome, :entry_hash, 'reviewer-action')
            """),
            {
                "event_type": f"reviewer.{action}",
                "doc_id": doc_id,
                "actor_id": user_id,
                "actor_email": user.get("email", ""),
                "detail": {
                    "action": action,
                    "notes": notes,
                    "override_reason": override_reason,
                    "reviewer": username,
                },
                "outcome": "success",
                "entry_hash": entry_hash,
            },
        )

        await self._db.commit()
        logger.info(
            "Reviewer action persisted",
            extra={"doc_id": doc_id, "action": action, "reviewer": username},
        )

        # 3. Publish Kafka event
        try:
            await self._producer.publish(
                _TOPIC_DOCUMENTS_REVIEWED,
                {
                    "event_version": "1.0",
                    "doc_id": doc_id,
                    "action": action,
                    "reviewer_id": user_id,
                    "reviewer_name": username,
                    "notes": notes,
                    "override_reason": override_reason,
                    "timestamp": int(time.time() * 1000),
                },
                key=doc_id,
            )
        except Exception as exc:
            logger.warning(
                "Kafka publish failed for reviewer action — DB write succeeded",
                extra={"doc_id": doc_id, "error": str(exc)},
            )

        return True

    # ── PDF report generation ─────────────────────────────────────────────────

    async def generate_pdf_report(self, doc_id: str) -> bytes:
        """Generate a CDSCO-format PDF for a submission using WeasyPrint."""
        detail = await self.get_submission_detail(doc_id)
        if not detail:
            raise ValueError(f"Submission {doc_id} not found")

        html = _render_report_html(detail)
        try:
            from weasyprint import HTML  # type: ignore[import]
            pdf_bytes: bytes = HTML(string=html, base_url=None).write_pdf()
            return pdf_bytes
        except ImportError:
            logger.error("WeasyPrint not installed — PDF generation unavailable")
            raise RuntimeError("PDF generation requires WeasyPrint. Install it with: pip install weasyprint")


# ── Helpers ────────────────────────────────────────────────────────────────────


def _human_age(dt: datetime | None) -> str:
    """Convert a datetime to a human-readable age string."""
    if not dt:
        return "Unknown"
    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = now - dt
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return f"{seconds} secs ago"
    if seconds < 3600:
        return f"{seconds // 60} mins ago"
    if seconds < 86400:
        return f"{seconds // 3600} hrs ago"
    return f"{delta.days} days ago"


def _unpack_json(value: Any) -> Any:
    """Safely unpack a JSON blob that may already be a dict/list or a JSON string."""
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        import json
        try:
            return json.loads(value)
        except Exception:
            return value
    return value


def _render_report_html(detail: "SubmissionDetail") -> str:
    """Render a CDSCO-format HTML report for WeasyPrint conversion."""
    findings_html = ""
    for comp in detail.compliance_findings:
        for finding in comp.get("findings", []):
            sev = finding.get("severity", "info")
            sev_color = {"critical": "#dc2626", "warning": "#d97706", "info": "#2563eb"}.get(sev, "#6b7280")
            findings_html += f"""
            <div style="border-left: 4px solid {sev_color}; padding: 8px 12px; margin: 6px 0; background: #f9fafb;">
                <strong style="color:{sev_color}">[{finding.get('rule_id', '')}]</strong> {finding.get('message', '')}
                <br><small style="color:#6b7280">Framework: {finding.get('framework', '')}</small>
            </div>"""

    key_findings = "".join(f"<li>{f}</li>" for f in detail.key_findings)
    risks = "".join(f"<li>{r}</li>" for r in detail.risks)
    steps = "".join(f"<li>{s}</li>" for s in detail.recommended_next_steps)
    missing = "".join(f"<li>{m}</li>" for m in detail.missing_information)
    xai_rows = "".join(
        f"<tr><td>{x.get('module', '')}</td><td>{x.get('model_id', '')}</td>"
        f"<td>{x.get('confidence', 0):.2f}</td><td>{x.get('summary', '')}</td></tr>"
        for x in detail.xai_log
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>CDSCO SwasthaAI — Regulatory Review Report {detail.id}</title>
<style>
  body {{ font-family: 'Arial', sans-serif; margin: 40px; color: #111; font-size: 13px; }}
  h1 {{ color: #1e3a5f; border-bottom: 2px solid #1e3a5f; padding-bottom: 8px; }}
  h2 {{ color: #1e3a5f; margin-top: 24px; font-size: 15px; }}
  table {{ width: 100%; border-collapse: collapse; margin-top: 8px; }}
  th, td {{ border: 1px solid #d1d5db; padding: 6px 10px; text-align: left; font-size: 12px; }}
  th {{ background: #f3f4f6; font-weight: bold; }}
  .meta {{ display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }}
  .meta-item {{ background: #f9fafb; padding: 8px; border-radius: 4px; }}
  .meta-label {{ font-size: 11px; color: #6b7280; }}
  .meta-value {{ font-weight: bold; }}
  .badge {{ display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: bold; }}
  .badge-high {{ background: #fef3c7; color: #92400e; }}
  .badge-critical {{ background: #fee2e2; color: #991b1b; }}
  .footer {{ margin-top: 40px; padding-top: 12px; border-top: 1px solid #e5e7eb; font-size: 11px; color: #6b7280; }}
</style>
</head>
<body>
<h1>🇮🇳 CDSCO SwasthaAI — AI-Assisted Regulatory Review Report</h1>

<div class="meta">
  <div class="meta-item"><div class="meta-label">Document ID</div><div class="meta-value">{detail.id}</div></div>
  <div class="meta-item"><div class="meta-label">Submission Type</div><div class="meta-value">{detail.submission_type.replace('_', ' ').title()}</div></div>
  <div class="meta-item"><div class="meta-label">Filename</div><div class="meta-value">{detail.filename}</div></div>
  <div class="meta-item"><div class="meta-label">Portal Source</div><div class="meta-value">{detail.portal_source}</div></div>
  <div class="meta-item"><div class="meta-label">Received</div><div class="meta-value">{detail.received_at}</div></div>
  <div class="meta-item"><div class="meta-label">SHA-256 Checksum</div><div class="meta-value" style="font-size:10px;font-family:monospace">{detail.checksum_sha256}</div></div>
  <div class="meta-item"><div class="meta-label">AI Model</div><div class="meta-value">{detail.summary_model_id}</div></div>
  <div class="meta-item"><div class="meta-label">AI Confidence</div><div class="meta-value">{detail.summary_confidence:.1%}</div></div>
</div>

<h2>📋 Executive Summary</h2>
<p>{detail.executive_summary or 'No summary available.'}</p>

<h2>🔍 Key Findings</h2>
<ul>{key_findings}</ul>

<h2>⚠️ Identified Risks</h2>
<ul>{risks}</ul>

<h2>❓ Missing Information</h2>
<ul>{missing or '<li>None identified</li>'}</ul>

<h2>✅ Recommended Next Steps</h2>
<ul>{steps}</ul>

<h2>🛡️ Compliance & Governance Findings</h2>
{findings_html or '<p>No compliance findings.</p>'}

<h2>🤖 XAI Decision Log</h2>
<table>
  <thead><tr><th>Module</th><th>Model ID</th><th>Confidence</th><th>Decision Summary</th></tr></thead>
  <tbody>{xai_rows or '<tr><td colspan="4">No XAI records.</td></tr>'}</tbody>
</table>

<div class="footer">
  Generated by SwasthaAI v1.0 | CDSCO-IndiaAI Health Innovation Hackathon |
  Report generated at: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')} |
  This report is AI-assisted and requires human reviewer verification before regulatory action.
</div>
</body>
</html>"""
