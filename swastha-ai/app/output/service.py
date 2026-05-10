import logging
from typing import Any, List, Dict
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from app.output.schemas import DashboardMetrics, SubmissionSummary, SubmissionQueueResponse

logger = logging.getLogger(__name__)

class OutputService:
    def __init__(self, db: AsyncSession, minio: Any, producer: Any):
        self._db = db
        self._minio = minio
        self._producer = producer

    async def get_dashboard_metrics(self) -> DashboardMetrics:
        """Returns mock metrics for the frontend dashboard."""
        # In a real scenario, these would aggregate from PostgreSQL `submissions` and `compliance_assessments`
        return DashboardMetrics(
            total_processed=2845,
            pending_review=142,
            critical_saes=28,
            auto_approved=1893,
            throughput=[
                {"name": "Mon", "submissions": 40},
                {"name": "Tue", "submissions": 30},
                {"name": "Wed", "submissions": 20},
            ],
            priority_breakdown=[
                {"name": "Drug", "critical": 12, "processed": 400},
            ]
        )

    async def get_queue(self, page: int = 1, size: int = 10) -> SubmissionQueueResponse:
        """Returns pending cases."""
        # Mock data representing DB queries
        mock_data = [
            SubmissionSummary(id="DOC-8A7B9C", applicant="PharmaCorp Ltd.", type="drug", received="10 mins ago", status="review_required", priority="high", score=0.82),
            SubmissionSummary(id="DOC-2X9Y4Z", applicant="MedTech Devices", type="medical_device", received="1 hr ago", status="processed", priority="medium", score=0.95),
        ]
        return SubmissionQueueResponse(items=mock_data, total=142, page=page, size=size)

    async def process_reviewer_action(self, doc_id: str, action: str, notes: str, user: Any) -> bool:
        """Process manual review approval/rejection and trigger webhook."""
        logger.info(f"Reviewer action: {action} on {doc_id} by {user.get('preferred_username', 'unknown')}")
        # Here we would update DB status to approved/rejected and publish an event
        # await self._producer.publish("documents.reviewed", {"doc_id": doc_id, "action": action})
        return True
