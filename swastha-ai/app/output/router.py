from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Any, Optional
import io

from app.db.connection import get_db_session
from app.storage.minio_client import get_minio_client
from app.queue.kafka_producer import get_kafka_producer
from app.output.schemas import (
    DashboardMetrics,
    ReviewerActionRequest,
    SubmissionDetail,
    SubmissionQueueResponse,
)
from app.output.service import OutputService

router = APIRouter(prefix="/output", tags=["Output & Delivery"])


async def _build_service() -> tuple:
    """Return (session_factory, minio, producer) singletons."""
    from app.db.connection import get_session_factory
    from app.storage.minio_client import get_minio_client as _minio
    from app.queue.kafka_producer import get_kafka_producer as _kafka

    session_factory = get_session_factory()
    minio = await _minio()
    producer = await _kafka()
    return session_factory, minio, producer


@router.get("/dashboard/metrics", response_model=DashboardMetrics)
async def get_metrics(request: Request) -> DashboardMetrics:
    """Fetch real aggregated metrics from PostgreSQL for the reviewer dashboard."""
    session_factory, minio, producer = await _build_service()
    async with session_factory() as session:
        svc = OutputService(session, minio, producer)
        return await svc.get_dashboard_metrics()


@router.get("/submissions", response_model=SubmissionQueueResponse)
async def list_submissions(
    request: Request,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    status: Optional[str] = Query(None, description="Filter by status: ingested, processed, reviewed, rejected"),
) -> SubmissionQueueResponse:
    """List real submissions from the database with compliance status overlay."""
    session_factory, minio, producer = await _build_service()
    async with session_factory() as session:
        svc = OutputService(session, minio, producer)
        return await svc.get_queue(page=page, size=size, status_filter=status)


@router.get("/submissions/{doc_id}", response_model=SubmissionDetail)
async def get_submission(request: Request, doc_id: str) -> SubmissionDetail:
    """Return full AI analysis for a single submission — all real data from DB."""
    session_factory, minio, producer = await _build_service()
    async with session_factory() as session:
        svc = OutputService(session, minio, producer)
        detail = await svc.get_submission_detail(doc_id)
        if not detail:
            raise HTTPException(status_code=404, detail=f"Submission {doc_id} not found")
        return detail


@router.post("/submissions/{doc_id}/review")
async def review_submission(
    doc_id: str,
    action: ReviewerActionRequest,
    request: Request,
) -> dict:
    """
    Submit a reviewer decision (approve/reject).

    Writes to DB audit log, updates submission status, and publishes Kafka event.
    """
    if action.action not in ("approve", "reject"):
        raise HTTPException(status_code=400, detail="action must be 'approve' or 'reject'")

    user = getattr(request.state, "user", {
        "preferred_username": "reviewer1",
        "sub": "00000000-0000-0000-0000-000000000001",
        "email": "reviewer@cdsco.gov.in",
    })

    session_factory, minio, producer = await _build_service()
    async with session_factory() as session:
        svc = OutputService(session, minio, producer)
        success = await svc.process_reviewer_action(
            doc_id,
            action.action,
            action.notes or "",
            action.override_reason,
            user,
        )
        if not success:
            raise HTTPException(status_code=400, detail="Reviewer action could not be processed")

    verb = "approved" if action.action == "approve" else "rejected"
    return {
        "status": "success",
        "message": f"Document {doc_id} {verb} by {user.get('preferred_username')}",
        "doc_id": doc_id,
        "action": action.action,
    }


@router.get("/submissions/{doc_id}/report.pdf")
async def download_pdf_report(request: Request, doc_id: str) -> StreamingResponse:
    """
    Generate and download a simple PDF report for a submission.
    Uses built-in text rendering — no native dependencies required.
    """
    session_factory, minio, producer = await _build_service()
    async with session_factory() as session:
        svc = OutputService(session, minio, producer)
        detail = await svc.get_submission_detail(doc_id)
        if not detail:
            raise HTTPException(status_code=404, detail=f"Submission {doc_id} not found")

    # Generate a simple text-based PDF using only the stdlib
    pdf_bytes = _generate_simple_pdf(detail)

    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=swastha-ai-{doc_id}.pdf"},
    )


def _generate_simple_pdf(detail: SubmissionDetail) -> bytes:
    """
    Generate a minimal PDF report without any native library dependencies.
    Uses raw PDF spec — no WeasyPrint, no GTK, no reportlab needed.
    """
    lines = [
        "SwasthaAI Regulatory Review Report",
        "",
        f"Document ID: {detail.id}",
        f"Type: {detail.submission_type}",
        f"Filename: {detail.filename}",
        f"Status: {detail.status}",
        f"Portal Source: {detail.portal_source}",
        f"Received: {detail.received_at}",
        f"File Size: {detail.file_size_bytes:,} bytes",
        f"Checksum: {detail.checksum_sha256}",
        "",
        "--- Executive Summary ---",
        detail.executive_summary or "(No AI summary available yet)",
        "",
    ]

    if detail.key_findings:
        lines.append("--- Key Findings ---")
        for i, f in enumerate(detail.key_findings, 1):
            lines.append(f"  {i}. {f}")
        lines.append("")

    if detail.risks:
        lines.append("--- Risks ---")
        for i, r in enumerate(detail.risks, 1):
            lines.append(f"  {i}. {r}")
        lines.append("")

    if detail.compliance_findings:
        lines.append("--- Compliance Findings ---")
        for cf in detail.compliance_findings:
            lines.append(f"  Decision: {cf.get('decision', 'N/A')}")
            lines.append(f"  Human Review: {cf.get('human_review_required', False)}")
            lines.append(f"  Blocked: {cf.get('blocked', False)}")
            lines.append(f"  Confidence: {cf.get('confidence', 0)}")
            lines.append("")

    if detail.xai_log:
        lines.append("--- XAI Decision Log ---")
        for x in detail.xai_log:
            lines.append(f"  Module: {x.get('module', 'N/A')}")
            lines.append(f"  Model: {x.get('model_id', 'N/A')}")
            lines.append(f"  Confidence: {x.get('confidence', 0)}")
            lines.append(f"  Summary: {x.get('summary', 'N/A')}")
            lines.append("")

    lines.append("")
    lines.append("Generated by SwasthaAI - CDSCO Regulatory AI Platform")

    return _build_raw_pdf(lines)


def _build_raw_pdf(lines: list[str]) -> bytes:
    """Build a valid PDF file from a list of text lines using raw PDF spec."""
    page_text_lines = []
    y = 750
    for line in lines:
        safe = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        page_text_lines.append(f"BT /F1 10 Tf 50 {y} Td ({safe}) Tj ET")
        y -= 14
        if y < 50:
            break

    stream_content = "\n".join(page_text_lines)
    stream_bytes = stream_content.encode("latin-1", errors="replace")

    objects = []
    objects.append(b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj")
    objects.append(b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj")
    objects.append(
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>\nendobj"
    )
    stream_obj = (
        f"4 0 obj\n<< /Length {len(stream_bytes)} >>\nstream\n".encode("latin-1")
        + stream_bytes
        + b"\nendstream\nendobj"
    )
    objects.append(stream_obj)
    objects.append(
        b"5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj"
    )

    pdf = io.BytesIO()
    pdf.write(b"%PDF-1.4\n")

    offsets = []
    for obj in objects:
        offsets.append(pdf.tell())
        pdf.write(obj + b"\n")

    xref_pos = pdf.tell()
    pdf.write(b"xref\n")
    pdf.write(f"0 {len(objects) + 1}\n".encode())
    pdf.write(b"0000000000 65535 f \n")
    for offset in offsets:
        pdf.write(f"{offset:010d} 00000 n \n".encode())

    pdf.write(b"trailer\n")
    pdf.write(f"<< /Size {len(objects) + 1} /Root 1 0 R >>\n".encode())
    pdf.write(b"startxref\n")
    pdf.write(f"{xref_pos}\n".encode())
    pdf.write(b"%%EOF\n")

    return pdf.getvalue()
