"""
Abstract base class for portal adapters.

All portal adapters (SUGAM, MD Online, etc.) must implement this interface.
The adapters run as background asyncio tasks polling their respective portals
for new submissions and translating them into the canonical SubmissionEvent format.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class SubmissionEvent:
    """
    Canonical submission event returned by all portal adapters.

    All portal-specific fields are normalised into this structure before
    being handed off to the IngestionService for processing.
    """

    # Portal-assigned reference ID (e.g., SUGAM ApplicationID)
    external_id: str

    # Submission type (drug / medical_device / clinical_trial / sae)
    submission_type: str

    # Which portal this came from (sugam / md_online / manual / sae_feed)
    portal_source: str

    # Applicant name from the portal
    applicant_name: str

    # List of document download URLs as returned by the portal
    document_urls: list[str]

    # Original filenames parallel to document_urls
    document_filenames: list[str]

    # Timestamp from the portal (when the submission was received by them)
    portal_timestamp: datetime | None = None

    # Additional portal-specific metadata (preserved for audit purposes)
    raw_metadata: dict[str, Any] = field(default_factory=dict)


class BasePortalAdapter(ABC):
    """
    Abstract base class for portal submission adapters.

    Subclasses implement the three abstract methods:
    - poll_new_submissions: fetch unacknowledged submissions from the portal
    - acknowledge_submission: mark a submission as received by SwasthaAI
    - get_submission_document: download a document from the portal

    The run() method is the main entry point — it runs as a long-lived
    asyncio task, polling on a configurable interval with exponential backoff.
    """

    def __init__(self, poll_interval_seconds: int = 60) -> None:
        self._poll_interval = poll_interval_seconds
        self._running = False

    @abstractmethod
    async def poll_new_submissions(self) -> list[SubmissionEvent]:
        """
        Fetch unacknowledged submissions from the portal.

        Must return a list of SubmissionEvent objects.
        Must NOT raise on transient errors — handle internally and return [].
        """
        ...

    @abstractmethod
    async def acknowledge_submission(self, external_id: str) -> bool:
        """
        Acknowledge receipt of a submission to the portal.

        Called after the submission has been successfully ingested.
        Returns True on success, False on failure.
        """
        ...

    @abstractmethod
    async def get_submission_document(
        self, document_url: str, filename: str
    ) -> bytes:
        """
        Download a document from the portal by its URL.

        Returns the raw document bytes.
        Raises an exception if the download fails after retries.
        """
        ...

    @property
    def adapter_name(self) -> str:
        """Return the human-readable name of this adapter."""
        return self.__class__.__name__

    def stop(self) -> None:
        """Signal the adapter's run loop to stop on the next iteration."""
        self._running = False
