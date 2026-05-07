"""
Kafka Admin — topic and consumer group management.
"""
from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

try:
    from kafka import KafkaAdminClient, KafkaConsumer
    from kafka.admin import NewTopic, ConfigResource, ConfigResourceType
    from kafka.errors import TopicAlreadyExistsError
    from kafka.structs import TopicPartition as TP
except ImportError:  # pragma: no cover
    KafkaAdminClient = None  # type: ignore[assignment, misc]
    NewTopic = None  # type: ignore[assignment, misc]
    ConfigResource = None  # type: ignore[assignment]
    ConfigResourceType = None  # type: ignore[assignment]
    TopicAlreadyExistsError = Exception  # type: ignore[assignment, misc]
    TP = None  # type: ignore[assignment]

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from kafka.config.topic_configs import ALL_TOPICS, TOPIC_BY_NAME

_ENVIRONMENT = os.getenv("ENVIRONMENT", "development")


class StartupError(RuntimeError):
    """Raised when required Kafka topics are missing or misconfigured."""


class AdminClient:
    def __init__(self, bootstrap_servers: str) -> None:
        self._bootstrap = bootstrap_servers

    def _client(self) -> Any:
        return KafkaAdminClient(
            bootstrap_servers=self._bootstrap,
            client_id="swastha-admin",
            request_timeout_ms=30_000,
        )

    def create_topics(self) -> dict[str, str]:
        """Idempotent topic creation. Returns status per topic."""
        client = self._client()
        try:
            existing = set(client.list_topics())
            to_create = [
                NewTopic(
                    name=t.name,
                    num_partitions=t.partitions,
                    replication_factor=t.replication_factor,
                    topic_configs=t.to_kafka_dict(),
                )
                for t in ALL_TOPICS if t.name not in existing
            ]
            results: dict[str, str] = {t.name: "skipped" for t in ALL_TOPICS if t.name in existing}
            if to_create:
                for name, future in client.create_topics(to_create, validate_only=False).items():
                    try:
                        future.result()
                        results[name] = "created"
                        logger.info(f"Created topic: {name}")
                    except TopicAlreadyExistsError:
                        results[name] = "skipped"
                    except Exception as exc:
                        results[name] = f"failed: {exc}"
                        logger.error(f"Failed to create {name}: {exc}")
            return results
        finally:
            client.close()

    def verify_topics(self) -> None:
        """Verify all topics exist with correct partition count. Raises StartupError."""
        client = self._client()
        try:
            existing = set(client.list_topics())
            meta_by_name = {t["topic"]: t for t in client.describe_topics(list(existing))}
            errors: list[str] = []
            for topic_cfg in ALL_TOPICS:
                if topic_cfg.name not in existing:
                    errors.append(f"Missing: {topic_cfg.name}")
                    continue
                actual = len(meta_by_name.get(topic_cfg.name, {}).get("partitions", []))
                if actual != topic_cfg.partitions:
                    errors.append(f"Partition mismatch {topic_cfg.name}: expected={topic_cfg.partitions}, actual={actual}")
            if errors:
                raise StartupError("Topic verification failed:\n" + "\n".join(f"  - {e}" for e in errors))
            logger.info(f"All {len(ALL_TOPICS)} topics verified.")
        finally:
            client.close()

    def alter_topic_config(self, topic: str, config_key: str, config_value: str) -> None:
        client = self._client()
        try:
            resource = ConfigResource(ConfigResourceType.TOPIC, topic)
            client.alter_configs({resource: {config_key: config_value}})
            logger.info("Topic config altered", extra={"topic": topic, "key": config_key, "value": config_value})
        finally:
            client.close()

    def get_topic_stats(self, topic: str) -> dict:
        client = self._client()
        try:
            meta_list = client.describe_topics([topic])
            meta = meta_list[0] if meta_list else {}
            partitions = meta.get("partitions", [])
            return {
                "topic": topic,
                "partition_count": len(partitions),
                "replication_factor": len(partitions[0]["replicas"]) if partitions else 0,
                "partitions": [
                    {"partition": p["partition"], "leader": p.get("leader"), "replicas": p.get("replicas", [])}
                    for p in partitions
                ],
            }
        finally:
            client.close()

    def delete_topic(self, topic: str, confirmation_token: str) -> None:
        if _ENVIRONMENT.lower() == "production":
            raise PermissionError("Topic deletion blocked in production.")
        if confirmation_token != f"DELETE:{topic}":
            raise ValueError(f"Invalid confirmation token. Expected: 'DELETE:{topic}'")
        client = self._client()
        try:
            client.delete_topics([topic])
            logger.warning(f"Topic '{topic}' deleted by admin")
        finally:
            client.close()

    def reset_consumer_offset(
        self,
        topic: str,
        group_id: str,
        partition: int,
        offset_strategy: str,
        specific_offset: int | None = None,
        specific_timestamp_ms: int | None = None,
    ) -> int:
        """Reset consumer group offset. Returns the new offset."""
        consumer = KafkaConsumer(group_id=group_id, bootstrap_servers=self._bootstrap, enable_auto_commit=False)
        tp = TP(topic, partition)
        consumer.assign([tp])
        try:
            if offset_strategy == "earliest":
                consumer.seek_to_beginning(tp)
            elif offset_strategy == "latest":
                consumer.seek_to_end(tp)
            elif offset_strategy == "specific_offset" and specific_offset is not None:
                consumer.seek(tp, specific_offset)
            elif offset_strategy == "specific_timestamp" and specific_timestamp_ms is not None:
                offsets = consumer.offsets_for_times({tp: specific_timestamp_ms})
                off = offsets[tp].offset if offsets.get(tp) else 0
                consumer.seek(tp, off)
            else:
                raise ValueError(f"Unknown offset strategy: {offset_strategy}")
            new_offset = consumer.position(tp)
            consumer.commit({tp: new_offset})
            logger.info("Consumer offset reset", extra={"topic": topic, "group": group_id, "partition": partition, "new_offset": new_offset})
            return new_offset
        finally:
            consumer.close()
