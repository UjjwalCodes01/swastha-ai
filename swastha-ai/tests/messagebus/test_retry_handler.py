"""Tests for RetryHandler (messagebus/retry_handler.py)."""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

from app.messagebus.retry_handler import RetryHandler, RETRY_DELAYS


@pytest.fixture
def make_handler(mock_redis, mock_db_session):
    factory, _ = mock_db_session

    def _make(level: int, original_handler=None):
        from app.messagebus.event_store import EventStore
        store = EventStore(factory)
        handler = AsyncMock() if original_handler is None else original_handler
        with patch("app.messagebus.retry_handler.BaseConsumer"):
            rh = RetryHandler(
                level=level,
                bootstrap_servers="localhost:9092",
                redis_client=mock_redis,
                event_store=store,
                original_handler=handler,
            )
            rh._consumer = MagicMock()
            return rh, handler
    return _make


@pytest.mark.asyncio
async def test_retry1_waits_5_seconds(make_handler):
    rh, original = make_handler(1)

    record = MagicMock()
    record.topic = "raw.documents.ingested.retry.1"
    record.timestamp = int(time.time() * 1000)

    payload = {
        "doc_id": "DOC-RETRY",
        "_retry_timestamp": int((time.time() - 1) * 1000),  # 1 second ago → 4s remaining
        "_original_topic": "raw.documents.ingested",
        "_retry_count": 1,
    }

    slept_for = []

    async def fake_sleep(seconds):
        slept_for.append(seconds)

    with patch("asyncio.sleep", side_effect=fake_sleep):
        await rh._handle_retry_message(record, payload)

    assert slept_for, "Expected asyncio.sleep to be called"
    assert 3.5 <= slept_for[0] <= 5.0  # Should sleep ~4s


@pytest.mark.asyncio
async def test_retry2_waits_30_seconds(make_handler):
    rh, _ = make_handler(2)

    record = MagicMock()
    payload = {
        "doc_id": "DOC-RETRY",
        "_retry_timestamp": int(time.time() * 1000),  # Just now → full 30s remaining
        "_original_topic": "raw.documents.ingested",
    }

    slept_for = []

    async def fake_sleep(seconds):
        slept_for.append(seconds)

    with patch("asyncio.sleep", side_effect=fake_sleep):
        await rh._handle_retry_message(record, payload)

    assert slept_for[0] >= 29.0


@pytest.mark.asyncio
async def test_retry3_waits_5_minutes(make_handler):
    rh, _ = make_handler(3)

    record = MagicMock()
    payload = {
        "doc_id": "DOC-RETRY",
        "_retry_timestamp": int(time.time() * 1000),
        "_original_topic": "raw.documents.ingested",
    }

    slept_for = []

    async def fake_sleep(seconds):
        slept_for.append(seconds)

    with patch("asyncio.sleep", side_effect=fake_sleep):
        await rh._handle_retry_message(record, payload)

    assert slept_for[0] >= 295.0  # ~5 minutes


@pytest.mark.asyncio
async def test_successful_retry_calls_original_handler(make_handler):
    original = AsyncMock()
    rh, _ = make_handler(1, original_handler=original)

    record = MagicMock()
    payload = {
        "doc_id": "DOC-OK",
        "_retry_timestamp": int((time.time() - 10) * 1000),  # elapsed → no sleep
        "_original_topic": "raw.documents.ingested",
    }

    with patch("asyncio.sleep", new_callable=AsyncMock):
        await rh._handle_retry_message(record, payload)

    original.assert_called_once()
    # Retry metadata should be stripped
    clean_payload = original.call_args[0][1]
    assert "_retry_timestamp" not in clean_payload
    assert "_retry_count" not in clean_payload
    assert "_original_topic" not in clean_payload


@pytest.mark.asyncio
async def test_failed_retry_propagates_exception(make_handler):
    original = AsyncMock(side_effect=ValueError("still failing"))
    rh, _ = make_handler(1, original_handler=original)

    record = MagicMock()
    payload = {
        "doc_id": "DOC-FAIL",
        "_retry_timestamp": int((time.time() - 10) * 1000),
        "_original_topic": "raw.documents.ingested",
    }

    with patch("asyncio.sleep", new_callable=AsyncMock):
        with pytest.raises(ValueError, match="still failing"):
            await rh._handle_retry_message(record, payload)


@pytest.mark.asyncio
async def test_retry_count_in_redis_expires_after_24h(mock_redis):
    """Verify that retry counts have TTL set to 24 hours."""
    from app.messagebus.consumer import _REDIS_RETRY_TTL, _REDIS_RETRY_PREFIX
    assert _REDIS_RETRY_TTL == 86400
