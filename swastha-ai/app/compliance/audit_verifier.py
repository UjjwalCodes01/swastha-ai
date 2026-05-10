"""Tamper-evident audit-chain verification."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text

GENESIS_HASH = "0" * 64


class AuditChainVerifier:
    """Verifies prev_hash links and entry hashes in audit_log."""

    async def verify(self, session_factory: Any, limit: int = 1000) -> dict:
        async with session_factory() as session:
            rows = (
                await session.execute(
                    text("""
                        SELECT id, event_type, outcome, action_detail, doc_id, actor_id,
                               actor_email, ip_address, entry_hash, prev_hash, created_at
                        FROM audit_log
                        ORDER BY id ASC
                        LIMIT :limit
                    """),
                    {"limit": limit},
                )
            ).mappings().all()

        previous = GENESIS_HASH
        checked = 0
        failures = []
        for row in rows:
            if row["prev_hash"] != previous:
                failures.append({"id": str(row["id"]), "reason": "prev_hash_mismatch"})
            if not row["entry_hash"] or len(row["entry_hash"]) != 64:
                failures.append({"id": str(row["id"]), "reason": "invalid_entry_hash_format"})
            previous = row["entry_hash"]
            checked += 1
        return {
            "checked": checked,
            "valid": not failures,
            "failures": failures[:50],
            "note": "Verifies hash linkage and hash format. Full entry recomputation requires storing the original audit event timestamp.",
        }
