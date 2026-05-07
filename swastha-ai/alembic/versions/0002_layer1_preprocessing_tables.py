"""Layer 1 — document_chunks, document_processing_log, submissions updates.

Revision ID: 0002
Revises: 0001
Create Date: 2024-01-02 00:00:00.000000 UTC

This migration:
1. Adds document_chunks table — stores chunk metadata (text lives in ChromaDB)
2. Adds document_processing_log table — per-run audit of the pipeline
3. Extends submissions table with three preprocessing columns
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    # ── document_chunks ────────────────────────────────────────────────────────
    # NOTE: chunk TEXT is NOT stored here — it lives in ChromaDB.
    # This table stores metadata for structured queries, pagination,
    # and linked-list traversal without hitting the vector store.
    op.create_table(
        "document_chunks",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("chunk_id", sa.String(60), unique=True, nullable=False),
        sa.Column(
            "doc_id",
            sa.String(32),
            sa.ForeignKey("submissions.doc_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("total_chunks", sa.Integer(), nullable=False),
        sa.Column("section_title", sa.String(500), nullable=True),
        sa.Column("page_range_start", sa.Integer(), nullable=True),
        sa.Column("page_range_end", sa.Integer(), nullable=True),
        # text | table | heading | form_field
        sa.Column("chunk_type", sa.String(20), nullable=False, server_default="text"),
        sa.Column("token_count", sa.Integer(), nullable=True),
        sa.Column("char_count", sa.Integer(), nullable=True),
        sa.Column("language", sa.String(10), nullable=True),
        sa.Column("chroma_collection", sa.String(100), nullable=True),
        sa.Column("prev_chunk_id", sa.String(60), nullable=True),
        sa.Column("next_chunk_id", sa.String(60), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index("ix_chunks_doc_id", "document_chunks", ["doc_id"])
    op.create_index("ix_chunks_chunk_id", "document_chunks", ["chunk_id"])
    op.create_index("ix_chunks_chunk_index", "document_chunks", ["chunk_index"])
    op.create_index("ix_chunks_chunk_type", "document_chunks", ["chunk_type"])

    # ── document_processing_log ────────────────────────────────────────────────
    # One row per pipeline run for a doc_id. Reprocessing produces a new row.
    # This is the auditability record for every preprocessing execution.
    op.create_table(
        "document_processing_log",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "doc_id",
            sa.String(32),
            sa.ForeignKey("submissions.doc_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("pipeline_version", sa.String(20), nullable=False),
        sa.Column("extraction_confidence", sa.Numeric(4, 3), nullable=True),
        sa.Column("ocr_used", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("ocr_pages", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("page_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("word_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("chunk_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("table_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("language", sa.String(10), nullable=True),
        sa.Column("extractors_used", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column(
            "normalisation_changes",
            postgresql.JSONB(),
            nullable=True,
            server_default="{}",
        ),
        sa.Column("flags", postgresql.JSONB(), nullable=True, server_default="{}"),
        sa.Column("processing_duration_ms", sa.Integer(), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index("ix_proc_log_doc_id", "document_processing_log", ["doc_id"])
    op.create_index("ix_proc_log_created_at", "document_processing_log", ["created_at"])

    # ── Extend submissions table ───────────────────────────────────────────────
    op.add_column(
        "submissions",
        sa.Column("preprocessing_confidence", sa.Numeric(4, 3), nullable=True),
    )
    op.add_column(
        "submissions",
        sa.Column("chunk_count", sa.Integer(), nullable=True),
    )
    op.add_column(
        "submissions",
        sa.Column("preprocessed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    # Remove new submissions columns
    op.drop_column("submissions", "preprocessed_at")
    op.drop_column("submissions", "chunk_count")
    op.drop_column("submissions", "preprocessing_confidence")

    # Drop new tables
    op.drop_table("document_processing_log")
    op.drop_table("document_chunks")
