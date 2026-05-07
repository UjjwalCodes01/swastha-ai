"""Layer 2 — Message Bus tables.

Revision ID: 0003
Revises: 0002
Create Date: 2024-01-03 00:00:00.000000 UTC

This migration creates:
  1. kafka_events         — full event audit log (partitioned by month)
  2. consumer_group_state — lag and heartbeat tracking per partition
  3. circuit_breaker_state — persisted circuit breaker state
  4. dlq_events           — DLQ event records for admin UI and recovery
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:

    # ── kafka_events (range-partitioned by produced_at month) ─────────────────
    # NOTE: SQLAlchemy/Alembic don't support declarative partitioning directly.
    # We use raw SQL for the partitioned table and its child partitions.
    op.execute("""
        CREATE TABLE kafka_events (
            id                      UUID DEFAULT gen_random_uuid(),
            event_id                VARCHAR(100) NOT NULL,
            topic                   VARCHAR(200) NOT NULL,
            partition               INTEGER,
            "offset"                BIGINT,
            consumer_group          VARCHAR(200),
            doc_id                  VARCHAR(32),
            event_type              VARCHAR(100),
            payload_summary         JSONB,
            processing_status       VARCHAR(20) NOT NULL DEFAULT 'received'
                CONSTRAINT chk_kafka_status
                CHECK (processing_status IN (
                    'received','processing','success','failed','dlq','dismissed'
                )),
            processing_duration_ms  INTEGER,
            error_detail            TEXT,
            retry_count             INTEGER NOT NULL DEFAULT 0,
            produced_at             TIMESTAMPTZ NOT NULL,
            consumed_at             TIMESTAMPTZ,
            completed_at            TIMESTAMPTZ,
            created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (id, produced_at)
        ) PARTITION BY RANGE (produced_at)
    """)

    # Create initial monthly partitions (Jan–Dec 2024 + 2025)
    for year in (2024, 2025, 2026):
        for month in range(1, 13):
            next_month = month + 1
            next_year = year
            if next_month > 12:
                next_month = 1
                next_year = year + 1
            partition_name = f"kafka_events_{year}_{month:02d}"
            op.execute(f"""
                CREATE TABLE {partition_name}
                    PARTITION OF kafka_events
                    FOR VALUES FROM ('{year}-{month:02d}-01')
                    TO ('{next_year}-{next_month:02d}-01')
            """)

    # Unique index on event_id must include the partition key
    op.execute("""
        CREATE UNIQUE INDEX uq_kafka_events_event_id
            ON kafka_events (event_id, produced_at)
    """)
    op.execute("""
        CREATE INDEX idx_kafka_events_doc_id
            ON kafka_events (doc_id)
        WHERE doc_id IS NOT NULL
    """)
    op.execute("""
        CREATE INDEX idx_kafka_events_topic_status
            ON kafka_events (topic, processing_status)
    """)
    op.execute("""
        CREATE INDEX idx_kafka_events_produced_at
            ON kafka_events (produced_at DESC)
    """)

    # ── consumer_group_state ──────────────────────────────────────────────────
    op.create_table(
        "consumer_group_state",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("group_id",              sa.String(200), nullable=False),
        sa.Column("topic",                 sa.String(200), nullable=False),
        sa.Column("partition",             sa.Integer(),   nullable=False),
        sa.Column("committed_offset",      sa.BigInteger(), nullable=False),
        sa.Column("lag",                   sa.BigInteger(), nullable=True),
        sa.Column("last_heartbeat",        sa.DateTime(timezone=True), nullable=True),
        sa.Column("consumer_instance_id",  sa.String(200), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.UniqueConstraint("group_id", "topic", "partition", name="uq_consumer_state"),
    )
    op.create_index("ix_cgs_group_topic",    "consumer_group_state", ["group_id", "topic"])
    op.create_index("ix_cgs_last_heartbeat", "consumer_group_state", ["last_heartbeat"])

    # ── circuit_breaker_state ─────────────────────────────────────────────────
    op.create_table(
        "circuit_breaker_state",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("circuit_name",   sa.String(100), unique=True, nullable=False),
        sa.Column(
            "state",
            sa.String(20),
            nullable=False,
            server_default="CLOSED",
        ),  # CLOSED | OPEN | HALF_OPEN
        sa.Column("failure_count",   sa.Integer(),   nullable=False, server_default="0"),
        sa.Column("last_failure_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("opened_at",       sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )

    # ── dlq_events ────────────────────────────────────────────────────────────
    op.create_table(
        "dlq_events",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("event_id",          sa.String(100), unique=True, nullable=False),
        sa.Column("original_topic",    sa.String(200), nullable=False),
        sa.Column("original_partition",sa.Integer(),   nullable=True),
        sa.Column("original_offset",   sa.BigInteger(),nullable=True),
        sa.Column("original_payload",  sa.Text(),      nullable=False),
        sa.Column("error_type",        sa.String(200), nullable=True),
        sa.Column("error_message",     sa.Text(),      nullable=True),
        sa.Column("stack_trace",       sa.Text(),      nullable=True),
        sa.Column("retry_count",       sa.Integer(),   nullable=False, server_default="0"),
        sa.Column("consumer_group",    sa.String(200), nullable=True),
        sa.Column("doc_id",            sa.String(32),  nullable=True),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="pending",
        ),  # pending | reprocessing | resolved | dismissed
        sa.Column("dismissal_reason",  sa.Text(),      nullable=True),
        sa.Column(
            "resolved_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("resolved_at",  sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at",    sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index("ix_dlq_events_doc_id",       "dlq_events", ["doc_id"])
    op.create_index("ix_dlq_events_original_topic","dlq_events", ["original_topic"])
    op.create_index("ix_dlq_events_status",        "dlq_events", ["status"])
    op.create_index("ix_dlq_events_failed_at",     "dlq_events", ["failed_at"])


def downgrade() -> None:
    op.drop_table("dlq_events")
    op.drop_table("circuit_breaker_state")
    op.drop_table("consumer_group_state")

    # Drop all partition children before the parent
    for year in (2024, 2025, 2026):
        for month in range(1, 13):
            op.execute(f"DROP TABLE IF EXISTS kafka_events_{year}_{month:02d} CASCADE")

    op.execute("DROP TABLE IF EXISTS kafka_events CASCADE")
