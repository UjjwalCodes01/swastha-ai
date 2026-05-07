"""Tests for EventStore (messagebus/event_store.py)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, call

import pytest

from app.messagebus.event_store import EventStore, _PAYLOAD_SUMMARY_MAX_BYTES


@pytest.fixture
def store(mock_db_session):
    factory, _ = mock_db_session
    return EventStore(factory), mock_db_session[1]


@pytest.mark.asyncio
async def test_record_received_inserts_kafka_events_row(store):
    event_store, session = store
    produced_at = datetime.now(timezone.utc)

    await event_store.record_received(
        event_id="ev-001",
        topic="raw.documents.ingested",
        partition=0,
        offset=100,
        consumer_group="rxflow-preprocessor",
        doc_id="DOC-123",
        event_type="RawDocumentIngested",
        payload={"doc_id": "DOC-123", "event_id": "ev-001"},
        produced_at=produced_at,
    )

    session.execute.assert_called_once()
    session.commit.assert_called_once()


@pytest.mark.asyncio
async def test_update_status_changes_processing_status(store):
    event_store, session = store
    produced_at = datetime.now(timezone.utc)

    await event_store.update_status(
        event_id="ev-001",
        produced_at=produced_at,
        status="success",
        duration_ms=250,
    )

    session.execute.assert_called_once()
    session.commit.assert_called_once()
    # Verify "success" is in the SQL parameters
    call_params = session.execute.call_args[0][1]
    assert call_params["status"] == "success"
    assert call_params["duration_ms"] == 250


@pytest.mark.asyncio
async def test_payload_summary_truncates_at_1kb(store):
    event_store, session = store
    large_payload = {"doc_id": "DOC-X", "data": "A" * 5000}  # > 1KB

    produced_at = datetime.now(timezone.utc)
    await event_store.record_received(
        event_id="ev-big",
        topic="documents.preprocessed",
        partition=1,
        offset=200,
        consumer_group="rxflow-ai",
        doc_id="DOC-X",
        event_type="DocumentPreprocessed",
        payload=large_payload,
        produced_at=produced_at,
    )

    call_params = session.execute.call_args[0][1]
    summary_str = call_params["payload_summary"]
    summary = json.loads(summary_str)
    # Should be truncated
    assert summary.get("_truncated") is True
    assert len(summary.get("_preview", "")) <= _PAYLOAD_SUMMARY_MAX_BYTES


@pytest.mark.asyncio
async def test_payload_summary_stores_full_if_under_1kb(store):
    event_store, session = store
    small_payload = {"doc_id": "DOC-SM", "value": "small"}

    await event_store.record_received(
        event_id="ev-sm",
        topic="documents.preprocessed",
        partition=0,
        offset=1,
        consumer_group="rxflow-ai",
        doc_id="DOC-SM",
        event_type="DocumentPreprocessed",
        payload=small_payload,
        produced_at=datetime.now(timezone.utc),
    )

    call_params = session.execute.call_args[0][1]
    summary = json.loads(call_params["payload_summary"])
    assert summary.get("_truncated") is None
    assert summary["doc_id"] == "DOC-SM"


@pytest.mark.asyncio
async def test_query_by_doc_id_uses_correct_filter(store):
    event_store, session = store
    session.execute.return_value = AsyncMock(fetchall=AsyncMock(return_value=[]))

    await event_store.query_by_doc_id("DOC-QUERY")

    session.execute.assert_called_once()
    call_params = session.execute.call_args[0][1]
    assert call_params["doc_id"] == "DOC-QUERY"


@pytest.mark.asyncio
async def test_completed_at_set_on_success_status(store):
    event_store, session = store
    produced_at = datetime.now(timezone.utc)

    await event_store.update_status("ev-001", produced_at, "success")

    call_params = session.execute.call_args[0][1]
    assert call_params["completed_at"] is not None


@pytest.mark.asyncio
async def test_completed_at_none_on_processing_status(store):
    event_store, session = store

    await event_store.update_status("ev-001", datetime.now(timezone.utc), "processing")

    call_params = session.execute.call_args[0][1]
    assert call_params["completed_at"] is None
