"""Initial schema — users, submissions, audit_log, rate_limit_violations.

Revision ID: 0001
Revises: (none)
Create Date: 2024-01-01 00:00:00.000000 UTC

This migration:
1. Creates all ENUM types
2. Creates all tables with constraints and indexes
3. REVOKEs UPDATE and DELETE on audit_log from the app user
   to enforce the append-only tamper-evident log.
"""

from __future__ import annotations

import os

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | None = None
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | None = None

# The database user the application runs as — must match POSTGRES_USER
APP_DB_USER = os.environ.get("POSTGRES_USER", "swastha-ai")


def upgrade() -> None:
    # ── ENUM Types ─────────────────────────────────────────────────────────────
    user_role_enum = postgresql.ENUM(
        "admin", "reviewer", "portal_operator", "api_client",
        name="user_role_enum",
    )
    submission_type_enum = postgresql.ENUM(
        "drug", "medical_device", "clinical_trial", "sae",
        name="submission_type_enum",
    )
    portal_source_enum = postgresql.ENUM(
        "sugam", "md_online", "manual", "sae_feed",
        name="portal_source_enum",
    )
    submission_status_enum = postgresql.ENUM(
        "ingested", "queued", "preprocessing", "processed", "failed", "rejected",
        name="submission_status_enum",
    )

    user_role_enum.create(op.get_bind(), checkfirst=True)
    submission_type_enum.create(op.get_bind(), checkfirst=True)
    portal_source_enum.create(op.get_bind(), checkfirst=True)
    submission_status_enum.create(op.get_bind(), checkfirst=True)

    # ── users ──────────────────────────────────────────────────────────────────
    op.create_table(
        "users",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("keycloak_id", sa.String(200), unique=True, nullable=False),
        sa.Column("email", sa.String(300), unique=True, nullable=False),
        sa.Column("full_name", sa.String(300), nullable=False, server_default=""),
        sa.Column(
            "role",
            postgresql.ENUM(
                "admin", "reviewer", "portal_operator", "api_client",
                name="user_role_enum",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index("ix_users_keycloak_id", "users", ["keycloak_id"])
    op.create_index("ix_users_email", "users", ["email"])

    # ── submissions ────────────────────────────────────────────────────────────
    op.create_table(
        "submissions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("doc_id", sa.String(32), unique=True, nullable=False),
        sa.Column(
            "submission_type",
            postgresql.ENUM(
                "drug", "medical_device", "clinical_trial", "sae",
                name="submission_type_enum",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "portal_source",
            postgresql.ENUM(
                "sugam", "md_online", "manual", "sae_feed",
                name="portal_source_enum",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("external_id", sa.String(100), nullable=True),
        sa.Column("original_filename", sa.String(500), nullable=False),
        sa.Column("mime_type", sa.String(100), nullable=False),
        sa.Column("file_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("checksum_sha256", sa.String(64), unique=True, nullable=False),
        sa.Column("raw_storage_path", sa.Text(), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(
                "ingested", "queued", "preprocessing", "processed", "failed", "rejected",
                name="submission_status_enum",
                create_type=False,
            ),
            nullable=False,
            server_default="ingested",
        ),
        sa.Column(
            "submitted_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("ip_address", postgresql.INET(), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column(
            "metadata",
            postgresql.JSONB(),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.UniqueConstraint("checksum_sha256", name="uq_submission_checksum"),
        sa.UniqueConstraint("doc_id", name="uq_submission_doc_id"),
    )
    op.create_index("ix_submissions_status", "submissions", ["status"])
    op.create_index("ix_submissions_submitted_by", "submissions", ["submitted_by"])
    op.create_index("ix_submissions_created_at", "submissions", ["created_at"])
    op.create_index("ix_submissions_portal_source", "submissions", ["portal_source"])

    # Trigger to auto-update updated_at column
    op.execute("""
        CREATE OR REPLACE FUNCTION update_updated_at_column()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = NOW();
            RETURN NEW;
        END;
        $$ language 'plpgsql';
    """)
    op.execute("""
        CREATE TRIGGER submissions_updated_at
            BEFORE UPDATE ON submissions
            FOR EACH ROW
            EXECUTE FUNCTION update_updated_at_column();
    """)

    # ── audit_log ──────────────────────────────────────────────────────────────
    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("doc_id", sa.String(32), nullable=True),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_email", sa.String(300), nullable=True),
        sa.Column("ip_address", postgresql.INET(), nullable=True),
        sa.Column("action_detail", postgresql.JSONB(), nullable=False),
        sa.Column("outcome", sa.String(20), nullable=False),
        sa.Column("entry_hash", sa.String(64), unique=True, nullable=False),
        sa.Column("prev_hash", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index("ix_audit_log_doc_id", "audit_log", ["doc_id"])
    op.create_index("ix_audit_log_actor_id", "audit_log", ["actor_id"])
    op.create_index("ix_audit_log_created_at", "audit_log", ["created_at"])
    op.create_index("ix_audit_log_event_type", "audit_log", ["event_type"])

    # REVOKE UPDATE and DELETE on audit_log from the app user
    # This enforces the tamper-evident, append-only constraint at the DB level.
    op.execute(f'REVOKE UPDATE, DELETE ON audit_log FROM "{APP_DB_USER}"')

    # ── rate_limit_violations ─────────────────────────────────────────────────
    op.create_table(
        "rate_limit_violations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("ip_address", postgresql.INET(), nullable=True),
        sa.Column("endpoint", sa.String(200), nullable=True),
        sa.Column("violation_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("last_violation_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("blocked_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_rlv_ip_address", "rate_limit_violations", ["ip_address"]
    )
    op.create_index(
        "ix_rlv_blocked_until", "rate_limit_violations", ["blocked_until"]
    )


def downgrade() -> None:
    # Drop in reverse dependency order
    op.drop_table("rate_limit_violations")

    # Restore REVOKE before dropping (grants it back so we can drop)
    APP_DB_USER_ = os.environ.get("POSTGRES_USER", "swastha-ai")
    op.execute(f'GRANT UPDATE, DELETE ON audit_log TO "{APP_DB_USER_}"')
    op.drop_table("audit_log")
    op.drop_table("submissions")
    op.drop_table("users")

    # Drop ENUM types
    op.execute("DROP FUNCTION IF EXISTS update_updated_at_column() CASCADE")
    op.execute("DROP TYPE IF EXISTS submission_status_enum")
    op.execute("DROP TYPE IF EXISTS portal_source_enum")
    op.execute("DROP TYPE IF EXISTS submission_type_enum")
    op.execute("DROP TYPE IF EXISTS user_role_enum")
