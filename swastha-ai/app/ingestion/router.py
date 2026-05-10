"""
Ingestion API router.

Endpoints:
  POST   /api/v1/ingest/submission        — single document upload
  POST   /api/v1/ingest/bulk             — bulk zip upload (admin only)
  GET    /api/v1/ingest/status/{doc_id}  — submission status
  DELETE /api/v1/ingest/submission/{doc_id} — soft delete (admin only)
  GET    /api/v1/ingest/health           — service health check
"""

from __future__ import annotations

import io
import json
import logging
import time
import uuid
import zipfile
from typing import Any

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.logger import write_audit_event
from app.auth.jwt_handler import TokenData
from app.auth.rbac import require_any_authenticated_role, require_role
from app.db.models import Submission, SubmissionStatusEnum, UserRoleEnum
from app.dependencies import get_db, get_document_store, get_kafka, get_redis
from app.ingestion.schemas import (
    BulkIngestManifest,
    BulkIngestResponse,
    ErrorResponse,
    HealthResponse,
    ServiceHealth,
    SubmissionDeleteRequest,
    SubmissionDeleteResponse,
    SubmissionIngestResponse,
    SubmissionStatusResponse,
)
from app.ingestion.service import (
    DuplicateSubmissionError,
    IngestionService,
    ValidationError,
    VirusScanError,
)
from app.middleware.request_id import get_request_id
from app.queue.kafka_producer import KafkaProducerClient
from app.rate_limit.limiter import check_rate_limit
from app.storage.document_store import DocumentStore
from app.queue.kafka_producer import get_kafka_producer
from app.storage.minio_client import get_minio_client

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/ingest",
    tags=["Ingestion"],
)


async def _direct_process_document(
    doc_id: str,
    raw_storage_path: str,
    submission_type: str,
    portal_source: str,
    submitted_by: str | None,
    original_filename: str,
    file_bytes: bytes,
) -> None:
    """
    Run the preprocessing pipeline directly, bypassing Kafka.

    Used when Kafka is unavailable (local dev). This processes the document
    in-process as a background task so the upload API returns immediately.
    """
    from app.db.connection import get_session_factory
    from app.preprocessing.pipeline import PreprocessingPipeline
    from app.preprocessing.embedder.embedding_service import EmbeddingService
    from app.preprocessing.embedder.chroma_store import ChromaStore
    from app.config import get_settings
    from sqlalchemy import text as sa_text

    settings = get_settings()

    try:
        logger.info(f"[Direct Pipeline] Starting for {doc_id}")

        # Update status to preprocessing
        session_factory = get_session_factory()
        async with session_factory() as session:
            await session.execute(
                sa_text("UPDATE submissions SET status = 'preprocessing' WHERE doc_id = :doc_id"),
                {"doc_id": doc_id},
            )
            await session.commit()

        # Initialize lightweight pipeline components
        minio_client = await get_minio_client()

        # Embedding service — try to initialize, fallback to no-op if it fails
        embedding_svc = EmbeddingService(batch_size=settings.embedding_batch_size)
        try:
            from app.dependencies import get_redis as _get_redis
            redis = await _get_redis()
            await embedding_svc.initialize(redis)
        except Exception as emb_exc:
            logger.warning(f"[Direct Pipeline] Embedding init failed: {emb_exc} — using dummy embeddings")
            # Patch generate_embeddings to return zero vectors
            async def _dummy_embeddings(texts):
                return [[0.0] * 384 for _ in texts]
            embedding_svc.generate_embeddings = _dummy_embeddings

        # ChromaDB — try to connect, fallback gracefully
        chroma_store = ChromaStore(host=settings.chroma_host, port=settings.chroma_port)
        try:
            await chroma_store.initialize()
        except Exception as chroma_exc:
            logger.warning(f"[Direct Pipeline] ChromaDB init failed: {chroma_exc} — skipping vector storage")
            # Patch add_chunks to no-op
            async def _dummy_add_chunks(**kwargs):
                return ""
            chroma_store.add_chunks = _dummy_add_chunks

        pipeline = PreprocessingPipeline(
            minio_client=minio_client,
            db_session_factory=session_factory,
            embedding_service=embedding_svc,
            chroma_store=chroma_store,
            ocr_concurrency=settings.ocr_concurrency,
        )

        # Override the pipeline's _save_to_minio to use our MinIOClient wrapper
        async def _save_to_minio_fixed(path, data):
            import json as _json
            json_bytes = _json.dumps(data, ensure_ascii=False).encode("utf-8")
            bucket = settings.ai_core_processed_bucket
            # Ensure bucket exists
            try:
                await minio_client._client.head_bucket(Bucket=bucket)
            except Exception:
                await minio_client._client.create_bucket(Bucket=bucket)
            await minio_client._client.put_object(
                Bucket=bucket, Key=path, Body=json_bytes, ContentType="application/json",
            )
        pipeline._save_to_minio = _save_to_minio_fixed

        # Run the full pipeline
        result = await pipeline.process_document(
            doc_id=doc_id,
            file_bytes=file_bytes,
            submission_type=submission_type,
            portal_source=portal_source,
            submitted_by=submitted_by,
            original_filename=original_filename,
        )

        logger.info(
            f"[Direct Pipeline] Preprocessing completed for {doc_id}",
            extra={"chunks": result.get("chunks"), "duration_ms": result.get("duration_ms")},
        )

        # ── Layer 3: AI Core (Summarisation, Classification, Anonymisation) ──
        try:
            from app.ai_core.pipeline import AICorePipeline
            from app.queue.kafka_producer import get_kafka_producer
            from datetime import datetime as _dt, timezone as _tz

            producer = await get_kafka_producer()
            ai_pipeline = AICorePipeline(
                minio_client=minio_client,
                producer=producer,
                db_session_factory=session_factory,
                chroma_client=None,
            )

            # Build the event payload that Layer 1 would normally publish to Kafka
            now = _dt.now(_tz.utc)
            processed_path = f"processed/{submission_type}/{now.year}/{now.month:02d}/{now.day:02d}/{doc_id}/processed.json"
            preprocessed_event = {
                "doc_id": doc_id,
                "processed_storage_path": processed_path,
                "submission_type": submission_type,
                "portal_source": portal_source,
                "submitted_by": submitted_by,
                "original_filename": original_filename,
            }

            # Step 1: Anonymisation (handle_preprocessed)
            anon_result = await ai_pipeline.handle_preprocessed(preprocessed_event)
            logger.info(f"[Direct Pipeline] Anonymisation done for {doc_id}")

            # Step 2: Summarisation + Classification (handle_anonymised)
            await ai_pipeline.handle_anonymised(anon_result)
            logger.info(f"[Direct Pipeline] AI Core completed for {doc_id}")

        except Exception as ai_exc:
            logger.warning(
                f"[Direct Pipeline] AI Core failed for {doc_id}: {ai_exc}",
                exc_info=True,
            )
            # Don't fail the whole pipeline — preprocessing already succeeded

    except Exception as exc:
        logger.error(
            f"[Direct Pipeline] Failed for {doc_id}: {exc}",
            exc_info=True,
        )
        # Mark as failed in DB
        try:
            session_factory = get_session_factory()
            async with session_factory() as session:
                await session.execute(
                    sa_text("UPDATE submissions SET status = 'failed' WHERE doc_id = :doc_id"),
                    {"doc_id": doc_id},
                )
                await session.commit()
        except Exception:
            pass


def _get_client_ip(request: Request) -> str:
    """Extract the real client IP from the request."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


# ── POST /submission ───────────────────────────────────────────────────────────


@router.post(
    "/submission",
    response_model=SubmissionIngestResponse,
    status_code=status.HTTP_200_OK,
    summary="Ingest a single regulatory document",
    description="""
    Upload a single regulatory document for ingestion.

    The file is immediately stored in MinIO and an event is published to Kafka.
    Returns doc_id and status="queued" — processing is asynchronous.

    **Supported MIME types**: PDF, DOCX, XML, CSV, JSON, ZIP

    **Rate limits**: 60 requests/min per IP, 20 requests/min per user

    **Auth**: Requires portal_operator, api_client, or admin role.
    """,
    responses={
        200: {"description": "Document accepted and queued for processing"},
        400: {"model": ErrorResponse, "description": "Invalid request"},
        401: {"model": ErrorResponse, "description": "Authentication required"},
        403: {"model": ErrorResponse, "description": "Insufficient permissions"},
        409: {"model": ErrorResponse, "description": "Duplicate document"},
        413: {"model": ErrorResponse, "description": "File too large"},
        415: {"model": ErrorResponse, "description": "Unsupported file type"},
        422: {"model": ErrorResponse, "description": "File failed validation (virus, encryption, etc.)"},
        429: {"model": ErrorResponse, "description": "Rate limit exceeded"},
    },
)
async def ingest_submission(
    request: Request,
    file: UploadFile = File(..., description="The document to upload"),
    submission_type: str = Form(
        ...,
        description="Type: drug | medical_device | clinical_trial | sae",
    ),
    portal_source: str = Form(
        default="manual",
        description="Source: sugam | md_online | manual | sae_feed",
    ),
    external_id: str | None = Form(
        default=None,
        description="Portal's reference ID for cross-referencing",
    ),
    db: AsyncSession = Depends(get_db),
    document_store: DocumentStore = Depends(get_document_store),
    kafka: KafkaProducerClient = Depends(get_kafka),
    redis: Any = Depends(get_redis),
    current_user: TokenData = Depends(
        require_role([
            UserRoleEnum.admin,
            UserRoleEnum.portal_operator,
            UserRoleEnum.api_client,
        ])
    ),
) -> SubmissionIngestResponse:
    request_id = get_request_id()
    client_ip = _get_client_ip(request)

    # Rate limiting
    await check_rate_limit(
        request=request,
        db=db,
        redis=redis,
        user_id=current_user.sub,
        role=current_user.primary_role,
    )

    # Read file bytes eagerly (don't hold file handle open during processing)
    try:
        file_bytes = await file.read()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to read uploaded file: {exc}",
        )

    if not file_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is empty",
        )

    filename = file.filename or "unknown"
    claimed_mime = file.content_type or "application/octet-stream"

    logger.info(
        "Document upload received",
        extra={
            "request_id": request_id,
            "document_filename": filename,
            "size_bytes": len(file_bytes),
            "submission_type": submission_type,
            "actor": current_user.email,
            "ip": client_ip,
        },
    )

    service = IngestionService(db=db, document_store=document_store, kafka=kafka)

    try:
        result = await service.ingest_document(
            file_bytes=file_bytes,
            filename=filename,
            claimed_mime_type=claimed_mime,
            submission_type=submission_type,
            portal_source=portal_source,
            actor_id=current_user.sub,
            actor_email=current_user.email,
            ip_address=client_ip,
            user_agent=request.headers.get("User-Agent"),
            external_id=external_id,
        )

        # ── Direct processing fallback when Kafka is unavailable ──────────
        # In local dev, Kafka broker is often unreachable. Messages sit in the
        # local in-memory queue and consumers can't start. To keep the pipeline
        # working, we trigger preprocessing directly as a background task.
        kafka_healthy = await kafka.health_check()
        if not kafka_healthy:
            import asyncio
            asyncio.create_task(
                _direct_process_document(
                    doc_id=result.doc_id,
                    raw_storage_path=result.raw_storage_path,
                    submission_type=submission_type,
                    portal_source=portal_source,
                    submitted_by=current_user.sub,
                    original_filename=filename,
                    file_bytes=file_bytes,
                ),
                name=f"direct-process-{result.doc_id}",
            )
            logger.info(
                "Kafka unavailable — triggered direct pipeline processing",
                extra={"doc_id": result.doc_id},
            )

        return result

    except DuplicateSubmissionError as e:
        await write_audit_event(
            db,
            event_type="document_duplicate_rejected",
            outcome="blocked",
            actor_id=current_user.sub,
            actor_email=current_user.email,
            ip_address=client_ip,
            action_detail={"filename": filename, "existing_doc_id": e.existing_doc_id},
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "Duplicate document",
                "existing_doc_id": e.existing_doc_id,
                "message": "A document with this exact content already exists",
            },
        )

    except ValidationError as e:
        status_code = {
            "FILE_SIZE": status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            "MIME_TYPE": status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
        }.get(e.error_code, status.HTTP_422_UNPROCESSABLE_ENTITY)

        await write_audit_event(
            db,
            event_type="document_validation_failed",
            outcome="failure",
            actor_id=current_user.sub,
            actor_email=current_user.email,
            ip_address=client_ip,
            action_detail={"filename": filename, "reason": e.reason, "code": e.error_code},
        )
        raise HTTPException(status_code=status_code, detail=e.reason)

    except VirusScanError as e:
        await write_audit_event(
            db,
            event_type="document_virus_detected",
            outcome="blocked",
            actor_id=current_user.sub,
            actor_email=current_user.email,
            ip_address=client_ip,
            action_detail={"filename": filename, "detail": e.detail},
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Virus scan failed: {e.detail}",
        )


# ── POST /bulk ────────────────────────────────────────────────────────────────


@router.post(
    "/bulk",
    response_model=BulkIngestResponse,
    status_code=status.HTTP_200_OK,
    summary="Bulk ingest documents from a zip archive",
    description="""
    Upload a zip archive containing multiple regulatory documents.

    The zip **must** contain a `manifest.json` at the root level specifying
    metadata for each document. Documents not listed in the manifest are ignored.

    **Auth**: Admin only.

    **manifest.json format**:
    ```json
    {
      "documents": [
        {
          "filename": "report.pdf",
          "submission_type": "drug",
          "portal_source": "manual",
          "external_id": "APP-12345"
        }
      ]
    }
    ```
    """,
    responses={
        200: {"description": "Bulk upload processed (check errors array for partial failures)"},
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        422: {"model": ErrorResponse, "description": "Invalid zip or manifest"},
    },
)
async def ingest_bulk(
    request: Request,
    file: UploadFile = File(..., description="Zip archive containing documents and manifest.json"),
    db: AsyncSession = Depends(get_db),
    document_store: DocumentStore = Depends(get_document_store),
    kafka: KafkaProducerClient = Depends(get_kafka),
    redis: Any = Depends(get_redis),
    current_user: TokenData = Depends(require_role([UserRoleEnum.admin])),
) -> BulkIngestResponse:
    request_id = get_request_id()
    client_ip = _get_client_ip(request)

    await check_rate_limit(request=request, db=db, redis=redis, user_id=current_user.sub)

    zip_bytes = await file.read()

    if not zip_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded zip file is empty",
        )

    # Extract and parse the manifest
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
            if "manifest.json" not in zf.namelist():
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Zip archive must contain a manifest.json file at the root level",
                )
            manifest_bytes = zf.read("manifest.json")
    except zipfile.BadZipFile:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Uploaded file is not a valid zip archive",
        )

    try:
        manifest_data = json.loads(manifest_bytes)
        manifest = BulkIngestManifest(**manifest_data)
    except (json.JSONDecodeError, ValueError, Exception) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid manifest.json: {exc}",
        )

    logger.info(
        "Bulk upload received",
        extra={
            "request_id": request_id,
            "zip_size_bytes": len(zip_bytes),
            "document_count": len(manifest.documents),
            "actor": current_user.email,
        },
    )

    service = IngestionService(db=db, document_store=document_store, kafka=kafka)

    try:
        result = await service.ingest_bulk(
            zip_bytes=zip_bytes,
            manifest=manifest.documents,
            actor_id=current_user.sub,
            actor_email=current_user.email,
            ip_address=client_ip,
            user_agent=request.headers.get("User-Agent"),
        )
        return result
    except ValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=e.reason,
        )


# ── GET /status/{doc_id} ──────────────────────────────────────────────────────


@router.get(
    "/status/{doc_id}",
    response_model=SubmissionStatusResponse,
    summary="Get the processing status of a submission",
    description="""
    Returns the current status of a document submission.

    **Access control**:
    - **admin**: can see all submissions
    - **reviewer**: can only see submissions assigned to them
    - **portal_operator**: can only see their own submissions
    - **api_client**: can only see their own submissions
    """,
    responses={
        200: {"description": "Submission status"},
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse, "description": "Submission not found"},
    },
)
async def get_submission_status(
    doc_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(require_any_authenticated_role()),
) -> SubmissionStatusResponse:
    result = await db.execute(
        select(Submission).where(Submission.doc_id == doc_id)
    )
    submission = result.scalar_one_or_none()

    if submission is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Submission {doc_id} not found",
        )

    # Access control: non-admin users can only see their own submissions
    if current_user.primary_role != "admin":
        # submitted_by may be None if the user FK was set to NULL on creation
        # (e.g. api_client whose user row didn't exist yet). In that case,
        # reviewers still get 403 but api_clients get through since they
        # submitted it implicitly.
        if submission.submitted_by is not None:
            if str(submission.submitted_by) != current_user.sub:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="You do not have permission to view this submission",
                )
        # If submitted_by is None, only api_client/portal_operator can pass through
        elif current_user.primary_role not in ("api_client", "portal_operator", "admin"):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to view this submission",
            )

    # Generate presigned URL for processed submissions
    download_url = None
    if submission.status == SubmissionStatusEnum.processed:
        try:
            minio = await get_minio_client()
            from app.storage.document_store import DocumentStore as DS
            store = DS(minio)
            download_url = await store.get_presigned_download_url(submission.raw_storage_path)
        except Exception as exc:
            logger.warning("Failed to generate presigned URL", extra={"error": str(exc)})

    return SubmissionStatusResponse(
        doc_id=submission.doc_id,
        status=submission.status.value,
        submission_type=submission.submission_type.value,
        portal_source=submission.portal_source.value,
        original_filename=submission.original_filename,
        mime_type=submission.mime_type,
        file_size_bytes=submission.file_size_bytes,
        checksum_sha256=submission.checksum_sha256,
        submitted_by=str(submission.submitted_by) if submission.submitted_by else None,
        created_at=submission.created_at,
        updated_at=submission.updated_at,
        rejection_reason=submission.rejection_reason,
        download_url=download_url,
    )


# ── DELETE /submission/{doc_id} ───────────────────────────────────────────────


@router.delete(
    "/submission/{doc_id}",
    response_model=SubmissionDeleteResponse,
    summary="Soft-delete a submission (admin only)",
    description="""
    Mark a submission as rejected with a mandatory reason.

    **This is a soft delete only** — the document is never removed from MinIO.
    Regulatory documents must be retained regardless of rejection status.

    The rejection is permanently recorded in the tamper-evident audit log.

    **Auth**: Admin only.
    """,
    responses={
        200: {"description": "Submission rejected"},
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse, "description": "Submission not found"},
    },
)
async def delete_submission(
    doc_id: str,
    body: SubmissionDeleteRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    document_store: DocumentStore = Depends(get_document_store),
    kafka: KafkaProducerClient = Depends(get_kafka),
    current_user: TokenData = Depends(require_role([UserRoleEnum.admin])),
) -> SubmissionDeleteResponse:
    client_ip = _get_client_ip(request)

    service = IngestionService(db=db, document_store=document_store, kafka=kafka)

    try:
        await service.soft_delete_submission(
            doc_id=doc_id,
            reason=body.reason,
            actor_id=current_user.sub,
            actor_email=current_user.email,
            ip_address=client_ip,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        )

    return SubmissionDeleteResponse(
        doc_id=doc_id,
        status="rejected",
        message=f"Submission {doc_id} has been rejected and is retained in storage",
    )


# ── GET /health ────────────────────────────────────────────────────────────────


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Service health check",
    description="Returns the health status of the ingestion service and all dependencies. No authentication required.",
    include_in_schema=True,
)
async def health_check(
    db: AsyncSession = Depends(get_db),
    redis: Any = Depends(get_redis),
) -> HealthResponse:
    from app.config import get_settings
    settings = get_settings()
    services: dict[str, ServiceHealth] = {}

    # PostgreSQL
    try:
        start = time.monotonic()
        from sqlalchemy import text
        await db.execute(text("SELECT 1"))
        latency = (time.monotonic() - start) * 1000
        services["postgres"] = ServiceHealth(status="ok", latency_ms=round(latency, 2))
    except Exception as exc:
        services["postgres"] = ServiceHealth(status="unavailable", detail=str(exc)[:100])

    # MinIO
    try:
        start = time.monotonic()
        minio = await get_minio_client()
        healthy = await minio.health_check()
        latency = (time.monotonic() - start) * 1000
        services["minio"] = ServiceHealth(
            status="ok" if healthy else "unavailable",
            latency_ms=round(latency, 2),
        )
    except Exception as exc:
        services["minio"] = ServiceHealth(status="unavailable", detail=str(exc)[:100])

    # Kafka
    try:
        kafka = await get_kafka_producer()
        kafka_ok = await kafka.health_check()
        services["kafka"] = ServiceHealth(
            status="ok" if kafka_ok else "degraded",
            detail="Using local queue fallback" if not kafka_ok else None,
        )
    except Exception as exc:
        services["kafka"] = ServiceHealth(status="unavailable", detail=str(exc)[:100])

    # Redis
    try:
        start = time.monotonic()
        await redis.ping()
        latency = (time.monotonic() - start) * 1000
        services["redis"] = ServiceHealth(status="ok", latency_ms=round(latency, 2))
    except Exception as exc:
        services["redis"] = ServiceHealth(status="unavailable", detail=str(exc)[:100])

    overall = "healthy" if all(s.status == "ok" for s in services.values()) else "degraded"

    return HealthResponse(
        status=overall,
        environment=settings.environment,
        services=services,
    )
