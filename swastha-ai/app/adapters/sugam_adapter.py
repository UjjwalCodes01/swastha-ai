"""
SUGAM portal adapter.

Polls the SUGAM REST API every 60 seconds for new drug/device submissions.
Translates SUGAM's ApplicationID, ApplicantName, Documents[] format into
the canonical SubmissionEvent structure.

Auth: API key via X-API-Key header.
Error handling: exponential backoff, never crashes the app.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from app.adapters.base_adapter import BasePortalAdapter, SubmissionEvent
from app.config import get_settings

logger = logging.getLogger(__name__)

# Exponential backoff configuration
_INITIAL_BACKOFF_SECONDS = 5
_MAX_BACKOFF_SECONDS = 300  # 5 minutes
_BACKOFF_MULTIPLIER = 2


class SUGAMAdapter(BasePortalAdapter):
    """
    Adapter for the SUGAM (System for Unified General Approvals and Management)
    portal operated by CDSCO.

    SUGAM API response format (example):
    {
        "applications": [
            {
                "ApplicationID": "SUGAM-2024-001",
                "ApplicantName": "PharmaCo Ltd",
                "ApplicationType": "NewDrug",
                "SubmittedAt": "2024-01-15T10:30:00Z",
                "Documents": [
                    {
                        "DocumentID": "DOC001",
                        "DocumentType": "CTD",
                        "FileName": "module1.pdf",
                        "DownloadURL": "https://sugam.cdsco.gov.in/docs/DOC001"
                    }
                ]
            }
        ]
    }
    """

    # Maps SUGAM ApplicationType to SwasthaAI submission_type
    _TYPE_MAP: dict[str, str] = {
        "NewDrug": "drug",
        "GenericDrug": "drug",
        "ClinicalTrial": "clinical_trial",
        "MedicalDevice": "medical_device",
        "SAE": "sae",
        "IND": "clinical_trial",
        "NDA": "drug",
        "ANDA": "drug",
    }

    def __init__(self) -> None:
        settings = get_settings()
        super().__init__(poll_interval_seconds=settings.adapter_poll_interval_seconds)
        self._base_url = settings.sugam_api_base_url
        self._api_key = settings.sugam_api_key
        self._client: httpx.AsyncClient | None = None
        self._consecutive_errors = 0

    async def _get_client(self) -> httpx.AsyncClient:
        """Lazily create the HTTP client."""
        if self._client is None or getattr(self._client, "is_closed", False) is True:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                headers={
                    "X-API-Key": self._api_key,
                    "Accept": "application/json",
                    "User-Agent": "SwasthaAI-SUGAM-Adapter/1.0",
                },
                timeout=httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=5.0),
                follow_redirects=True,
            )
        return self._client

    async def poll_new_submissions(self) -> list[SubmissionEvent]:
        """
        Fetch new (unacknowledged) submissions from SUGAM.

        Returns an empty list on any error to avoid crashing the polling loop.
        """
        if not self._api_key:
            logger.warning("SUGAM API key not configured — skipping poll")
            return []

        try:
            client = await self._get_client()
            response = await client.get(
                "/submissions/pending",
                params={"status": "pending", "limit": 50},
            )
            response.raise_for_status()
            data = response.json()

            events = self._parse_response(data)
            self._consecutive_errors = 0  # Reset backoff counter on success
            logger.info(
                "SUGAM poll successful",
                extra={"new_submissions": len(events)},
            )
            return events

        except httpx.HTTPStatusError as exc:
            self._consecutive_errors += 1
            logger.error(
                "SUGAM API returned error",
                extra={
                    "status_code": exc.response.status_code,
                    "error": str(exc)[:200],
                    "consecutive_errors": self._consecutive_errors,
                },
            )
            return []

        except httpx.TimeoutException:
            self._consecutive_errors += 1
            logger.warning(
                "SUGAM API request timed out",
                extra={"consecutive_errors": self._consecutive_errors},
            )
            return []

        except Exception as exc:
            self._consecutive_errors += 1
            logger.error(
                "Unexpected SUGAM adapter error",
                extra={"error": str(exc), "consecutive_errors": self._consecutive_errors},
                exc_info=True,
            )
            return []

    def _parse_response(self, data: dict[str, Any]) -> list[SubmissionEvent]:
        """Translate SUGAM API response into canonical SubmissionEvent objects."""
        events = []
        for app in data.get("applications", []):
            try:
                application_id = app.get("ApplicationID", "")
                applicant_name = app.get("ApplicantName", "Unknown")
                app_type = app.get("ApplicationType", "drug")
                submission_type = self._TYPE_MAP.get(app_type, "drug")

                submitted_at_str = app.get("SubmittedAt")
                portal_timestamp = None
                if submitted_at_str:
                    try:
                        portal_timestamp = datetime.fromisoformat(
                            submitted_at_str.replace("Z", "+00:00")
                        )
                    except ValueError:
                        pass

                document_urls = []
                document_filenames = []
                for doc in app.get("Documents", []):
                    url = doc.get("DownloadURL", "")
                    filename = doc.get("FileName", doc.get("DocumentID", "document"))
                    if url:
                        document_urls.append(url)
                        document_filenames.append(filename)

                if not document_urls:
                    logger.warning(
                        "SUGAM submission has no documents — skipping",
                        extra={"application_id": application_id},
                    )
                    continue

                event = SubmissionEvent(
                    external_id=application_id,
                    submission_type=submission_type,
                    portal_source="sugam",
                    applicant_name=applicant_name,
                    document_urls=document_urls,
                    document_filenames=document_filenames,
                    portal_timestamp=portal_timestamp,
                    raw_metadata=app,
                )
                events.append(event)

            except Exception as exc:
                logger.error(
                    "Failed to parse SUGAM submission",
                    extra={"application_data": str(app)[:200], "error": str(exc)},
                )
                continue

        return events

    async def acknowledge_submission(self, external_id: str) -> bool:
        """Tell SUGAM that we have received and processed this submission."""
        try:
            client = await self._get_client()
            response = await client.post(
                f"/submissions/{external_id}/acknowledge",
                json={"status": "received", "receiver": "swastha-ai"},
            )
            response.raise_for_status()
            logger.info("SUGAM submission acknowledged", extra={"external_id": external_id})
            return True
        except Exception as exc:
            logger.error(
                "SUGAM acknowledgement failed",
                extra={"external_id": external_id, "error": str(exc)},
            )
            return False

    async def get_submission_document(
        self, document_url: str, filename: str
    ) -> bytes:
        """Download a document from SUGAM."""
        client = await self._get_client()
        response = await client.get(document_url)
        response.raise_for_status()
        return response.content

    def _compute_backoff(self) -> float:
        """Exponential backoff capped at _MAX_BACKOFF_SECONDS."""
        if self._consecutive_errors == 0:
            return self._poll_interval
        backoff = min(
            _INITIAL_BACKOFF_SECONDS * (_BACKOFF_MULTIPLIER ** (self._consecutive_errors - 1)),
            _MAX_BACKOFF_SECONDS,
        )
        return float(backoff)

    async def run(self) -> None:
        """
        Main polling loop — runs as a background asyncio task.

        On error: backs off exponentially. Never raises or crashes.
        Stops cleanly when stop() is called.
        """
        self._running = True
        logger.info("SUGAM adapter started")

        while self._running:
            submissions = await self.poll_new_submissions()

            for event in submissions:
                logger.info(
                    "New SUGAM submission available",
                    extra={
                        "external_id": event.external_id,
                        "submission_type": event.submission_type,
                        "document_count": len(event.document_urls),
                    },
                )
                # Note: actual ingestion is handled by the main ingestion service.
                # The adapter's job is to detect new submissions and emit events.
                # The ingestion pipeline picks these up via a separate consumer
                # or the adapter calls ingest_document() directly in a full impl.

            sleep_seconds = self._compute_backoff()
            logger.debug(
                "SUGAM adapter sleeping",
                extra={"seconds": sleep_seconds, "consecutive_errors": self._consecutive_errors},
            )
            try:
                await asyncio.sleep(sleep_seconds)
            except asyncio.CancelledError:
                break

        logger.info("SUGAM adapter stopped")
        if self._client and not self._client.is_closed:
            await self._client.aclose()
