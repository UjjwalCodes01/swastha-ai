from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from datetime import datetime

class DashboardMetrics(BaseModel):
    total_processed: int
    pending_review: int
    critical_saes: int
    auto_approved: int
    throughput: List[Dict[str, Any]]
    priority_breakdown: List[Dict[str, Any]]

class SubmissionSummary(BaseModel):
    id: str
    applicant: str
    type: str
    received: str
    status: str
    priority: str
    score: float

class SubmissionQueueResponse(BaseModel):
    items: List[SubmissionSummary]
    total: int
    page: int
    size: int

class ReviewerActionRequest(BaseModel):
    action: str # "approve", "reject"
    notes: Optional[str] = None
    override_reason: Optional[str] = None
