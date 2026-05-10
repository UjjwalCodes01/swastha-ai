"""Layer 5 — Audit log immutability at database level.

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-10 00:00:00.000000 UTC

This migration makes the audit_log table truly append-only at the PostgreSQL level:
  1. Revokes UPDATE and DELETE from the application user (swastha_app)
  2. Creates a trigger that prevents any row modification attempt
     (belt-and-suspenders: works even if connection is made as a superuser)

Per the architecture spec (layers doc):
  "The audit log table should have update and delete permissions revoked at the
   database level for the application user. The application can only insert.
   Not even a bug in your code can modify historical records."
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | None = None

# ── Application DB user ────────────────────────────────────────────────────────
# Change this to your actual application DB user if different.
# In Docker Compose this defaults to the superuser — set APP_DB_USER env var
# and the migration will use it.
import os
_APP_USER = os.environ.get("APP_DB_USER", "swastha_app")


def upgrade() -> None:
    # 1. Revoke UPDATE and DELETE on audit_log from the application user
    #    (Use DO block so this doesn't fail if the role doesn't exist yet)
    op.execute(f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{_APP_USER}') THEN
                REVOKE UPDATE, DELETE ON TABLE audit_log FROM "{_APP_USER}";
            END IF;
        END;
        $$;
    """)

    # 2. Create immutability trigger function (belt-and-suspenders protection)
    op.execute("""
        CREATE OR REPLACE FUNCTION fn_audit_log_immutable()
        RETURNS TRIGGER LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION
                'audit_log is immutable: UPDATE and DELETE are prohibited. '
                'This table is an append-only tamper-evident audit log. '
                'Operation attempted by role: %', current_user;
        END;
        $$;
    """)

    # 3. Attach trigger for UPDATE attempts
    op.execute("DROP TRIGGER IF EXISTS trg_audit_log_no_update ON audit_log;")
    op.execute("""
        CREATE TRIGGER trg_audit_log_no_update
        BEFORE UPDATE ON audit_log
        FOR EACH ROW EXECUTE FUNCTION fn_audit_log_immutable();
    """)

    # 4. Attach trigger for DELETE attempts
    op.execute("DROP TRIGGER IF EXISTS trg_audit_log_no_delete ON audit_log;")
    op.execute("""
        CREATE TRIGGER trg_audit_log_no_delete
        BEFORE DELETE ON audit_log
        FOR EACH ROW EXECUTE FUNCTION fn_audit_log_immutable();
    """)

    # 5. Add a DB-level check: entry_hash must be unique (already is, but make explicit)
    #    This ensures each audit entry is unique and cannot be silently overwritten.
    op.execute("""
        ALTER TABLE audit_log
            ALTER COLUMN entry_hash SET NOT NULL;
    """)

    # 6. Add NOT NULL constraint on prev_hash
    op.execute("""
        ALTER TABLE audit_log
            ALTER COLUMN prev_hash SET NOT NULL;
    """)

    # 7. Add an index on entry_hash for chain verification queries
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_audit_log_entry_hash ON audit_log (entry_hash);
    """)


def downgrade() -> None:
    # Remove triggers
    op.execute("DROP TRIGGER IF EXISTS trg_audit_log_no_update ON audit_log;")
    op.execute("DROP TRIGGER IF EXISTS trg_audit_log_no_delete ON audit_log;")
    op.execute("DROP FUNCTION IF EXISTS fn_audit_log_immutable();")

    # Re-grant UPDATE/DELETE (this undoes the governance protection — use with caution)
    op.execute(f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{_APP_USER}') THEN
                GRANT UPDATE, DELETE ON TABLE audit_log TO "{_APP_USER}";
            END IF;
        END;
        $$;
    """)
