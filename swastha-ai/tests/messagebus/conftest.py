"""
conftest.py for messagebus tests.

Provides:
  - kafka_producer_mock
  - kafka_consumer_mock
  - mock_schema_registry
  - mock_redis (fakeredis async)
  - mock_postgres (in-memory test session)
  - sample_raw_document_event
  - sample_preprocessed_event
  - sample_dlq_event
"""
from __future__ import annotations

import json
import struct
import time
import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio


# ── Redis mock ────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_redis():
    """In-memory async Redis mock using a plain dict."""
    store: dict[str, Any] = {}
    lists: dict[str, list] = {}

    redis = AsyncMock()

    async def set_impl(key, value, ex=None, **kwargs):
        store[key] = value

    async def get_impl(key):
        return store.get(key)

    async def delete_impl(*keys):
        for k in keys:
            store.pop(k, None)
            lists.pop(k, None)

    async def incr_impl(key):
        val = int(store.get(key, 0)) + 1
        store[key] = str(val)
        return val

    async def expire_impl(key, seconds):
        pass  # No-op for tests

    async def rpush_impl(key, *values):
        lists.setdefault(key, []).extend(values)
        return len(lists[key])

    async def lpop_impl(key):
        lst = lists.get(key, [])
        if not lst:
            return None
        return lst.pop(0)

    async def llen_impl(key):
        return len(lists.get(key, []))

    async def lpush_impl(key, *values):
        lst = lists.setdefault(key, [])
        for v in reversed(values):
            lst.insert(0, v)

    redis.set = AsyncMock(side_effect=set_impl)
    redis.get = AsyncMock(side_effect=get_impl)
    redis.delete = AsyncMock(side_effect=delete_impl)
    redis.incr = AsyncMock(side_effect=incr_impl)
    redis.expire = AsyncMock(side_effect=expire_impl)
    redis.rpush = AsyncMock(side_effect=rpush_impl)
    redis.lpop = AsyncMock(side_effect=lpop_impl)
    redis.llen = AsyncMock(side_effect=llen_impl)
    redis.lpush = AsyncMock(side_effect=lpush_impl)

    return redis


# ── PostgreSQL mock ───────────────────────────────────────────────────────────

@pytest.fixture
def mock_db_session():
    """Returns a session factory mock that records execute() calls."""
    session = AsyncMock()
    session.execute = AsyncMock(return_value=AsyncMock(fetchall=AsyncMock(return_value=[]), fetchone=AsyncMock(return_value=None), scalar=AsyncMock(return_value=0)))
    session.commit = AsyncMock()

    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=session)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)

    return factory, session


# ── Schema Registry mock ──────────────────────────────────────────────────────

def _make_wire_bytes(schema_id: int, payload: dict) -> bytes:
    """Simulate Confluent wire format encoding."""
    payload_bytes = json.dumps(payload).encode()
    header = struct.pack(">bI", 0x00, schema_id)
    return header + payload_bytes


def _parse_wire_bytes(data: bytes) -> tuple[int, dict]:
    """Simulate Confluent wire format decoding."""
    magic, schema_id = struct.unpack_from(">bI", data, 0)
    payload = json.loads(data[5:].decode())
    return schema_id, payload


@pytest.fixture
def mock_schema_registry():
    registry = MagicMock()
    registry.serialise = MagicMock(side_effect=lambda topic, payload: _make_wire_bytes(1, payload))
    registry.deserialise = MagicMock(side_effect=lambda data: _parse_wire_bytes(data)[1])
    return registry


# ── Kafka mocks ───────────────────────────────────────────────────────────────

@pytest.fixture
def kafka_producer_mock():
    producer = AsyncMock()
    producer.start = AsyncMock()
    producer.stop = AsyncMock()
    producer.flush = AsyncMock()
    producer.send_and_wait = AsyncMock(return_value=MagicMock(topic="test", partition=0, offset=42))
    return producer


@pytest.fixture
def kafka_consumer_mock():
    """A mock AIOKafkaConsumer that can be iterated."""
    consumer = AsyncMock()
    consumer.start = AsyncMock()
    consumer.stop = AsyncMock()
    consumer.commit = AsyncMock()
    consumer.pause = MagicMock()
    consumer.resume = MagicMock()
    consumer.seek = MagicMock()
    consumer.position = MagicMock(return_value=42)
    return consumer


# ── Sample Payloads ───────────────────────────────────────────────────────────

@pytest.fixture
def sample_raw_document_event():
    return {
        "event_id": str(uuid.uuid4()),
        "event_version": "1.0",
        "doc_id": "DOC-001TESTDOC001",
        "raw_storage_path": "raw/drug/2024/01/15/DOC-001TESTDOC001/app.pdf",
        "submission_type": "drug",
        "portal_source": "sugam",
        "original_filename": "drug_application.pdf",
        "mime_type": "application/pdf",
        "file_size_bytes": 1024000,
        "checksum_sha256": "abc123def456" * 4,
        "submitted_by": "user-test-001",
        "ip_address": "192.168.1.1",
        "external_id": "SUGAM-2024-001",
        "metadata": {"department": "pharma"},
        "timestamp": int(time.time() * 1000),
    }


@pytest.fixture
def sample_preprocessed_event():
    return {
        "event_id": str(uuid.uuid4()),
        "event_version": "1.0",
        "doc_id": "DOC-001TESTDOC001",
        "processed_storage_path": "processed/drug/2024/01/15/DOC-001TESTDOC001/processed.json",
        "submission_type": "drug",
        "portal_source": "sugam",
        "submitted_by": "user-test-001",
        "original_filename": "drug_application.pdf",
        "chunk_count": 25,
        "table_count": 3,
        "page_count": 10,
        "word_count": 5000,
        "extraction_confidence": 0.95,
        "language": "en",
        "language_confidence": 0.99,
        "is_multilingual": False,
        "ocr_used": False,
        "ocr_pages": 0,
        "extractors_used": ["PDFExtractor"],
        "flags": {
            "needs_human_review": False,
            "has_tables": True,
            "is_multilingual": False,
            "ocr_used": False,
            "low_confidence": False,
            "possible_encrypted": False,
            "possible_corrupted": False,
            "translation_needed": False,
            "pii_columns_detected": False,
        },
        "metadata": {"applicant": "PharmaCo"},
        "preprocessed_at": int(time.time() * 1000),
        "timestamp": int(time.time() * 1000),
    }


@pytest.fixture
def sample_dlq_event():
    return {
        "event_id": str(uuid.uuid4()),
        "event_version": "1.0",
        "original_topic": "raw.documents.ingested",
        "original_partition": 0,
        "original_offset": 100,
        "original_payload": json.dumps({"doc_id": "DOC-FAIL001", "event_id": str(uuid.uuid4())}),
        "original_key": "DOC-FAIL001",
        "error_type": "ValueError",
        "error_message": "Cannot parse document: file is corrupted",
        "stack_trace": "Traceback...\nValueError: Cannot parse document",
        "retry_count": 3,
        "consumer_group": "rxflow-preprocessor",
        "failed_at": int(time.time() * 1000),
        "doc_id": "DOC-FAIL001",
        "timestamp": int(time.time() * 1000),
    }
