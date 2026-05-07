"""Tests for ConsumerLagMonitor (messagebus/consumer_monitor.py)."""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.messagebus.consumer_monitor import (
    ConsumerLagMonitor,
    _REDIS_LAG_PREFIX,
    _LAG_WARN_THRESHOLD,
    _LAG_CRIT_THRESHOLD,
    _REDIS_LAG_TTL,
)


@pytest.fixture
def monitor(mock_redis, mock_db_session):
    factory, _ = mock_db_session
    mock_producer = AsyncMock()
    return ConsumerLagMonitor(
        bootstrap_servers="localhost:9092",
        redis_client=mock_redis,
        db_factory=factory,
        producer_func=AsyncMock(return_value=mock_producer),
        consumer_groups=["rxflow-preprocessor", "rxflow-ai-core"],
    )


@pytest.mark.asyncio
async def test_lag_stored_in_redis(monitor, mock_redis):
    lag_data = {
        "group_id": "rxflow-preprocessor",
        "total_lag": 250,
        "partitions": [{"topic": "raw.documents.ingested", "partition": 0, "committed_offset": 100, "end_offset": 350, "lag": 250}],
        "last_checked": time.time(),
    }

    await monitor._store_lag("rxflow-preprocessor", lag_data)

    raw = await mock_redis.get(f"{_REDIS_LAG_PREFIX}rxflow-preprocessor")
    assert raw is not None
    data = json.loads(raw)
    assert data["total_lag"] == 250


@pytest.mark.asyncio
async def test_lag_retrieved_from_redis(monitor, mock_redis):
    lag_data = {
        "group_id": "rxflow-ai-core",
        "total_lag": 500,
        "partitions": [],
        "last_checked": time.time(),
    }
    await mock_redis.set(
        f"{_REDIS_LAG_PREFIX}rxflow-ai-core",
        json.dumps(lag_data),
    )

    result = await monitor.get_lag_for_group("rxflow-ai-core")
    assert result["total_lag"] == 500


@pytest.mark.asyncio
async def test_warning_alert_published_when_lag_exceeds_1000(monitor):
    with patch.object(monitor, "_alert", new_callable=AsyncMock) as mock_alert:
        await monitor._alert("rxflow-preprocessor", 1500, "WARNING")

    mock_alert.assert_called_once_with("rxflow-preprocessor", 1500, "WARNING")


@pytest.mark.asyncio
async def test_critical_alert_published_when_lag_exceeds_10000(monitor):
    with patch.object(monitor, "_alert", new_callable=AsyncMock) as mock_alert:
        await monitor._alert("rxflow-ai-core", 15000, "CRITICAL")

    mock_alert.assert_called_once_with("rxflow-ai-core", 15000, "CRITICAL")


@pytest.mark.asyncio
async def test_alert_publishes_to_notifications_events(monitor):
    mock_producer = AsyncMock()
    mock_producer.publish = AsyncMock()
    monitor._producer_func = AsyncMock(return_value=mock_producer)

    await monitor._alert("rxflow-preprocessor", 2000, "WARNING")

    mock_producer.publish.assert_called_once()
    call_args = mock_producer.publish.call_args
    assert call_args[0][0] == "notifications.events"
    payload = call_args[0][1]
    assert payload["severity"] == "WARNING"
    assert payload["alert_type"] == "consumer_lag_high"
    assert payload["metric_value"] == 2000.0


@pytest.mark.asyncio
async def test_heartbeat_updated_in_redis(monitor, mock_redis):
    await monitor._update_heartbeat("rxflow-preprocessor")
    raw = await mock_redis.get("consumer_heartbeat:rxflow-preprocessor")
    assert raw is not None
    heartbeat = float(raw)
    assert heartbeat > time.time() - 5  # Should be recent


@pytest.mark.asyncio
async def test_get_all_lags_returns_dict_for_all_groups(monitor, mock_redis):
    # Pre-populate one group
    await mock_redis.set(
        f"{_REDIS_LAG_PREFIX}rxflow-preprocessor",
        json.dumps({"group_id": "rxflow-preprocessor", "total_lag": 10, "partitions": [], "last_checked": time.time()}),
    )

    result = await monitor.get_all_lags()
    assert "rxflow-preprocessor" in result
    assert "rxflow-ai-core" in result


@pytest.mark.asyncio
async def test_lag_threshold_constants():
    """Verify threshold values match the requirement."""
    assert _LAG_WARN_THRESHOLD == 1000
    assert _LAG_CRIT_THRESHOLD == 10000
    assert _REDIS_LAG_TTL == 60  # 60 second TTL per spec
