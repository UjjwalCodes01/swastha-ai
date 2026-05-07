"""Tests for DLQ Handler."""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.messagebus.dlq_handler import DLQAlertTask, router


@pytest.fixture
def mock_dlq_row(sample_dlq_event):
    row = MagicMock()
    row._mapping = {
        "event_id": sample_dlq_event["event_id"],
        "original_topic": sample_dlq_event["original_topic"],
        "original_partition": 0,
        "original_offset": 100,
        "original_payload": sample_dlq_event["original_payload"],
        "error_type": "ValueError",
        "error_message": "Cannot parse",
        "stack_trace": None,
        "retry_count": 3,
        "consumer_group": "rxflow-preprocessor",
        "doc_id": "DOC-FAIL001",
        "status": "pending",
        "dismissal_reason": None,
        "failed_at": datetime.now(timezone.utc),
        "created_at": datetime.now(timezone.utc),
    }
    return row


@pytest.mark.asyncio
async def test_dlq_event_summary_returns_correct_counts(mock_db_session):
    factory, session = mock_db_session
    result_row = MagicMock()
    result_row._mapping = {
        "original_topic": "raw.documents.ingested",
        "pending_count": 15,
        "reprocessing_count": 2,
        "dismissed_count": 1,
        "resolved_count": 5,
        "oldest_pending_at": datetime.now(timezone.utc),
        "most_common_error": "ValueError",
    }
    session.execute.return_value = AsyncMock(fetchall=AsyncMock(return_value=[result_row]))

    from app.messagebus import dlq_handler as dh
    dh._db_factory = factory

    response = await dh.dlq_summary(db=factory)
    assert response["total_pending"] == 15
    assert len(response["topics"]) == 1
    assert response["topics"][0]["pending_count"] == 15


@pytest.mark.asyncio
async def test_reprocess_publishes_to_original_topic(mock_db_session, mock_dlq_row, sample_dlq_event):
    factory, session = mock_db_session
    session.execute.return_value = AsyncMock(fetchone=AsyncMock(return_value=mock_dlq_row))

    mock_producer = AsyncMock()
    mock_producer.publish = AsyncMock()

    from app.messagebus import dlq_handler as dh
    dh._db_factory = factory
    dh._redis = AsyncMock()
    dh._redis.delete = AsyncMock()

    with patch("app.messagebus.dlq_handler.get_producer", return_value=mock_producer):
        result = await dh.reprocess_event(sample_dlq_event["event_id"], db=factory)

    mock_producer.publish.assert_called_once()
    call_args = mock_producer.publish.call_args
    assert call_args[0][0] == "raw.documents.ingested"


@pytest.mark.asyncio
async def test_reprocess_resets_retry_counter_in_redis(mock_db_session, mock_dlq_row, sample_dlq_event):
    factory, session = mock_db_session
    session.execute.return_value = AsyncMock(fetchone=AsyncMock(return_value=mock_dlq_row))

    mock_producer = AsyncMock()
    mock_redis = AsyncMock()
    mock_redis.delete = AsyncMock()

    from app.messagebus import dlq_handler as dh
    dh._db_factory = factory
    dh._redis = mock_redis

    with patch("app.messagebus.dlq_handler.get_producer", return_value=mock_producer):
        await dh.reprocess_event(sample_dlq_event["event_id"], db=factory)

    mock_redis.delete.assert_called_once_with(
        f"retry_count:raw.documents.ingested:DOC-FAIL001"
    )


@pytest.mark.asyncio
async def test_dismiss_sets_status_to_dismissed(mock_db_session, sample_dlq_event):
    factory, session = mock_db_session
    dismissed_row = MagicMock()
    dismissed_row._mapping = {"event_id": sample_dlq_event["event_id"]}
    session.execute.return_value = AsyncMock(fetchone=AsyncMock(return_value=dismissed_row))

    from app.messagebus.dlq_handler import DismissRequest
    from app.messagebus import dlq_handler as dh
    dh._db_factory = factory

    result = await dh.dismiss_event(
        sample_dlq_event["event_id"],
        DismissRequest(dismissal_reason="Document format not supported"),
        db=factory,
    )
    assert result["status"] == "dismissed"


@pytest.mark.asyncio
async def test_dlq_alert_warning_at_10_events():
    mock_producer = AsyncMock()
    mock_producer.publish = AsyncMock()

    mock_db = MagicMock()
    result_row = MagicMock()
    result_row.original_topic = "raw.documents.ingested"
    result_row.pending_count = 15

    async def mock_aenter(self):
        session = AsyncMock()
        session.execute.return_value = AsyncMock(fetchall=AsyncMock(return_value=[result_row]))
        return session

    async def mock_aexit(self, *args):
        return False

    mock_db.return_value.__aenter__ = mock_aenter
    mock_db.return_value.__aexit__ = mock_aexit

    task = DLQAlertTask(mock_db, AsyncMock(return_value=mock_producer))

    with patch.object(task, "_alert", new_callable=AsyncMock) as mock_alert:
        await task._check()

    mock_alert.assert_called_once_with("WARNING", "raw.documents.ingested", 15)


@pytest.mark.asyncio
async def test_dlq_alert_critical_at_50_events():
    mock_producer = AsyncMock()
    mock_producer.publish = AsyncMock()

    mock_db = MagicMock()
    result_row = MagicMock()
    result_row.original_topic = "documents.classified"
    result_row.pending_count = 55

    async def mock_aenter(self):
        session = AsyncMock()
        session.execute.return_value = AsyncMock(fetchall=AsyncMock(return_value=[result_row]))
        return session

    async def mock_aexit(self, *args):
        return False

    mock_db.return_value.__aenter__ = mock_aenter
    mock_db.return_value.__aexit__ = mock_aexit

    task = DLQAlertTask(mock_db, AsyncMock(return_value=mock_producer))

    with patch.object(task, "_alert", new_callable=AsyncMock) as mock_alert:
        await task._check()

    mock_alert.assert_called_once_with("CRITICAL", "documents.classified", 55)
