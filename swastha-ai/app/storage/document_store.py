"""
Document store — high-level interface for persisting documents to MinIO.

Builds the canonical storage path and delegates to MinIOClient.
Path format: raw/{submission_type}/{YYYY}/{MM}/{DD}/{doc_id}/{original_filename}
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.config import get_settings
from app.storage.minio_client import MinIOClient

logger = logging.getLogger(__name__)


def build_storage_path(
    submission_type: str,
    doc_id: str,
    original_filename: str,
    timestamp: datetime | None = None,
) -> str:
    """
    Build the canonical MinIO object key for a document.

    Format: raw/{submission_type}/{YYYY}/{MM}/{DD}/{doc_id}/{original_filename}

    The date component is always the upload date (UTC), providing
    a time-based namespace that makes chronological enumeration trivial.
    """
    ts = timestamp or datetime.now(timezone.utc)
    return (
        f"raw/{submission_type}/"
        f"{ts.year}/{ts.month:02d}/{ts.day:02d}/"
        f"{doc_id}/{original_filename}"
    )


class DocumentStore:
    """
    High-level document storage operations.

    Wraps MinIOClient with domain-specific logic for SwasthaAI documents.
    """

    def __init__(self, minio: MinIOClient) -> None:
        self._minio = minio
        self._settings = get_settings()

    async def store_document(
        self,
        *,
        doc_id: str,
        submission_type: str,
        original_filename: str,
        content_type: str,
        data: bytes,
        submitted_by: str,
        checksum: str,
    ) -> str:
        """
        Store a document in MinIO and return the resolved storage path.

        Metadata stored alongside the object:
          - doc_id: the canonical document identifier
          - submitted_by: user UUID who uploaded the file
          - checksum_sha256: integrity hash
          - submission_type: drug / medical_device / clinical_trial / sae
        """
        storage_path = build_storage_path(submission_type, doc_id, original_filename)

        metadata = {
            "doc-id": doc_id,
            "submitted-by": str(submitted_by),
            "checksum-sha256": checksum,
            "submission-type": submission_type,
        }

        resolved_path = await self._minio.upload_object(
            bucket=self._settings.minio_bucket_raw,
            key=storage_path,
            data=data,
            content_type=content_type,
            metadata=metadata,
        )

        logger.info(
            "Document stored",
            extra={"doc_id": doc_id, "path": resolved_path, "size": len(data)},
        )
        return resolved_path

    async def get_presigned_download_url(self, storage_path: str) -> str:
        """Generate a 15-minute presigned URL for secure document download."""
        return await self._minio.generate_presigned_url(
            bucket=self._settings.minio_bucket_raw,
            key=storage_path,
            expires_in_seconds=900,
        )
