"""
Integration tests for the ingestion API endpoints.

Tests cover all success and failure paths for:
- POST /api/v1/ingest/submission
- POST /api/v1/ingest/bulk
- GET  /api/v1/ingest/status/{doc_id}
- DELETE /api/v1/ingest/submission/{doc_id}
- GET  /api/v1/ingest/health
"""

from __future__ import annotations

import io
import json
import uuid
import zipfile
from unittest.mock import AsyncMock, patch, MagicMock

import pytest
import pytest_asyncio
from httpx import AsyncClient

from tests.conftest import auth_headers, make_test_token


pytestmark = pytest.mark.asyncio


# ── Helpers ───────────────────────────────────────────────────────────────────


def make_upload_files(
    filename: str = "test.pdf",
    content_type: str = "application/pdf",
    content: bytes = b"%PDF-1.4 minimal",
) -> dict:
    return {"file": (filename, content, content_type)}


def make_bulk_zip(documents: list[tuple[str, bytes]], manifest: dict) -> bytes:
    """Create a zip archive with documents and a manifest.json."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        for filename, content in documents:
            zf.writestr(filename, content)
    return buf.getvalue()


# ── POST /submission Tests ────────────────────────────────────────────────────


class TestIngestSubmission:
    """Tests for POST /api/v1/ingest/submission"""

    async def test_successful_pdf_upload_returns_doc_id(
        self, async_client: AsyncClient, sample_pdf_bytes: bytes
    ):
        """Test that a valid PDF upload returns 200 with a doc_id."""
        with patch("app.ingestion.service.IngestionService._run_validations", new_callable=AsyncMock), \
             patch("app.ingestion.service.IngestionService._virus_scan", new_callable=AsyncMock):
            response = await async_client.post(
                "/api/v1/ingest/submission",
                headers=auth_headers("portal_operator"),
                files={"file": ("report.pdf", sample_pdf_bytes, "application/pdf")},
                data={"submission_type": "drug", "portal_source": "manual"},
            )

        assert response.status_code == 200
        body = response.json()
        assert "doc_id" in body
        assert body["doc_id"].startswith("DOC-")
        assert len(body["doc_id"]) == 16  # DOC- + 12 chars
        assert body["status"] == "queued"
        assert "checksum_sha256" in body

    async def test_duplicate_file_returns_409(
        self, async_client: AsyncClient, sample_pdf_bytes: bytes
    ):
        """Test that uploading the same file twice returns 409 with existing doc_id."""
        from app.ingestion.service import DuplicateSubmissionError

        existing_doc_id = "DOC-EXISTINGDOC1"
        with patch(
            "app.ingestion.service.IngestionService._check_deduplication",
            new_callable=AsyncMock,
            side_effect=DuplicateSubmissionError(existing_doc_id),
        ), patch("app.ingestion.service.IngestionService._run_validations", new_callable=AsyncMock):
            response = await async_client.post(
                "/api/v1/ingest/submission",
                headers=auth_headers("portal_operator"),
                files={"file": ("report.pdf", sample_pdf_bytes, "application/pdf")},
                data={"submission_type": "drug"},
            )

        assert response.status_code == 409
        body = response.json()
        assert "existing_doc_id" in body["detail"]
        assert existing_doc_id in str(body["detail"])

    async def test_oversized_file_returns_413(self, async_client: AsyncClient):
        """Test that a file exceeding the size limit returns 413."""
        from app.ingestion.service import ValidationError

        with patch(
            "app.ingestion.service.IngestionService._run_validations",
            new_callable=AsyncMock,
            side_effect=ValidationError("File size 150.0MB exceeds the 100MB limit", "FILE_SIZE"),
        ):
            response = await async_client.post(
                "/api/v1/ingest/submission",
                headers=auth_headers("portal_operator"),
                files={"file": ("large.pdf", b"content", "application/pdf")},
                data={"submission_type": "drug"},
            )

        assert response.status_code == 413

    async def test_disallowed_mime_type_returns_415(self, async_client: AsyncClient):
        """Test that a file with a disallowed MIME type returns 415."""
        from app.ingestion.service import ValidationError

        with patch(
            "app.ingestion.service.IngestionService._run_validations",
            new_callable=AsyncMock,
            side_effect=ValidationError("MIME type 'image/jpeg' is not permitted", "MIME_TYPE"),
        ):
            response = await async_client.post(
                "/api/v1/ingest/submission",
                headers=auth_headers("portal_operator"),
                files={"file": ("photo.jpg", b"\xff\xd8\xff", "image/jpeg")},
                data={"submission_type": "drug"},
            )

        assert response.status_code == 415

    async def test_unauthenticated_request_returns_401(
        self, async_client: AsyncClient, sample_pdf_bytes: bytes
    ):
        """Test that a request without auth returns 401."""
        response = await async_client.post(
            "/api/v1/ingest/submission",
            files={"file": ("report.pdf", sample_pdf_bytes, "application/pdf")},
            data={"submission_type": "drug"},
        )
        assert response.status_code == 401

    async def test_wrong_role_returns_403(
        self, async_client: AsyncClient, sample_pdf_bytes: bytes
    ):
        """Test that a reviewer (read-only) role gets 403 on upload."""
        response = await async_client.post(
            "/api/v1/ingest/submission",
            headers=auth_headers("reviewer"),
            files={"file": ("report.pdf", sample_pdf_bytes, "application/pdf")},
            data={"submission_type": "drug"},
        )
        assert response.status_code == 403

    async def test_path_traversal_filename_is_rejected(
        self, async_client: AsyncClient
    ):
        """Test that a filename with path traversal is rejected."""
        from app.ingestion.service import ValidationError

        with patch(
            "app.ingestion.service.IngestionService._run_validations",
            new_callable=AsyncMock,
            side_effect=ValidationError(
                "Filename contains path traversal sequences", "FILENAME"
            ),
        ):
            response = await async_client.post(
                "/api/v1/ingest/submission",
                headers=auth_headers("portal_operator"),
                files={"file": ("../../etc/passwd", b"content", "application/pdf")},
                data={"submission_type": "drug"},
            )

        assert response.status_code == 422

    async def test_zip_bomb_is_rejected(self, async_client: AsyncClient):
        """Test that a zip bomb is detected and rejected."""
        from app.ingestion.service import ValidationError

        with patch(
            "app.ingestion.service.IngestionService._run_validations",
            new_callable=AsyncMock,
            side_effect=ValidationError(
                "Zip compression ratio 1000:1 exceeds maximum", "ZIP_BOMB"
            ),
        ):
            response = await async_client.post(
                "/api/v1/ingest/submission",
                headers=auth_headers("portal_operator"),
                files={"file": ("archive.zip", b"PK", "application/zip")},
                data={"submission_type": "drug"},
            )

        assert response.status_code == 422
        assert "zip" in response.json()["detail"].lower() or "ratio" in response.json()["detail"].lower()

    async def test_encrypted_pdf_is_rejected(self, async_client: AsyncClient):
        """Test that an encrypted (password-protected) PDF is rejected."""
        from app.ingestion.service import ValidationError

        with patch(
            "app.ingestion.service.IngestionService._run_validations",
            new_callable=AsyncMock,
            side_effect=ValidationError(
                "PDF is password-protected. Encrypted PDFs cannot be processed.", "PDF_ENCRYPTED"
            ),
        ):
            response = await async_client.post(
                "/api/v1/ingest/submission",
                headers=auth_headers("portal_operator"),
                files={"file": ("encrypted.pdf", b"%PDF-1.4 encrypted", "application/pdf")},
                data={"submission_type": "drug"},
            )

        assert response.status_code == 422
        assert "encrypted" in response.json()["detail"].lower()

    async def test_admin_can_upload(
        self, async_client: AsyncClient, sample_pdf_bytes: bytes
    ):
        """Test that admin role can also upload documents."""
        with patch("app.ingestion.service.IngestionService._run_validations", new_callable=AsyncMock), \
             patch("app.ingestion.service.IngestionService._virus_scan", new_callable=AsyncMock):
            response = await async_client.post(
                "/api/v1/ingest/submission",
                headers=auth_headers("admin"),
                files={"file": ("admin_upload.pdf", sample_pdf_bytes, "application/pdf")},
                data={"submission_type": "clinical_trial"},
            )

        assert response.status_code == 200
        assert response.json()["doc_id"].startswith("DOC-")


# ── POST /bulk Tests ──────────────────────────────────────────────────────────


class TestBulkIngest:
    """Tests for POST /api/v1/ingest/bulk"""

    async def test_bulk_upload_with_valid_zip_and_manifest(
        self, async_client: AsyncClient, sample_pdf_bytes: bytes
    ):
        """Test successful bulk upload with a valid zip and manifest."""
        manifest = {
            "documents": [
                {
                    "filename": "doc1.pdf",
                    "submission_type": "drug",
                    "portal_source": "manual",
                }
            ]
        }
        zip_bytes = make_bulk_zip([("doc1.pdf", sample_pdf_bytes)], manifest)

        with patch("app.ingestion.service.IngestionService._run_validations", new_callable=AsyncMock), \
             patch("app.ingestion.service.IngestionService._virus_scan", new_callable=AsyncMock), \
             patch("app.ingestion.service.validate_zip_bomb", return_value=MagicMock(passed=True, reason="ok")):
            response = await async_client.post(
                "/api/v1/ingest/bulk",
                headers=auth_headers("admin"),
                files={"file": ("batch.zip", zip_bytes, "application/zip")},
            )

        assert response.status_code == 200
        body = response.json()
        assert "batch_id" in body
        assert body["total_submitted"] == 1

    async def test_bulk_upload_requires_admin(
        self, async_client: AsyncClient
    ):
        """Test that non-admin users cannot perform bulk uploads."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("manifest.json", '{"documents": []}')
        zip_bytes = buf.getvalue()

        response = await async_client.post(
            "/api/v1/ingest/bulk",
            headers=auth_headers("portal_operator"),
            files={"file": ("batch.zip", zip_bytes, "application/zip")},
        )
        assert response.status_code == 403

    async def test_bulk_upload_missing_manifest_returns_422(
        self, async_client: AsyncClient
    ):
        """Test that a zip without manifest.json returns 422."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("doc.pdf", b"content")
        zip_bytes = buf.getvalue()

        response = await async_client.post(
            "/api/v1/ingest/bulk",
            headers=auth_headers("admin"),
            files={"file": ("batch.zip", zip_bytes, "application/zip")},
        )
        assert response.status_code == 422
        assert "manifest.json" in response.json()["detail"]


# ── GET /status/{doc_id} Tests ────────────────────────────────────────────────


class TestSubmissionStatus:
    """Tests for GET /api/v1/ingest/status/{doc_id}"""

    async def test_status_endpoint_returns_correct_status(
        self, async_client: AsyncClient, db_session
    ):
        """Test that the status endpoint returns submission details."""
        # Create a submission record in the test DB
        from datetime import timezone
        from app.db.models import (
            Submission, SubmissionTypeEnum, PortalSourceEnum, SubmissionStatusEnum
        )

        sub_id = uuid.uuid4()
        actor_id = uuid.uuid4()
        doc_id = "DOC-STATUSTEST01"
        submission = Submission(
            id=sub_id,
            doc_id=doc_id,
            submission_type=SubmissionTypeEnum.drug,
            portal_source=PortalSourceEnum.manual,
            original_filename="test.pdf",
            mime_type="application/pdf",
            file_size_bytes=1024,
            checksum_sha256="a" * 64,
            raw_storage_path="raw/drug/2024/01/15/DOC-STATUSTEST01/test.pdf",
            status=SubmissionStatusEnum.queued,
            submitted_by=actor_id,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        db_session.add(submission)
        await db_session.commit()

        token = make_test_token(sub=str(actor_id), role="portal_operator")
        response = await async_client.get(
            f"/api/v1/ingest/status/{doc_id}",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["doc_id"] == doc_id
        assert body["status"] == "queued"

    async def test_status_not_found_returns_404(
        self, async_client: AsyncClient
    ):
        """Test that a non-existent doc_id returns 404."""
        response = await async_client.get(
            "/api/v1/ingest/status/DOC-DOESNOTEXIST1",
            headers=auth_headers("admin"),
        )
        assert response.status_code == 404


# ── DELETE /submission/{doc_id} Tests ─────────────────────────────────────────


class TestDeleteSubmission:
    """Tests for DELETE /api/v1/ingest/submission/{doc_id}"""

    async def test_soft_delete_sets_status_to_rejected(
        self, async_client: AsyncClient, db_session
    ):
        """Test that soft delete sets status to 'rejected' without removing from MinIO."""
        from datetime import timezone
        from app.db.models import (
            Submission, SubmissionTypeEnum, PortalSourceEnum, SubmissionStatusEnum
        )

        doc_id = "DOC-SOFTDEL00001"
        submission = Submission(
            id=uuid.uuid4(),
            doc_id=doc_id,
            submission_type=SubmissionTypeEnum.drug,
            portal_source=PortalSourceEnum.manual,
            original_filename="to_delete.pdf",
            mime_type="application/pdf",
            file_size_bytes=512,
            checksum_sha256="b" * 64,
            raw_storage_path="raw/drug/2024/01/15/DOC-SOFTDEL00001/to_delete.pdf",
            status=SubmissionStatusEnum.queued,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        db_session.add(submission)
        await db_session.commit()

        with patch("app.audit.logger.write_audit_event", new_callable=AsyncMock):
            response = await async_client.delete(
                f"/api/v1/ingest/submission/{doc_id}",
                headers=auth_headers("admin"),
                json={"reason": "Document submitted in error — not a valid CDSCO submission"},
            )

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "rejected"
        assert body["doc_id"] == doc_id

    async def test_soft_delete_requires_admin(self, async_client: AsyncClient):
        """Test that only admins can soft-delete submissions."""
        response = await async_client.delete(
            "/api/v1/ingest/submission/DOC-NODELETEPERM",
            headers=auth_headers("portal_operator"),
            json={"reason": "This should fail"},
        )
        assert response.status_code == 403

    async def test_soft_delete_nonexistent_returns_404(
        self, async_client: AsyncClient
    ):
        """Test that deleting a non-existent submission returns 404."""
        response = await async_client.delete(
            "/api/v1/ingest/submission/DOC-NOTFOUND001",
            headers=auth_headers("admin"),
            json={"reason": "Testing non-existent doc_id deletion"},
        )
        assert response.status_code == 404


# ── GET /health Tests ─────────────────────────────────────────────────────────


class TestHealthCheck:
    """Tests for GET /api/v1/ingest/health"""

    async def test_health_check_returns_service_status(
        self, async_client: AsyncClient, mock_kafka
    ):
        """Test that health check returns status for all services."""
        with patch("app.ingestion.router.get_minio_client", new_callable=AsyncMock) as mock_get_minio, \
             patch("app.ingestion.router.get_kafka_producer", new_callable=AsyncMock) as mock_get_kafka:
            mock_minio_inst = AsyncMock()
            mock_minio_inst.health_check = AsyncMock(return_value=True)
            mock_get_minio.return_value = mock_minio_inst
            mock_get_kafka.return_value = mock_kafka

            response = await async_client.get("/api/v1/ingest/health")

        assert response.status_code == 200
        body = response.json()
        assert "status" in body
        assert "services" in body
        assert "postgres" in body["services"]

    async def test_health_check_requires_no_auth(self, async_client: AsyncClient):
        """Test that health check is publicly accessible."""
        with patch("app.ingestion.router.get_minio_client", new_callable=AsyncMock) as m, \
             patch("app.ingestion.router.get_kafka_producer", new_callable=AsyncMock) as k:
            mock_minio_inst = AsyncMock()
            mock_minio_inst.health_check = AsyncMock(return_value=True)
            m.return_value = mock_minio_inst
            k.return_value = AsyncMock(health_check=AsyncMock(return_value=True))

            # No auth headers
            response = await async_client.get("/api/v1/ingest/health")

        # Should not return 401
        assert response.status_code != 401


# ── Import needed in tests ────────────────────────────────────────────────────
from datetime import datetime
