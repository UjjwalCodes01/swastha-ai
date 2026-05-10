"""Layer 4 - Compliance and governance tables.

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-10 00:00:00.000000 UTC

This migration creates:
  1. compliance_assessments - DPDP/ICMR/NDHM governance decisions
  2. xai_decision_log       - explainable AI records for every AI output
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "compliance_assessments",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("doc_id", sa.String(32), nullable=True),
        sa.Column("source_event", sa.String(200), nullable=False),
        sa.Column("decision", sa.String(30), nullable=False),
        sa.Column("frameworks_checked", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("findings", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("human_review_required", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("blocked", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("assessed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.CheckConstraint("decision IN ('pass','review_required','blocked')", name="chk_compliance_decision"),
    )
    op.create_index("ix_compliance_doc_id", "compliance_assessments", ["doc_id"])
    op.create_index("ix_compliance_decision", "compliance_assessments", ["decision"])
    op.create_index("ix_compliance_source_event", "compliance_assessments", ["source_event"])
    op.create_index("ix_compliance_assessed_at", "compliance_assessments", ["assessed_at"])

    op.create_table(
        "xai_decision_log",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("doc_id", sa.String(32), nullable=True),
        sa.Column("module_name", sa.String(100), nullable=False),
        sa.Column("model_id", sa.String(200), nullable=False),
        sa.Column("model_version", sa.String(100), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("decision_summary", sa.Text(), nullable=False),
        sa.Column("input_refs", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("output_refs", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("rationale", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_index("ix_xai_doc_id", "xai_decision_log", ["doc_id"])
    op.create_index("ix_xai_module", "xai_decision_log", ["module_name"])
    op.create_index("ix_xai_created_at", "xai_decision_log", ["created_at"])


def downgrade() -> None:
    op.drop_table("xai_decision_log")
    op.drop_table("compliance_assessments")
