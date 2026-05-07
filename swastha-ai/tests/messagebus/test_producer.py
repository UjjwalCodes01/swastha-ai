"""Tests for ResilienceProducer (messagebus/producer.py)."""
from __future__ import annotations

import json
import struct
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.messagebus.producer import ResilienceProducer, PublishFailedError, _REDIS_BUFFER_MAX


@pytest.fixture
def producer(mock_redis, mock_schema_registry):
    with patch("app.messagebus.producer.AIOKafkaProducer") as MockProducer, \
         patch("app.messagebus.producer.get_schema_registry", return_value=mock_schema_registry):
        mock_inner = AsyncMock()
        mock_inner.start = AsyncMock()
        mock_inner.stop = AsyncMock()
        mock_inner.flush = AsyncMock()
        mock_inner.send_and_wait = AsyncMock(return_value=MagicMock(topic="test", partition=0, offset=1))
        MockProducer.return_value = mock_inner

        p = ResilienceProducer("localhost:9092", mock_redis)
        p._producer = mock_inner
        p._running = True
        return p


@pytest.mark.asyncio
async def test_publish_sends_to_correct_topic(producer, mock_schema_registry):
    payload = {"doc_id": "DOC-123", "event_id": "ev-001"}
    await producer.publish("raw.documents.ingested", payload)
    producer._producer.send_and_wait.assert_called_once()
    call_kwargs = producer._producer.send_and_wait.call_args
    assert call_kwargs[0][0] == "raw.documents.ingested"


@pytest.mark.asyncio
async def test_publish_uses_doc_id_as_partition_key(producer):
    payload = {"doc_id": "DOC-456"}
    await producer.publish("documents.preprocessed", payload)
    call_kwargs = producer._producer.send_and_wait.call_args
    assert call_kwargs[1]["key"] == b"DOC-456"


@pytest.mark.asyncio
async def test_publish_injects_event_id_if_missing(producer):
    payload = {"doc_id": "DOC-789"}
    await producer.publish("documents.preprocessed", payload)
    assert "event_id" in payload
    assert len(payload["event_id"]) == 36  # UUID4 format


@pytest.mark.asyncio
async def test_publish_injects_timestamp_if_missing(producer):
    payload = {"doc_id": "DOC-789"}
    before = int(time.time() * 1000)
    await producer.publish("documents.preprocessed", payload)
    after = int(time.time() * 1000)
    assert before <= payload["timestamp"] <= after


@pytest.mark.asyncio
async def test_broker_failure_buffers_in_redis(producer, mock_redis):
    producer._producer.send_and_wait.side_effect = Exception("Connection refused")
    payload = {"doc_id": "DOC-FAIL", "event_id": "ev-fail"}
    await producer.publish("raw.documents.ingested", payload)
    assert await mock_redis.llen("kafka:publish_buffer") == 1


@pytest.mark.asyncio
async def test_redis_buffer_drained_when_broker_recovers(producer, mock_redis):
    # Pre-populate buffer
    envelope = json.dumps({"topic": "raw.documents.ingested", "payload": {"doc_id": "DOC-BUF", "event_id": "x"}, "key": "DOC-BUF", "headers": {}})
    await mock_redis.rpush("kafka:publish_buffer", envelope)

    producer._producer.send_and_wait.side_effect = None  # Broker recovered
    producer._producer.send_and_wait.return_value = MagicMock()

    await producer._drain_once()
    assert await mock_redis.llen("kafka:publish_buffer") == 0
    producer._producer.send_and_wait.assert_called_once()


@pytest.mark.asyncio
async def test_redis_buffer_overflow_raises_publish_failed_error(producer, mock_redis):
    # Fill buffer to max
    for i in range(_REDIS_BUFFER_MAX):
        await mock_redis.rpush("kafka:publish_buffer", f"msg{i}")

    producer._producer.send_and_wait.side_effect = Exception("Broker down")

    with pytest.raises(PublishFailedError):
        await producer.publish("raw.documents.ingested", {"doc_id": "DOC-X"})


@pytest.mark.asyncio
async def test_graceful_shutdown_calls_flush(producer):
    producer._running = True
    await producer.stop()
    producer._producer.flush.assert_called_once()
    producer._producer.stop.assert_called_once()
