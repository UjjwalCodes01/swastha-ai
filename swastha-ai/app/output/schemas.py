from pydantic import BaseModel
from typing import Any, Dict, List, Optional


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
    action: str  # "approve" | "reject"
    notes: Optional[str] = None
    override_reason: Optional[str] = None


class SubmissionDetail(BaseModel):
    """Full AI analysis for a single submission."""
    id: str
    submission_type: str
    filename: str
    status: str
    checksum_sha256: str
    portal_source: str
    received_at: str
    file_size_bytes: int

    # AI analysis
    executive_summary: str
    key_findings: List[str]
    risks: List[str]
    missing_information: List[str]
    recommended_next_steps: List[str]
    summary_model_id: str
    summary_confidence: float

    # Classification
    classification: Dict[str, Any]
    completeness_score: float
    missing_required_fields: List[str]
    duplicate_candidates: List[Dict[str, Any]]

    # Anonymisation
    pii_entities_removed: int
    anonymisation_method: str

    # Compliance & XAI
    compliance_findings: List[Dict[str, Any]]
    xai_log: List[Dict[str, Any]]
