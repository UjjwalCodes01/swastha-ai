"""Kafka topics used by Layer 3 AI Core."""

DOCUMENTS_PREPROCESSED = "documents.preprocessed"
DOCUMENTS_ANONYMISED = "documents.anonymised"
DOCUMENTS_SUMMARISED = "documents.summarised"
DOCUMENTS_CLASSIFIED = "documents.classified"
DOCUMENTS_COMPARISON_REQUESTED = "documents.comparison.requested"
REPORTS_GENERATED = "reports.generated"
NOTIFICATIONS_EVENTS = "notifications.events"

AI_CORE_INPUT_TOPICS = [
    DOCUMENTS_PREPROCESSED,
    DOCUMENTS_ANONYMISED,
    DOCUMENTS_COMPARISON_REQUESTED,
]
