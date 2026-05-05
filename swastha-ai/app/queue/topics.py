"""
Kafka topic name constants for the SwasthaAI ingestion layer.

ALL Kafka topic references in the codebase MUST use these constants.
Never hardcode topic strings directly in producers or consumers.

Topic naming convention: {domain}.{entity}.{event}
"""

from __future__ import annotations

# ── Ingestion Layer Topics ────────────────────────────────────────────────────

# Published when a new document is successfully ingested and stored in MinIO.
# Downstream preprocessing services consume from this topic.
RAW_DOCUMENTS_INGESTED = "raw.documents.ingested"

# Published when an ingested document fails validation or virus scan.
RAW_DOCUMENTS_REJECTED = "raw.documents.rejected"

# Published when a batch/bulk upload completes.
RAW_DOCUMENTS_BATCH_COMPLETE = "raw.documents.batch_complete"

# Published when a document's status is updated by the ingestion layer.
SUBMISSION_STATUS_UPDATED = "submissions.status.updated"

# Dead-letter topic: messages that could not be processed after retries.
INGESTION_DEAD_LETTER = "ingestion.dead_letter"

# ── All Topics ────────────────────────────────────────────────────────────────
# Used during startup to ensure topics are created if auto-creation is disabled.

ALL_TOPICS: list[str] = [
    RAW_DOCUMENTS_INGESTED,
    RAW_DOCUMENTS_REJECTED,
    RAW_DOCUMENTS_BATCH_COMPLETE,
    SUBMISSION_STATUS_UPDATED,
    INGESTION_DEAD_LETTER,
]
