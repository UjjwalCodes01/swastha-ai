#!/usr/bin/env python3
"""
kafka/setup/create_topics.py

Idempotent topic creation script. Creates all topics defined in
kafka/config/topic_configs.py.

Safe to run multiple times — skips topics that already exist.
Run as a Docker init container before starting any consumer or producer.

Usage:
    python kafka/setup/create_topics.py

Environment variables:
    KAFKA_BOOTSTRAP_SERVERS  — default: localhost:9092
"""
from __future__ import annotations

import os
import sys
import time

# Ensure project root is on sys.path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from kafka.admin import KafkaAdminClient, NewTopic
from kafka.errors import TopicAlreadyExistsError, NoBrokersAvailable

from kafka.config.topic_configs import ALL_TOPICS

BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
MAX_RETRIES = 10
RETRY_SLEEP_SECONDS = 5


def wait_for_broker() -> KafkaAdminClient:
    """Retry until broker is reachable (used during container startup race)."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            admin = KafkaAdminClient(
                bootstrap_servers=BOOTSTRAP_SERVERS,
                client_id="swastha-topic-creator",
                request_timeout_ms=10_000,
            )
            print(f"[OK] Connected to Kafka at {BOOTSTRAP_SERVERS}")
            return admin
        except NoBrokersAvailable:
            print(f"[WAIT] Broker not ready (attempt {attempt}/{MAX_RETRIES}), sleeping {RETRY_SLEEP_SECONDS}s...")
            time.sleep(RETRY_SLEEP_SECONDS)
    print(f"[ERROR] Kafka broker unreachable after {MAX_RETRIES} attempts. Exiting.")
    sys.exit(1)


def get_existing_topics(admin: KafkaAdminClient) -> set[str]:
    """Fetch the set of existing topic names from the broker."""
    metadata = admin.list_topics()
    return set(metadata)


def create_topics(admin: KafkaAdminClient) -> None:
    existing = get_existing_topics(admin)

    to_create: list[NewTopic] = []
    skipped: list[str] = []

    for topic_cfg in ALL_TOPICS:
        if topic_cfg.name in existing:
            skipped.append(topic_cfg.name)
            continue
        to_create.append(
            NewTopic(
                name=topic_cfg.name,
                num_partitions=topic_cfg.partitions,
                replication_factor=topic_cfg.replication_factor,
                topic_configs=topic_cfg.to_kafka_dict(),
            )
        )

    if skipped:
        print(f"\n[SKIP] {len(skipped)} topics already exist:")
        for name in skipped:
            print(f"       - {name}")

    if not to_create:
        print("\n[OK] All topics already exist. Nothing to create.")
        return

    print(f"\n[CREATE] Creating {len(to_create)} topics...")
    results = admin.create_topics(new_topics=to_create, validate_only=False)

    success = 0
    failures = 0
    for name, future in results.items():
        try:
            future.result()
            print(f"  [OK]   {name}")
            success += 1
        except TopicAlreadyExistsError:
            print(f"  [SKIP] {name} (already existed)")
        except Exception as exc:
            print(f"  [FAIL] {name}: {exc}")
            failures += 1

    print(f"\nSummary: {success} created, {len(skipped)} skipped, {failures} failed")
    if failures > 0:
        sys.exit(1)


def main() -> None:
    print("=" * 60)
    print("SwasthaAI — Kafka Topic Creation")
    print(f"Bootstrap: {BOOTSTRAP_SERVERS}")
    print(f"Total topics to ensure: {len(ALL_TOPICS)}")
    print("=" * 60)

    admin = wait_for_broker()
    try:
        create_topics(admin)
    finally:
        admin.close()

    print("\n[DONE] Topic creation complete.")


if __name__ == "__main__":
    main()
