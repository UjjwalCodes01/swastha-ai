"""
Topic configuration registry for all SwasthaAI Kafka topics.

Every topic is explicitly defined — no topic is created with broker defaults.
Import this module from create_topics.py, verify_setup.py, and admin.py.

RETENTION POLICY RATIONALE:
  raw.documents.ingested    — 7 days: Layer 1 must process within SLA
  documents.*               — 7 days: fast pipeline; 30 days for compliance topics
  notifications.events      — 24 hours: alerts are time-sensitive; stale alerts are noise
  DLQ topics               — 30 days: must allow manual recovery window
  Retry topics             — 24 hours: retry delays are short; old retries are invalid
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

IS_PRODUCTION = os.getenv("ENVIRONMENT", "development").lower() == "production"

REPLICATION_FACTOR = 3 if IS_PRODUCTION else 1
MIN_ISR = 2 if IS_PRODUCTION else 1


@dataclass
class TopicConfig:
    name: str
    partitions: int
    replication_factor: int = REPLICATION_FACTOR
    configs: dict[str, str] = field(default_factory=dict)

    def to_kafka_dict(self) -> dict[str, str]:
        """Return configs in kafka-python NewTopic format."""
        return {k: str(v) for k, v in self.configs.items()}


# ── Primary Data Topics ───────────────────────────────────────────────────────

RAW_DOCUMENTS_INGESTED = TopicConfig(
    name="raw.documents.ingested",
    partitions=6,
    configs={
        "retention.ms": "604800000",           # 7 days
        "retention.bytes": "5368709120",        # 5 GB
        "min.insync.replicas": str(MIN_ISR),
        "cleanup.policy": "delete",
        "compression.type": "lz4",
        "max.message.bytes": "10485760",        # 10 MB
        "segment.bytes": "1073741824",          # 1 GB segments
    },
)

DOCUMENTS_PREPROCESSED = TopicConfig(
    name="documents.preprocessed",
    partitions=6,
    configs={
        "retention.ms": "604800000",
        "retention.bytes": "10737418240",       # 10 GB — processed docs are larger
        "min.insync.replicas": str(MIN_ISR),
        "cleanup.policy": "delete",
        "compression.type": "lz4",
        "max.message.bytes": "10485760",
        "segment.bytes": "1073741824",
    },
)

DOCUMENTS_CHUNKS_READY = TopicConfig(
    name="documents.chunks.ready",
    partitions=6,
    configs={
        "retention.ms": "604800000",
        "retention.bytes": "1073741824",        # 1 GB — small event
        "min.insync.replicas": str(MIN_ISR),
        "cleanup.policy": "delete",
        "compression.type": "lz4",
        "max.message.bytes": "1048576",         # 1 MB max
    },
)

DOCUMENTS_ANONYMISED = TopicConfig(
    name="documents.anonymised",
    partitions=6,
    configs={
        "retention.ms": "2592000000",           # 30 days — DPDP compliance
        "retention.bytes": "10737418240",
        "min.insync.replicas": str(MIN_ISR),
        "cleanup.policy": "delete",
        "compression.type": "lz4",
        "max.message.bytes": "10485760",
    },
)

DOCUMENTS_SUMMARISED = TopicConfig(
    name="documents.summarised",
    partitions=6,
    configs={
        "retention.ms": "2592000000",
        "retention.bytes": "10737418240",
        "min.insync.replicas": str(MIN_ISR),
        "cleanup.policy": "delete",
        "compression.type": "lz4",
        "max.message.bytes": "10485760",
    },
)

DOCUMENTS_CLASSIFIED = TopicConfig(
    name="documents.classified",
    partitions=6,
    configs={
        "retention.ms": "2592000000",           # 30 days — compliance layer references for 30d
        "retention.bytes": "5368709120",
        "min.insync.replicas": str(MIN_ISR),
        "cleanup.policy": "delete",
        "compression.type": "lz4",
        "max.message.bytes": "1048576",
    },
)

DOCUMENTS_COMPARISON_REQUESTED = TopicConfig(
    name="documents.comparison.requested",
    partitions=3,                               # Lower volume
    configs={
        "retention.ms": "86400000",             # 24 hours — requests are time-sensitive
        "retention.bytes": "536870912",         # 512 MB
        "cleanup.policy": "delete",
        "compression.type": "lz4",
        "max.message.bytes": "1048576",
    },
)

REPORTS_GENERATED = TopicConfig(
    name="reports.generated",
    partitions=6,
    configs={
        "retention.ms": "2592000000",
        "retention.bytes": "10737418240",
        "min.insync.replicas": str(MIN_ISR),
        "cleanup.policy": "delete",
        "compression.type": "lz4",
        "max.message.bytes": "10485760",
    },
)

NOTIFICATIONS_EVENTS = TopicConfig(
    name="notifications.events",
    partitions=3,
    configs={
        "retention.ms": "86400000",             # 24 hours — stale alerts are noise
        "retention.bytes": "536870912",
        "cleanup.policy": "delete",
        "compression.type": "lz4",
        "max.message.bytes": "1048576",
    },
)

# ── Dead Letter Queue Topics ──────────────────────────────────────────────────
# 30-day retention — must allow manual recovery window

_DLQ_PRIMARY_TOPICS = [
    "raw.documents.ingested",
    "documents.preprocessed",
    "documents.anonymised",
    "documents.summarised",
    "documents.classified",
    "reports.generated",
]

DLQ_TOPICS: list[TopicConfig] = [
    TopicConfig(
        name=f"{t}.dlq",
        partitions=3,
        configs={
            "retention.ms": "2592000000",       # 30 days — never silently expire
            "retention.bytes": "5368709120",
            "cleanup.policy": "delete",
            "compression.type": "lz4",
            "max.message.bytes": "10485760",
        },
    )
    for t in _DLQ_PRIMARY_TOPICS
]

# ── Retry Topics (per primary topic, 3 levels) ────────────────────────────────
# Delay is simulated: consumer checks timestamp and sleeps until delay elapsed

RETRY_DELAYS_SECONDS = {1: 5, 2: 30, 3: 300}       # retry.1=5s, retry.2=30s, retry.3=5min

_RETRY_PRIMARY_TOPICS = [
    "raw.documents.ingested",
    "documents.preprocessed",
    "documents.anonymised",
    "documents.summarised",
    "documents.classified",
    "reports.generated",
]

RETRY_TOPICS: list[TopicConfig] = [
    TopicConfig(
        name=f"{t}.retry.{level}",
        partitions=3,
        configs={
            "retention.ms": "86400000",         # 24 hours — retries expire with the document SLA
            "retention.bytes": "536870912",
            "cleanup.policy": "delete",
            "compression.type": "lz4",
            "max.message.bytes": "10485760",
        },
    )
    for t in _RETRY_PRIMARY_TOPICS
    for level in range(1, 4)
]

# ── Master registry of all topics ─────────────────────────────────────────────

ALL_TOPICS: list[TopicConfig] = [
    RAW_DOCUMENTS_INGESTED,
    DOCUMENTS_PREPROCESSED,
    DOCUMENTS_CHUNKS_READY,
    DOCUMENTS_ANONYMISED,
    DOCUMENTS_SUMMARISED,
    DOCUMENTS_CLASSIFIED,
    DOCUMENTS_COMPARISON_REQUESTED,
    REPORTS_GENERATED,
    NOTIFICATIONS_EVENTS,
    *DLQ_TOPICS,
    *RETRY_TOPICS,
]

# Handy lookup by name
TOPIC_BY_NAME: dict[str, TopicConfig] = {t.name: t for t in ALL_TOPICS}
