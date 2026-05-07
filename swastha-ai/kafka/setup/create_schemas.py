#!/usr/bin/env python3
"""
kafka/setup/create_schemas.py

Registers all Avro schemas with the Confluent Schema Registry.
Idempotent — safe to run multiple times. Already-registered schemas
are compared by content; identical schemas are skipped.

Sets BACKWARD compatibility on every subject so that:
  - New fields must have defaults
  - Fields may not be removed
  - Types may not change
This prevents any producer from breaking existing consumers.

Usage:
    python kafka/setup/create_schemas.py

Environment variables:
    SCHEMA_REGISTRY_URL — default: http://localhost:8081
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import requests

SCHEMA_REGISTRY_URL = os.getenv("SCHEMA_REGISTRY_URL", "http://localhost:8081").rstrip("/")
SCHEMAS_DIR = Path(__file__).parent.parent / "schemas"
MAX_RETRIES = 10
RETRY_SLEEP_SECONDS = 5

# Map topic name → Avro schema filename + Schema Registry subject name.
# Subject follows the <topic>-value convention used by Confluent tooling.
SCHEMA_REGISTRY = [
    ("raw.documents.ingested",            "raw_document_ingested.avsc"),
    ("documents.preprocessed",           "document_preprocessed.avsc"),
    ("documents.chunks.ready",           "document_chunks_ready.avsc"),
    ("documents.anonymised",             "document_anonymised.avsc"),
    ("documents.summarised",             "document_summarised.avsc"),
    ("documents.classified",             "document_classified.avsc"),
    ("documents.comparison.requested",   "document_comparison_requested.avsc"),
    ("reports.generated",                "report_generated.avsc"),
    ("notifications.events",             "notification_event.avsc"),
    # DLQ topics all share the same schema
    ("raw.documents.ingested.dlq",       "dlq_event.avsc"),
    ("documents.preprocessed.dlq",      "dlq_event.avsc"),
    ("documents.anonymised.dlq",         "dlq_event.avsc"),
    ("documents.summarised.dlq",         "dlq_event.avsc"),
    ("documents.classified.dlq",         "dlq_event.avsc"),
    ("reports.generated.dlq",           "dlq_event.avsc"),
]


def wait_for_registry() -> None:
    """Retry until Schema Registry is reachable."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = requests.get(f"{SCHEMA_REGISTRY_URL}/subjects", timeout=5)
            if r.status_code == 200:
                print(f"[OK] Schema Registry reachable at {SCHEMA_REGISTRY_URL}")
                return
        except requests.ConnectionError:
            pass
        print(f"[WAIT] Schema Registry not ready (attempt {attempt}/{MAX_RETRIES}), sleeping {RETRY_SLEEP_SECONDS}s...")
        time.sleep(RETRY_SLEEP_SECONDS)
    print("[ERROR] Schema Registry unreachable. Exiting.")
    sys.exit(1)


def set_compatibility(subject: str) -> None:
    """Enforce BACKWARD compatibility on a subject."""
    url = f"{SCHEMA_REGISTRY_URL}/config/{subject}"
    r = requests.put(url, json={"compatibility": "BACKWARD"}, timeout=10)
    if r.status_code not in (200, 422):  # 422 = already set
        print(f"  [WARN] Could not set compatibility for {subject}: {r.status_code}")


def register_schema(subject: str, schema_path: Path) -> str:
    """
    Register a schema and return 'registered', 'existing', or 'failed'.
    """
    schema_str = schema_path.read_text(encoding="utf-8")
    # Validate JSON
    try:
        json.loads(schema_str)
    except json.JSONDecodeError as exc:
        print(f"  [FAIL] {schema_path.name} is not valid JSON: {exc}")
        return "failed"

    url = f"{SCHEMA_REGISTRY_URL}/subjects/{subject}/versions"
    payload = {"schema": schema_str, "schemaType": "AVRO"}

    # Check if already registered
    check_url = f"{SCHEMA_REGISTRY_URL}/subjects/{subject}"
    check = requests.post(check_url, json=payload, timeout=10)
    if check.status_code == 200:
        return "existing"

    # Register new schema
    r = requests.post(url, json=payload, timeout=10)
    if r.status_code in (200, 201):
        set_compatibility(subject)
        return "registered"
    else:
        print(f"  [FAIL] Schema Registry returned {r.status_code}: {r.text[:200]}")
        return "failed"


def main() -> None:
    print("=" * 60)
    print("SwasthaAI — Avro Schema Registration")
    print(f"Registry: {SCHEMA_REGISTRY_URL}")
    print(f"Schemas dir: {SCHEMAS_DIR}")
    print("=" * 60)

    wait_for_registry()

    registered = existing = failed = 0

    for topic_name, schema_file in SCHEMA_REGISTRY:
        subject = f"{topic_name}-value"
        schema_path = SCHEMAS_DIR / schema_file

        if not schema_path.exists():
            print(f"  [FAIL] Schema file not found: {schema_path}")
            failed += 1
            continue

        status = register_schema(subject, schema_path)
        if status == "registered":
            print(f"  [OK]   {subject}")
            registered += 1
        elif status == "existing":
            print(f"  [SKIP] {subject} (already registered)")
            existing += 1
        else:
            failed += 1

    print(f"\nSummary: {registered} registered, {existing} skipped, {failed} failed")
    if failed > 0:
        sys.exit(1)
    print("\n[DONE] Schema registration complete.")


if __name__ == "__main__":
    main()
