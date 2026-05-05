"""
MD Online portal adapter.

Polls the MD Online (Medical Devices Online) portal REST API for new
medical device submissions. Same architecture as the SUGAM adapter.

MD Online API response format (example):
{
    "submissions": [
        {
            "SubmissionID": "MDD-2024-001",
            "ManufacturerName": "MediDevice Corp",
            "DeviceClass": "ClassIII",
            "SubmissionDate": "2024-01-20T14:00:00Z",
            "Attachments": [
                {
                    "AttachmentID": "ATT001",
                    "FileType": "TestReport",
                    "OriginalFileName": "biocompatibility_test.pdf",
                    "DownloadLink": "https://mdonline.cdsco.gov.in/attachments/ATT001"
                }
            ]
        }
    ]
}
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

import httpx

from app.adapters.base_adapter import BasePortalAdapter, SubmissionEvent
from app.config import get_settings

logger = logging.getLogger(__name__)

_INITIAL_BACKOFF_SECONDS = 5
_MAX_BACKOFF_SECONDS = 300
_BACKOFF_MULTIPLIER = 2


class MDOnlineAdapter(BasePortalAdapter):
    """
    Adapter for the MD Online (Medical Devices Online) portal.

    MD Online handles medical device submissions including:
    - Device registration applications
    - Import licenses
    - Manufacturing licenses
    - Clinical performance evaluation reports
    """

    # Maps MD Online DeviceClass to SwasthaAI submission_type
    _TYPE_MAP: dict[str, str] = {
        "ClassI": "medical_device",
        "ClassII": "medical_device",
        "ClassIII": "medical_device",
        "ClassIV": "medical_device",
        "IVD": "medical_device",
        "ImportLicense": "medical_device",
        "ManufacturingLicense": "medical_device",
    }

    def __init__(self) -> None:
        settings = get_settings()
        super().__init__(poll_interval_seconds=settings.adapter_poll_interval_seconds)
        self._base_url = settings.md_online_api_base_url
        self._api_key = settings.md_online_api_key
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
                    "User-Agent": "SwasthaAI-MDOnline-Adapter/1.0",
                },
                timeout=httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=5.0),
                follow_redirects=True,
            )
        return self._client

    async def poll_new_submissions(self) -> list[SubmissionEvent]:
        """
        Fetch new (unacknowledged) submissions from MD Online.

        Returns empty list on any error.
        """
        if not self._api_key:
            logger.warning("MD Online API key not configured — skipping poll")
            return []

        try:
            client = await self._get_client()
            response = await client.get(
                "/submissions/pending",
                params={"status": "unacknowledged", "limit": 50},
            )
            response.raise_for_status()
            data = response.json()

            events = self._parse_response(data)
            self._consecutive_errors = 0
            logger.info(
                "MD Online poll successful",
                extra={"new_submissions": len(events)},
            )
            return events

        except httpx.HTTPStatusError as exc:
            self._consecutive_errors += 1
            logger.error(
                "MD Online API returned error",
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
                "MD Online API request timed out",
                extra={"consecutive_errors": self._consecutive_errors},
            )
            return []

        except Exception as exc:
            self._consecutive_errors += 1
            logger.error(
                "Unexpected MD Online adapter error",
                extra={"error": str(exc), "consecutive_errors": self._consecutive_errors},
                exc_info=True,
            )
            return []

    def _parse_response(self, data: dict[str, Any]) -> list[SubmissionEvent]:
        """Translate MD Online API response into canonical SubmissionEvent objects."""
        events = []
        for submission in data.get("submissions", []):
            try:
                submission_id = submission.get("SubmissionID", "")
                manufacturer_name = submission.get("ManufacturerName", "Unknown")
                device_class = submission.get("DeviceClass", "ClassII")
                submission_type = self._TYPE_MAP.get(device_class, "medical_device")

                # Parse submission timestamp
                submitted_at_str = submission.get("SubmissionDate")
                portal_timestamp = None
                if submitted_at_str:
                    try:
                        portal_timestamp = datetime.fromisoformat(
                            submitted_at_str.replace("Z", "+00:00")
                        )
                    except ValueError:
                        pass

                # Extract document URLs from Attachments[]
                document_urls = []
                document_filenames = []
                for attachment in submission.get("Attachments", []):
                    url = attachment.get("DownloadLink", "")
                    filename = attachment.get("OriginalFileName", attachment.get("AttachmentID", "document"))
                    if url:
                        document_urls.append(url)
                        document_filenames.append(filename)

                if not document_urls:
                    logger.warning(
                        "MD Online submission has no attachments — skipping",
                        extra={"submission_id": submission_id},
                    )
                    continue

                event = SubmissionEvent(
                    external_id=submission_id,
                    submission_type=submission_type,
                    portal_source="md_online",
                    applicant_name=manufacturer_name,
                    document_urls=document_urls,
                    document_filenames=document_filenames,
                    portal_timestamp=portal_timestamp,
                    raw_metadata=submission,
                )
                events.append(event)

            except Exception as exc:
                logger.error(
                    "Failed to parse MD Online submission",
                    extra={"submission_data": str(submission)[:200], "error": str(exc)},
                )
                continue

        return events

    async def acknowledge_submission(self, external_id: str) -> bool:
        """Tell MD Online that we have received and processed this submission."""
        try:
            client = await self._get_client()
            response = await client.post(
                f"/submissions/{external_id}/acknowledge",
                json={"acknowledgedBy": "swastha-ai-ingestion-layer"},
            )
            response.raise_for_status()
            logger.info("MD Online submission acknowledged", extra={"external_id": external_id})
            return True
        except Exception as exc:
            logger.error(
                "MD Online acknowledgement failed",
                extra={"external_id": external_id, "error": str(exc)},
            )
            return False

    async def get_submission_document(
        self, document_url: str, filename: str
    ) -> bytes:
        """Download an attachment from MD Online."""
        client = await self._get_client()
        response = await client.get(document_url)
        response.raise_for_status()
        return response.content

    def _compute_backoff(self) -> float:
        """Exponential backoff capped at _MAX_BACKOFF_SECONDS."""
        if self._consecutive_errors == 0:
            return float(self._poll_interval)
        backoff = min(
            _INITIAL_BACKOFF_SECONDS * (_BACKOFF_MULTIPLIER ** (self._consecutive_errors - 1)),
            _MAX_BACKOFF_SECONDS,
        )
        return float(backoff)

    async def run(self) -> None:
        """
        Main polling loop — runs as a background asyncio task.

        On error: backs off exponentially. Never raises or crashes.
        """
        self._running = True
        logger.info("MD Online adapter started")

        while self._running:
            submissions = await self.poll_new_submissions()

            for event in submissions:
                logger.info(
                    "New MD Online submission available",
                    extra={
                        "external_id": event.external_id,
                        "submission_type": event.submission_type,
                        "document_count": len(event.document_urls),
                        "applicant": event.applicant_name,
                    },
                )

            sleep_seconds = self._compute_backoff()
            logger.debug(
                "MD Online adapter sleeping",
                extra={"seconds": sleep_seconds, "consecutive_errors": self._consecutive_errors},
            )
            try:
                await asyncio.sleep(sleep_seconds)
            except asyncio.CancelledError:
                break

        logger.info("MD Online adapter stopped")
        if self._client and not self._client.is_closed:
            await self._client.aclose()
