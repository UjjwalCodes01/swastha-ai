"""
SQLAlchemy ORM models for the SwasthaAI ingestion layer.

These models mirror the PostgreSQL tables defined in the Alembic migration.
All UUIDs are generated server-side via gen_random_uuid() or Python uuid4.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# ── ENUM Definitions ──────────────────────────────────────────────────────────
# Using Python enums for type safety; Alembic creates the matching PG ENUM types.

import enum


INET_TYPE = INET().with_variant(String(45), "sqlite")
JSON_TYPE = JSON().with_variant(JSONB(), "postgresql")
UUID_TYPE = Uuid(as_uuid=True)


class SubmissionTypeEnum(str, enum.Enum):
    drug = "drug"
    medical_device = "medical_device"
    clinical_trial = "clinical_trial"
    sae = "sae"


class PortalSourceEnum(str, enum.Enum):
    sugam = "sugam"
    md_online = "md_online"
    manual = "manual"
    sae_feed = "sae_feed"


class SubmissionStatusEnum(str, enum.Enum):
    ingested = "ingested"
    queued = "queued"
    preprocessing = "preprocessing"
    processed = "processed"
    failed = "failed"
    rejected = "rejected"


class UserRoleEnum(str, enum.Enum):
    admin = "admin"
    reviewer = "reviewer"
    portal_operator = "portal_operator"
    api_client = "api_client"


# ── Base ──────────────────────────────────────────────────────────────────────


class Base(DeclarativeBase):
    pass


# ── Models ────────────────────────────────────────────────────────────────────


class User(Base):
    """
    Represents an authenticated user synced from Keycloak.

    Users are auto-created on first login using the Keycloak JWT claims.
    The keycloak_id is the stable identifier across Keycloak restarts.
    """

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, primary_key=True, default=uuid.uuid4
    )
    keycloak_id: Mapped[str] = mapped_column(
        String(200), unique=True, nullable=False, index=True
    )
    email: Mapped[str] = mapped_column(String(300), unique=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    role: Mapped[UserRoleEnum] = mapped_column(
        Enum(UserRoleEnum, name="user_role_enum"), nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationship back to submissions
    submissions: Mapped[list["Submission"]] = relationship(
        "Submission", back_populates="submitter", lazy="noload"
    )

    def __repr__(self) -> str:
        return f"<User id={self.id} email={self.email} role={self.role}>"


class Submission(Base):
    """
    The central record for every document ingested into the system.

    Immutable once created (status updates are the only allowed mutations).
    MinIO path and checksum are permanent references for downstream processing.
    """

    __tablename__ = "submissions"
    __table_args__ = (
        UniqueConstraint("checksum_sha256", name="uq_submission_checksum"),
        UniqueConstraint("doc_id", name="uq_submission_doc_id"),
        Index("ix_submissions_status", "status"),
        Index("ix_submissions_submitted_by", "submitted_by"),
        Index("ix_submissions_created_at", "created_at"),
        Index("ix_submissions_portal_source", "portal_source"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, primary_key=True, default=uuid.uuid4
    )
    doc_id: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    submission_type: Mapped[SubmissionTypeEnum] = mapped_column(
        Enum(SubmissionTypeEnum, name="submission_type_enum"), nullable=False
    )
    portal_source: Mapped[PortalSourceEnum] = mapped_column(
        Enum(PortalSourceEnum, name="portal_source_enum"), nullable=False
    )
    external_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    original_filename: Mapped[str] = mapped_column(String(500), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    checksum_sha256: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False
    )
    raw_storage_path: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[SubmissionStatusEnum] = mapped_column(
        Enum(SubmissionStatusEnum, name="submission_status_enum"),
        nullable=False,
        default=SubmissionStatusEnum.ingested,
    )
    submitted_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    ip_address: Mapped[str | None] = mapped_column(INET_TYPE, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_: Mapped[dict] = mapped_column(
        "metadata", JSON_TYPE, nullable=False, default=dict, server_default="{}"
    )
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    submitter: Mapped["User | None"] = relationship(
        "User", back_populates="submissions", lazy="noload"
    )

    def __repr__(self) -> str:
        return f"<Submission doc_id={self.doc_id} status={self.status}>"


class AuditLog(Base):
    """
    Tamper-evident audit log.

    Each entry includes the SHA-256 hash of the previous entry (chain hashing).
    UPDATE and DELETE are revoked from the app DB user in the migration,
    making this log append-only at the database level.
    """

    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_log_doc_id", "doc_id"),
        Index("ix_audit_log_actor_id", "actor_id"),
        Index("ix_audit_log_created_at", "created_at"),
        Index("ix_audit_log_event_type", "event_type"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    doc_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE, nullable=True
    )
    actor_email: Mapped[str | None] = mapped_column(String(300), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(INET_TYPE, nullable=True)
    action_detail: Mapped[dict] = mapped_column(JSON_TYPE, nullable=False)
    outcome: Mapped[str] = mapped_column(String(20), nullable=False)
    entry_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    prev_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<AuditLog id={self.id} event={self.event_type} outcome={self.outcome}>"


class RateLimitViolation(Base):
    """
    Tracks rate limit violations per IP for progressive blocking.

    After 10 violations in 1 hour, the IP is blocked for 24 hours.
    """

    __tablename__ = "rate_limit_violations"
    __table_args__ = (
        Index("ix_rlv_ip_address", "ip_address"),
        Index("ix_rlv_blocked_until", "blocked_until"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, primary_key=True, default=uuid.uuid4
    )
    ip_address: Mapped[str | None] = mapped_column(INET_TYPE, nullable=True)
    endpoint: Mapped[str | None] = mapped_column(String(200), nullable=True)
    violation_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    last_violation_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    blocked_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:
        return f"<RateLimitViolation ip={self.ip_address} count={self.violation_count}>"
