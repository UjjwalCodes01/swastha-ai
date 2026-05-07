"""Tests for BaseConsumer (messagebus/consumer.py)."""
from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from app.messagebus.consumer import BaseConsumer, _MAX_RETRIES, _REDIS_RETRY_PREFIX
from app.messagebus.event_store import EventStore


def make_record(topic="raw.documents.ingested", partition=0, offset=10, payload=None, doc_id="DOC-123"):
    """Create a fake ConsumerRecord-like object."""
    record = MagicMock()
    record.topic = topic
    record.partition = partition
    record.offset = offset
    record.timestamp = int(time.time() * 1000)
    record.key = doc_id.encode() if doc_id else None
    record.value = json.dumps(payload or {"doc_id": doc_id, "event_id": "ev-001"}).encode()
    record.headers = []
    return record


@pytest.fixture
def event_store(mock_db_session):
    factory, _ = mock_db_session
    return EventStore(factory)


@pytest.fixture
def consumer(mock_redis, event_store, mock_schema_registry):
    with patch("app.messagebus.consumer.get_schema_registry", return_value=mock_schema_registry):
        c = BaseConsumer(
            bootstrap_servers="localhost:9092",
            group_id="test-group",
            redis_client=mock_redis,
            event_store=event_store,
            max_retries=3,
        )
        c._consumer = AsyncMock()
        c._consumer.commit = AsyncMock()
        c._consumer.stop = AsyncMock()
        return c


@pytest.mark.asyncio
async def test_successful_processing_commits_offset(consumer, mock_schema_registry, sample_raw_document_event):
    mock_schema_registry.deserialise.return_value = sample_raw_document_event
    record = make_record(payload=sample_raw_document_event)

    handler = AsyncMock()
    consumer._handler = handler

    with patch("app.messagebus.consumer.get_schema_registry", return_value=mock_schema_registry):
        await consumer._process_record(record)

    consumer._consumer.commit.assert_called_once()
    handler.assert_called_once()


@pytest.mark.asyncio
async def test_failed_processing_does_not_commit_on_first_failure(consumer, mock_schema_registry, sample_raw_document_event):
    mock_schema_registry.deserialise.return_value = sample_raw_document_event
    record = make_record(payload=sample_raw_document_event)

    handler = AsyncMock(side_effect=ValueError("Processing error"))
    consumer._handler = handler

    with patch("app.messagebus.consumer.get_schema_registry", return_value=mock_schema_registry), \
         patch.object(consumer, "_publish_to_retry", new_callable=AsyncMock) as mock_retry, \
         patch.object(consumer, "_send_to_dlq", new_callable=AsyncMock) as mock_dlq:
        await consumer._process_record(record)

    # After first failure, should go to retry (not DLQ)
    mock_retry.assert_called_once()
    mock_dlq.assert_not_called()
    # Still commits after routing to retry (to avoid re-processing immediately)
    consumer._consumer.commit.assert_called_once()


@pytest.mark.asyncio
async def test_retry_counter_increments_on_failure(consumer, mock_redis, mock_schema_registry, sample_raw_document_event):
    mock_schema_registry.deserialise.return_value = sample_raw_document_event
    record = make_record(payload=sample_raw_document_event)
    consumer._handler = AsyncMock(side_effect=ValueError("fail"))

    with patch("app.messagebus.consumer.get_schema_registry", return_value=mock_schema_registry), \
         patch.object(consumer, "_publish_to_retry", new_callable=AsyncMock):
        await consumer._process_record(record)

    count = await mock_redis.get(f"{_REDIS_RETRY_PREFIX}raw.documents.ingested:DOC-001TESTDOC001")
    assert count is not None and int(count) == 1


@pytest.mark.asyncio
async def test_first_failure_goes_to_retry1(consumer, mock_redis, mock_schema_registry, sample_raw_document_event):
    mock_schema_registry.deserialise.return_value = sample_raw_document_event
    record = make_record(payload=sample_raw_document_event)
    consumer._handler = AsyncMock(side_effect=ValueError("fail"))

    with patch("app.messagebus.consumer.get_schema_registry", return_value=mock_schema_registry), \
         patch.object(consumer, "_publish_to_retry", new_callable=AsyncMock) as mock_retry:
        await consumer._process_record(record)

    mock_retry.assert_called_once()
    _, kwargs = mock_retry.call_args_list[0]
    assert mock_retry.call_args[0][2] == 1  # retry_count=1


@pytest.mark.asyncio
async def test_after_max_retries_goes_to_dlq(consumer, mock_redis, mock_schema_registry, sample_raw_document_event):
    mock_schema_registry.deserialise.return_value = sample_raw_document_event
    record = make_record(payload=sample_raw_document_event)

    # Pre-set retry count to max
    await mock_redis.set(
        f"{_REDIS_RETRY_PREFIX}raw.documents.ingested:{sample_raw_document_event['doc_id']}",
        str(_MAX_RETRIES),
    )
    consumer._handler = AsyncMock(side_effect=ValueError("final failure"))

    with patch("app.messagebus.consumer.get_schema_registry", return_value=mock_schema_registry), \
         patch.object(consumer, "_send_to_dlq", new_callable=AsyncMock) as mock_dlq, \
         patch.object(consumer, "_publish_to_retry", new_callable=AsyncMock) as mock_retry:
        await consumer._process_record(record)

    mock_dlq.assert_called_once()
    mock_retry.assert_not_called()


@pytest.mark.asyncio
async def test_pause_and_resume_partitions(consumer):
    consumer._consumer.pause = MagicMock()
    consumer._consumer.resume = MagicMock()

    await consumer.pause_partitions([("raw.documents.ingested", 0)])
    consumer._consumer.pause.assert_called_once()

    await consumer.resume_partitions([("raw.documents.ingested", 0)])
    consumer._consumer.resume.assert_called_once()


@pytest.mark.asyncio
async def test_seek_to_offset(consumer):
    consumer._consumer.seek = MagicMock()
    consumer._consumer.commit = AsyncMock()

    await consumer.seek_to_offset("raw.documents.ingested", 0, 999)

    consumer._consumer.seek.assert_called_once()
    consumer._consumer.commit.assert_called_once()
