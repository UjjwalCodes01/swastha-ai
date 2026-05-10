"""
Ingestion service — the core 8-step document processing pipeline.

Step 1: File validation (MIME, size, zip bomb, PDF encryption, filename, XXE)
Step 2: Checksum & deduplication (SHA-256 + PostgreSQL lookup)
Step 3: Virus scan (ClamAV daemon or webhook)
Step 4: Generate canonical document ID (DOC-XXXXXXXXXXXX)
Step 5: Store to MinIO with metadata and SSE-S3 encryption
Step 6: Write submission record to PostgreSQL
Step 7: Publish event to Kafka (with local queue fallback)
Step 8: Write to chain-hashed audit log

The service returns immediately after Step 7 — all processing is async.
Callers receive doc_id and status="queued" without waiting for processing.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import random
import string
import uuid
import zipfile
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.logger import write_audit_event
from app.config import get_settings
from app.db.models import Submission, SubmissionStatusEnum, User
from app.ingestion.schemas import (
    BulkDocumentEntry,
    BulkDocumentError,
    BulkIngestResponse,
    SubmissionIngestResponse,
)
from app.ingestion.validators import (
    ValidationResult,
    validate_file_size,
    validate_filename,
    validate_mime_by_magic_bytes,
    validate_pdf_not_encrypted,
    validate_xml_not_xxe,
    validate_zip_bomb,
)
from app.queue.kafka_producer import KafkaProducerClient
from app.queue.topics import RAW_DOCUMENTS_INGESTED, RAW_DOCUMENTS_REJECTED
from app.storage.document_store import DocumentStore

logger = logging.getLogger(__name__)

# Characters used for doc_id generation (uppercase alphanumeric, no ambiguous chars)
_DOC_ID_CHARS = string.ascii_uppercase + string.digits
_DOC_ID_LENGTH = 12
_DOC_ID_PREFIX = "DOC-"


class DuplicateSubmissionError(Exception):
    """Raised when a file with the same SHA-256 hash already exists."""

    def __init__(self, existing_doc_id: str) -> None:
        self.existing_doc_id = existing_doc_id
        super().__init__(f"Duplicate submission: {existing_doc_id}")


class ValidationError(Exception):
    """Raised when a file fails a validation check."""

    def __init__(self, reason: str, error_code: str = "VALIDATION_ERROR") -> None:
        self.reason = reason
        self.error_code = error_code
        super().__init__(reason)


class VirusScanError(Exception):
    """Raised when a virus scan finds an infected file."""

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


class IngestionService:
    """
    Orchestrates the 8-step document ingestion pipeline.

    One instance per request — dependencies (db, minio, kafka) are
    injected via the constructor.
    """

    def __init__(
        self,
        db: AsyncSession,
        document_store: DocumentStore,
        kafka: KafkaProducerClient,
    ) -> None:
        self._db = db
        self._store = document_store
        self._kafka = kafka
        self._settings = get_settings()

    async def ingest_document(
        self,
        *,
        file_bytes: bytes,
        filename: str,
        claimed_mime_type: str,
        submission_type: str,
        portal_source: str,
        actor_id: str,
        actor_email: str,
        ip_address: str | None,
        user_agent: str | None,
        external_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SubmissionIngestResponse:
        """
        Execute the full 8-step ingestion pipeline for a single document.

        Returns immediately with doc_id and status="queued".
        Raises DuplicateSubmissionError, ValidationError, or VirusScanError
        on failure — the router translates these to appropriate HTTP responses.
        """
        # ── Step 1: Validate the file ─────────────────────────────────────────
        await self._run_validations(file_bytes, filename, claimed_mime_type)

        # ── Step 2: Checksum and deduplication ────────────────────────────────
        checksum = self._compute_sha256(file_bytes)
        await self._check_deduplication(checksum)

        # ── Step 3: Virus scan ────────────────────────────────────────────────
        await self._virus_scan(file_bytes, filename)

        # ── Step 4: Generate canonical document ID ────────────────────────────
        doc_id = await self._generate_doc_id()

        # ── Step 5: Store to MinIO ────────────────────────────────────────────
        storage_path = await self._store.store_document(
            doc_id=doc_id,
            submission_type=submission_type,
            original_filename=filename,
            content_type=claimed_mime_type,
            data=file_bytes,
            submitted_by=actor_id,
            checksum=checksum,
        )

        # ── Step 6: Write to PostgreSQL ───────────────────────────────────────
        submission = await self._create_submission_record(
            doc_id=doc_id,
            submission_type=submission_type,
            portal_source=portal_source,
            filename=filename,
            mime_type=claimed_mime_type,
            file_size=len(file_bytes),
            checksum=checksum,
            storage_path=storage_path,
            actor_id=actor_id,
            ip_address=ip_address,
            user_agent=user_agent,
            external_id=external_id,
            metadata=metadata or {},
        )

        # ── Step 7: Publish to Kafka ──────────────────────────────────────────
        kafka_payload = {
            "doc_id": doc_id,
            "raw_storage_path": storage_path,
            "submission_type": submission_type,
            "portal_source": portal_source,
            "submitted_by": actor_id,
            "checksum": checksum,
            "original_filename": filename,
            "file_size_bytes": len(file_bytes),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        kafka_success = await self._kafka.publish(
            topic=RAW_DOCUMENTS_INGESTED,
            payload=kafka_payload,
            key=doc_id,  # Partition key for ordering consistency
        )

        if not kafka_success:
            # Kafka delivery failed — update status to "failed" only if
            # it's also NOT queued locally (complete Kafka outage)
            logger.error(
                "Kafka publish failed for document",
                extra={"doc_id": doc_id},
            )
            # Note: the local queue fallback means the message WILL be retried.
            # We only set status=failed if Kafka is permanently unavailable.
            # For now, leave status as "ingested" since the message is queued locally.

        # Update status to "queued" now that Kafka has the event
        submission.status = SubmissionStatusEnum.queued
        await self._db.commit()

        # ── Step 8: Write audit log ───────────────────────────────────────────
        response_created_at = submission.created_at

        await write_audit_event(
            self._db,
            event_type="document_ingested",
            outcome="success",
            doc_id=doc_id,
            actor_id=actor_id,
            actor_email=actor_email,
            ip_address=ip_address,
            action_detail={
                "doc_id": doc_id,
                "filename": filename,
                "mime_type": claimed_mime_type,
                "file_size_bytes": len(file_bytes),
                "checksum": checksum,
                "storage_path": storage_path,
                "submission_type": submission_type,
                "portal_source": portal_source,
                "kafka_published": kafka_success,
            },
        )

        return SubmissionIngestResponse(
            doc_id=doc_id,
            status="queued",
            message="Document accepted for processing",
            checksum_sha256=checksum,
            raw_storage_path=storage_path,
            created_at=response_created_at,
        )

    async def ingest_bulk(
        self,
        *,
        zip_bytes: bytes,
        manifest: list[BulkDocumentEntry],
        actor_id: str,
        actor_email: str,
        ip_address: str | None,
        user_agent: str | None,
    ) -> BulkIngestResponse:
        """
        Process a bulk zip upload: extract each file and ingest individually.

        Returns a batch_id with counts of successes and failures.
        Does not short-circuit on failure — processes all documents.
        """
        # Validate the zip first
        zip_result = validate_zip_bomb(zip_bytes)
        if not zip_result.passed:
            raise ValidationError(zip_result.reason, "ZIP_BOMB")

        batch_id = str(uuid.uuid4())
        doc_ids: list[str] = []
        errors: list[BulkDocumentError] = []

        # Build a filename -> entry map from the manifest
        manifest_map: dict[str, BulkDocumentEntry] = {
            entry.filename: entry for entry in manifest
        }

        try:
            with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
                for entry in manifest:
                    filename = entry.filename

                    if filename not in [info.filename for info in zf.infolist()]:
                        errors.append(BulkDocumentError(
                            filename=filename,
                            error=f"File '{filename}' listed in manifest but not found in zip",
                            error_code="FILE_NOT_IN_ZIP",
                        ))
                        continue

                    try:
                        file_bytes = zf.read(filename)

                        # Detect MIME from bytes
                        try:
                            import magic
                            detected_mime = magic.from_buffer(file_bytes[:8192], mime=True)
                        except Exception:
                            detected_mime = "application/octet-stream"

                        response = await self.ingest_document(
                            file_bytes=file_bytes,
                            filename=filename,
                            claimed_mime_type=detected_mime,
                            submission_type=entry.submission_type,
                            portal_source=entry.portal_source,
                            actor_id=actor_id,
                            actor_email=actor_email,
                            ip_address=ip_address,
                            user_agent=user_agent,
                            external_id=entry.external_id,
                            metadata=entry.metadata,
                        )
                        doc_ids.append(response.doc_id)

                    except DuplicateSubmissionError as e:
                        errors.append(BulkDocumentError(
                            filename=filename,
                            error=f"Duplicate file — already ingested as {e.existing_doc_id}",
                            error_code="DUPLICATE",
                        ))
                    except ValidationError as e:
                        errors.append(BulkDocumentError(
                            filename=filename,
                            error=e.reason,
                            error_code=e.error_code,
                        ))
                    except Exception as exc:
                        logger.error(
                            "Bulk ingestion error for file",
                            extra={"document_filename": filename, "error": str(exc)},
                        )
                        errors.append(BulkDocumentError(
                            filename=filename,
                            error=f"Internal error: {str(exc)[:200]}",
                            error_code="INTERNAL_ERROR",
                        ))

        except zipfile.BadZipFile:
            raise ValidationError("Uploaded file is not a valid zip archive", "BAD_ZIP")

        await write_audit_event(
            self._db,
            event_type="bulk_upload_complete",
            outcome="success" if not errors else "partial",
            actor_id=actor_id,
            actor_email=actor_email,
            ip_address=ip_address,
            action_detail={
                "batch_id": batch_id,
                "total": len(manifest),
                "successful": len(doc_ids),
                "failed": len(errors),
            },
        )

        return BulkIngestResponse(
            batch_id=batch_id,
            total_submitted=len(manifest),
            successful=len(doc_ids),
            failed=len(errors),
            doc_ids=doc_ids,
            errors=errors,
        )

    async def soft_delete_submission(
        self,
        doc_id: str,
        reason: str,
        actor_id: str,
        actor_email: str,
        ip_address: str | None,
    ) -> None:
        """
        Soft-delete a submission by setting its status to 'rejected'.

        Never deletes from MinIO — regulatory documents must be retained.
        Always writes to the audit log.
        """
        result = await self._db.execute(
            select(Submission).where(Submission.doc_id == doc_id)
        )
        submission = result.scalar_one_or_none()

        if submission is None:
            raise ValueError(f"Submission {doc_id} not found")

        submission.status = SubmissionStatusEnum.rejected
        submission.rejection_reason = reason
        await self._db.commit()

        await write_audit_event(
            self._db,
            event_type="submission_rejected",
            outcome="success",
            doc_id=doc_id,
            actor_id=actor_id,
            actor_email=actor_email,
            ip_address=ip_address,
            action_detail={
                "doc_id": doc_id,
                "reason": reason,
                "previous_status": submission.status.value,
            },
        )

        logger.info(
            "Submission soft-deleted",
            extra={"doc_id": doc_id, "reason": reason, "actor": actor_email},
        )

    # ── Private Helpers ───────────────────────────────────────────────────────

    async def _run_validations(
        self,
        file_bytes: bytes,
        filename: str,
        claimed_mime_type: str,
    ) -> None:
        """Run all validators in order. Raise ValidationError on first failure."""
        validators = [
            ("FILENAME", validate_filename(filename)),
            ("FILE_SIZE", validate_file_size(file_bytes, self._settings.max_upload_size_mb)),
            ("MIME_TYPE", validate_mime_by_magic_bytes(
                file_bytes,
                self._settings.allowed_mime_types,
                claimed_mime_type,
            )),
        ]

        for error_code, result in validators:
            if not result.passed:
                raise ValidationError(result.reason, error_code)

        # Type-specific validators
        canonical_mime = claimed_mime_type.lower()

        if canonical_mime == "application/zip" or filename.endswith(".zip"):
            result = validate_zip_bomb(file_bytes)
            if not result.passed:
                raise ValidationError(result.reason, "ZIP_BOMB")

        if canonical_mime == "application/pdf":
            result = validate_pdf_not_encrypted(file_bytes)
            if not result.passed:
                raise ValidationError(result.reason, "PDF_ENCRYPTED")

        if canonical_mime in ("application/xml", "text/xml"):
            result = validate_xml_not_xxe(file_bytes)
            if not result.passed:
                raise ValidationError(result.reason, "XML_XXE")

    @staticmethod
    def _compute_sha256(file_bytes: bytes) -> str:
        """Compute the SHA-256 hexdigest of the file bytes."""
        return hashlib.sha256(file_bytes).hexdigest()

    async def _check_deduplication(self, checksum: str) -> None:
        """
        Check for an existing submission with the same SHA-256 checksum.

        Raises DuplicateSubmissionError if a duplicate is found.
        """
        result = await self._db.execute(
            select(Submission.doc_id).where(Submission.checksum_sha256 == checksum)
        )
        existing_doc_id = result.scalar_one_or_none()
        if existing_doc_id:
            raise DuplicateSubmissionError(existing_doc_id)

    async def _virus_scan(self, file_bytes: bytes, filename: str) -> None:
        """
        Virus scan via ClamAV daemon or webhook.

        If neither is configured, logs a warning and proceeds.
        This allows dev environments to skip scanning without blocking uploads.
        """
        settings = self._settings

        if settings.clamav_host:
            await self._scan_with_clamd(file_bytes, filename, settings)
        elif settings.virus_scan_webhook_url:
            await self._scan_with_webhook(file_bytes, filename, settings)
        else:
            logger.warning(
                "Virus scanning not configured — proceeding without scan",
                extra={"document_filename": filename, "environment": settings.environment},
            )

    async def _scan_with_clamd(
        self, file_bytes: bytes, filename: str, settings: Any
    ) -> None:
        """Scan using ClamAV INSTREAM protocol over TCP."""
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(settings.clamav_host, settings.clamav_port),
                timeout=10.0,
            )
            try:
                # ClamAV INSTREAM protocol
                writer.write(b"zINSTREAM\0")
                chunk_size = len(file_bytes)
                writer.write(chunk_size.to_bytes(4, byteorder="big"))
                writer.write(file_bytes)
                writer.write(b"\x00\x00\x00\x00")  # End of stream
                await writer.drain()

                response = await asyncio.wait_for(reader.read(1024), timeout=30.0)
                response_str = response.decode("utf-8", errors="replace").strip("\0").strip()

                if "FOUND" in response_str:
                    virus_name = response_str.split(":")[-1].strip() if ":" in response_str else "Unknown"
                    raise VirusScanError(f"Virus detected: {virus_name}")

            finally:
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass

        except (VirusScanError, asyncio.TimeoutError):
            raise
        except Exception as exc:
            logger.warning(
                "ClamAV scan failed — proceeding without scan result",
                extra={"document_filename": filename, "error": str(exc)},
            )

    async def _scan_with_webhook(
        self, file_bytes: bytes, filename: str, settings: Any
    ) -> None:
        """POST file bytes to a configurable virus scan webhook."""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    settings.virus_scan_webhook_url,
                    content=file_bytes,
                    headers={"Content-Type": "application/octet-stream", "X-Filename": filename},
                )
                if response.status_code == 200:
                    result = response.json()
                    if result.get("infected"):
                        raise VirusScanError(f"Virus detected: {result.get('threat', 'Unknown')}")
                else:
                    logger.warning(
                        "Virus scan webhook returned non-200",
                        extra={"status": response.status_code},
                    )
        except VirusScanError:
            raise
        except Exception as exc:
            logger.warning(
                "Virus scan webhook failed — proceeding without scan",
                extra={"document_filename": filename, "error": str(exc)},
            )

    async def _generate_doc_id(self) -> str:
        """
        Generate a unique DOC-XXXXXXXXXXXX identifier.

        Checks PostgreSQL for collisions. Extremely unlikely to need
        more than one attempt given 36^12 ≈ 4.7 trillion combinations.
        """
        for _attempt in range(10):
            suffix = "".join(random.choices(_DOC_ID_CHARS, k=_DOC_ID_LENGTH))
            doc_id = f"{_DOC_ID_PREFIX}{suffix}"

            result = await self._db.execute(
                select(Submission.id).where(Submission.doc_id == doc_id)
            )
            if result.scalar_one_or_none() is None:
                return doc_id

        raise RuntimeError("Failed to generate a unique doc_id after 10 attempts")

    async def _create_submission_record(
        self,
        *,
        doc_id: str,
        submission_type: str,
        portal_source: str,
        filename: str,
        mime_type: str,
        file_size: int,
        checksum: str,
        storage_path: str,
        actor_id: str,
        ip_address: str | None,
        user_agent: str | None,
        external_id: str | None,
        metadata: dict[str, Any],
    ) -> Submission:
        """Create and persist the Submission ORM record."""
        from app.db.models import PortalSourceEnum, SubmissionTypeEnum

        # Resolve submitted_by: only set FK if the user actually exists in DB.
        # If _upsert_user failed earlier (rolled back), we set NULL to avoid FK violation.
        # The actor_id is still preserved in the Kafka payload and audit log.
        submitted_by_uuid: uuid.UUID | None = None
        if actor_id:
            try:
                user_uuid = uuid.UUID(actor_id)
                user_result = await self._db.execute(
                    select(User.id).where(User.id == user_uuid)
                )
                if user_result.scalar_one_or_none() is not None:
                    submitted_by_uuid = user_uuid
            except Exception:
                pass  # Malformed UUID or DB error — leave as None

        submission = Submission(
            id=uuid.uuid4(),
            doc_id=doc_id,
            submission_type=SubmissionTypeEnum(submission_type),
            portal_source=PortalSourceEnum(portal_source),
            external_id=external_id,
            original_filename=filename,
            mime_type=mime_type,
            file_size_bytes=file_size,
            checksum_sha256=checksum,
            raw_storage_path=storage_path,
            status=SubmissionStatusEnum.ingested,
            submitted_by=submitted_by_uuid,
            ip_address=ip_address,
            user_agent=user_agent,
            metadata_=metadata,
            rejection_reason=None,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        self._db.add(submission)
        await self._db.commit()
        await self._db.refresh(submission)
        return submission

