"""
Pydantic v2 schemas for the SwasthaAI ingestion API.

All request bodies, response bodies, and internal data transfer objects
are defined here. FastAPI uses these for OpenAPI doc generation and
automatic request validation.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


# ── Enums (string-based for JSON serialisation) ───────────────────────────────

class SubmissionType(str):
    """Valid submission types — matches the PostgreSQL ENUM."""
    DRUG = "drug"
    MEDICAL_DEVICE = "medical_device"
    CLINICAL_TRIAL = "clinical_trial"
    SAE = "sae"


class PortalSource(str):
    SUGAM = "sugam"
    MD_ONLINE = "md_online"
    MANUAL = "manual"
    SAE_FEED = "sae_feed"


class SubmissionStatus(str):
    INGESTED = "ingested"
    QUEUED = "queued"
    PREPROCESSING = "preprocessing"
    PROCESSED = "processed"
    FAILED = "failed"
    REJECTED = "rejected"


# ── Request Bodies ────────────────────────────────────────────────────────────

class SubmissionIngestRequest(BaseModel):
    """
    Multipart form data for a single document submission.

    The `file` field is handled separately as an UploadFile by FastAPI.
    These fields are the accompanying form fields.
    """

    submission_type: str = Field(
        ...,
        description="Type of regulatory submission",
        examples=["drug", "medical_device", "clinical_trial", "sae"],
    )
    portal_source: str = Field(
        default="manual",
        description="Portal the document originated from",
        examples=["sugam", "md_online", "manual", "sae_feed"],
    )
    external_id: str | None = Field(
        default=None,
        max_length=100,
        description="Portal's own reference ID for cross-referencing",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional metadata to store with the submission",
    )

    @field_validator("submission_type")
    @classmethod
    def validate_submission_type(cls, v: str) -> str:
        valid = {"drug", "medical_device", "clinical_trial", "sae"}
        if v not in valid:
            raise ValueError(f"submission_type must be one of: {valid}")
        return v

    @field_validator("portal_source")
    @classmethod
    def validate_portal_source(cls, v: str) -> str:
        valid = {"sugam", "md_online", "manual", "sae_feed"}
        if v not in valid:
            raise ValueError(f"portal_source must be one of: {valid}")
        return v


class BulkIngestManifest(BaseModel):
    """
    Manifest file structure for bulk (zip) uploads.

    The zip must contain a manifest.json at the root with this structure.
    Each entry maps a filename within the zip to its submission metadata.
    """

    documents: list[BulkDocumentEntry] = Field(
        ...,
        min_length=1,
        max_length=100,
        description="List of documents in the zip with their metadata",
    )


class BulkDocumentEntry(BaseModel):
    """Metadata for a single document within a bulk upload zip."""

    filename: str = Field(
        ...,
        max_length=255,
        description="Filename as it appears in the zip archive",
    )
    submission_type: str = Field(...)
    portal_source: str = Field(default="manual")
    external_id: str | None = Field(default=None, max_length=100)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("submission_type")
    @classmethod
    def validate_submission_type(cls, v: str) -> str:
        valid = {"drug", "medical_device", "clinical_trial", "sae"}
        if v not in valid:
            raise ValueError(f"submission_type must be one of: {valid}")
        return v


class SubmissionDeleteRequest(BaseModel):
    """Request body for soft-deleting a submission (admin only)."""

    reason: str = Field(
        ...,
        min_length=10,
        max_length=1000,
        description="Reason for rejection — required for audit trail",
    )


# ── Response Bodies ───────────────────────────────────────────────────────────

class SubmissionIngestResponse(BaseModel):
    """
    Response returned immediately after a successful document upload.

    The `status` will be "queued" — processing happens asynchronously.
    Use GET /status/{doc_id} to poll for processing completion.
    """

    doc_id: str = Field(
        ...,
        description="Canonical document identifier (format: DOC-XXXXXXXXXXXX)",
        examples=["DOC-A1B2C3D4E5F6"],
    )
    status: str = Field(
        default="queued",
        description="Initial processing status",
    )
    message: str = Field(
        default="Document accepted for processing",
    )
    checksum_sha256: str = Field(
        ...,
        description="SHA-256 checksum of the uploaded file for integrity verification",
    )
    raw_storage_path: str = Field(
        ...,
        description="MinIO storage path where the raw document is stored",
    )
    created_at: datetime = Field(...)


class BulkIngestResponse(BaseModel):
    """Response for a bulk (zip) upload."""

    batch_id: str = Field(
        ...,
        description="UUID identifying this bulk upload batch",
    )
    total_submitted: int
    successful: int
    failed: int
    doc_ids: list[str] = Field(
        description="List of doc_ids for successfully ingested documents",
    )
    errors: list[BulkDocumentError] = Field(
        default_factory=list,
        description="Errors for documents that failed ingestion",
    )


class BulkDocumentError(BaseModel):
    """Describes a failure for a single document within a bulk upload."""

    filename: str
    error: str
    error_code: str | None = None


class SubmissionStatusResponse(BaseModel):
    """Status response for GET /status/{doc_id}."""

    doc_id: str
    status: str
    submission_type: str
    portal_source: str
    original_filename: str
    mime_type: str
    file_size_bytes: int
    checksum_sha256: str
    submitted_by: str | None = None
    created_at: datetime
    updated_at: datetime
    rejection_reason: str | None = None
    download_url: str | None = Field(
        default=None,
        description="15-minute presigned download URL (only for processed submissions)",
    )


class SubmissionDeleteResponse(BaseModel):
    """Response for a soft-delete operation."""

    doc_id: str
    status: str = "rejected"
    message: str


class HealthResponse(BaseModel):
    """Health check response showing the status of all external dependencies."""

    status: str = Field(description="'healthy' or 'degraded'")
    version: str = "1.0.0"
    environment: str
    services: dict[str, ServiceHealth]


class ServiceHealth(BaseModel):
    """Health status of a single external service."""

    status: str = Field(description="'ok', 'degraded', or 'unavailable'")
    latency_ms: float | None = None
    detail: str | None = None


class ErrorResponse(BaseModel):
    """Standard error response shape for all 4xx/5xx responses."""

    error: str
    detail: str | None = None
    doc_id: str | None = None
    request_id: str | None = None
