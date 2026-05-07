#!/usr/bin/env python3
"""
kafka/setup/verify_setup.py

Verifies that all required Kafka topics and Avro schemas exist and are
correctly configured. Designed to run as a Kubernetes/Docker init container
before any consumer or producer starts.

Exit codes:
  0 — everything is correct
  1 — one or more verification checks failed (details printed to stdout)

Usage:
    python kafka/setup/verify_setup.py
"""
from __future__ import annotations

import os
import sys
import time

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from kafka.admin import KafkaAdminClient
from kafka.errors import NoBrokersAvailable

from kafka.config.topic_configs import ALL_TOPICS, TOPIC_BY_NAME
from kafka.setup.create_schemas import SCHEMA_REGISTRY as SCHEMA_REGISTRY_MAP

BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
SCHEMA_REGISTRY_URL = os.getenv("SCHEMA_REGISTRY_URL", "http://localhost:8081").rstrip("/")
MAX_RETRIES = 6
RETRY_SLEEP = 5


def connect_admin() -> KafkaAdminClient:
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return KafkaAdminClient(
                bootstrap_servers=BOOTSTRAP_SERVERS,
                client_id="swastha-verifier",
                request_timeout_ms=10_000,
            )
        except NoBrokersAvailable:
            print(f"[WAIT] Kafka not ready (attempt {attempt}/{MAX_RETRIES})...")
            time.sleep(RETRY_SLEEP)
    print("[ERROR] Cannot connect to Kafka")
    sys.exit(1)


def verify_topics(admin: KafkaAdminClient) -> list[str]:
    """
    Returns a list of error strings. Empty list = all OK.
    """
    errors: list[str] = []

    try:
        existing_topics = set(admin.list_topics())
        topic_metadata = admin.describe_topics(list(existing_topics))
    except Exception as exc:
        return [f"Cannot fetch topic metadata: {exc}"]

    meta_by_name = {t["topic"]: t for t in topic_metadata}

    for topic_cfg in ALL_TOPICS:
        name = topic_cfg.name
        if name not in existing_topics:
            errors.append(f"MISSING topic: {name}")
            continue

        meta = meta_by_name.get(name, {})
        actual_partitions = len(meta.get("partitions", []))
        if actual_partitions != topic_cfg.partitions:
            errors.append(
                f"PARTITION MISMATCH {name}: expected={topic_cfg.partitions}, actual={actual_partitions}"
            )

        # Replication factor check (from first partition)
        partitions = meta.get("partitions", [])
        if partitions:
            actual_rf = len(partitions[0].get("replicas", []))
            if actual_rf != topic_cfg.replication_factor:
                errors.append(
                    f"REPLICATION MISMATCH {name}: expected={topic_cfg.replication_factor}, actual={actual_rf}"
                )

    return errors


def verify_schemas() -> list[str]:
    """Check each registered subject exists in the Schema Registry."""
    errors: list[str] = []

    try:
        r = requests.get(f"{SCHEMA_REGISTRY_URL}/subjects", timeout=5)
        if r.status_code != 200:
            return [f"Schema Registry returned {r.status_code}"]
        registered_subjects = set(r.json())
    except Exception as exc:
        return [f"Cannot reach Schema Registry: {exc}"]

    for topic_name, _ in SCHEMA_REGISTRY_MAP:
        subject = f"{topic_name}-value"
        if subject not in registered_subjects:
            errors.append(f"MISSING schema subject: {subject}")

    return errors


def main() -> None:
    print("=" * 60)
    print("SwasthaAI — Kafka Setup Verification")
    print(f"Kafka:           {BOOTSTRAP_SERVERS}")
    print(f"Schema Registry: {SCHEMA_REGISTRY_URL}")
    print("=" * 60)

    all_errors: list[str] = []

    # Verify topics
    admin = connect_admin()
    try:
        topic_errors = verify_topics(admin)
    finally:
        admin.close()

    if topic_errors:
        print(f"\n[FAIL] Topic verification: {len(topic_errors)} error(s)")
        for err in topic_errors:
            print(f"  - {err}")
        all_errors.extend(topic_errors)
    else:
        print(f"\n[OK] All {len(ALL_TOPICS)} topics verified.")

    # Verify schemas
    schema_errors = verify_schemas()
    if schema_errors:
        print(f"\n[FAIL] Schema verification: {len(schema_errors)} error(s)")
        for err in schema_errors:
            print(f"  - {err}")
        all_errors.extend(schema_errors)
    else:
        print(f"[OK] All schema subjects verified.")

    if all_errors:
        print(f"\n[FAIL] Verification complete — {len(all_errors)} total error(s). Exiting with code 1.")
        sys.exit(1)

    print("\n[OK] All checks passed. Platform is ready to start.")
    sys.exit(0)


if __name__ == "__main__":
    main()
