from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Any

from app.db.connection import get_session
from app.storage.minio_client import get_minio_client
from app.queue.kafka_producer import get_producer
from app.output.schemas import DashboardMetrics, SubmissionQueueResponse, ReviewerActionRequest
from app.output.service import OutputService

router = APIRouter(prefix="/output", tags=["Output & Delivery"])

def get_output_service(
    db: AsyncSession = Depends(get_session),
    minio: Any = Depends(get_minio_client),
    producer: Any = Depends(get_producer)
) -> OutputService:
    return OutputService(db, minio, producer)

@router.get("/dashboard/metrics", response_model=DashboardMetrics)
async def get_metrics(svc: OutputService = Depends(get_output_service)):
    """Fetch aggregated metrics for the reviewer dashboard."""
    return await svc.get_dashboard_metrics()

@router.get("/submissions", response_model=SubmissionQueueResponse)
async def list_submissions(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    svc: OutputService = Depends(get_output_service)
):
    """List submissions for the reviewer queue."""
    return await svc.get_queue(page, size)

@router.post("/submissions/{doc_id}/review")
async def review_submission(
    doc_id: str,
    action: ReviewerActionRequest,
    request: Request,
    svc: OutputService = Depends(get_output_service)
):
    """Submit a reviewer decision (approve/reject)."""
    # Assuming user is added to request.state by auth middleware
    user = getattr(request.state, "user", {"preferred_username": "reviewer1"})
    success = await svc.process_reviewer_action(doc_id, action.action, action.notes or "", user)
    if not success:
        raise HTTPException(status_code=400, detail="Action could not be processed")
    return {"status": "success", "message": f"Document {doc_id} {action.action}ed"}
