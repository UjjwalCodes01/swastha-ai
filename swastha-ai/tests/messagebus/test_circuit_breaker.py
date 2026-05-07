"""Tests for CircuitBreaker (messagebus/circuit_breaker.py)."""
from __future__ import annotations

from unittest.mock import patch
import pytest

from app.messagebus.circuit_breaker import (
    CircuitBreaker, CircuitBreakerConfig, CircuitOpenError, CircuitState,
    _REDIS_PREFIX,
)


@pytest.fixture
def config():
    return CircuitBreakerConfig(
        failure_threshold=5,
        recovery_timeout=60.0,
        half_open_max_calls=3,
        expected_exceptions=(Exception,),
    )


@pytest.fixture
def breaker(mock_redis, config):
    return CircuitBreaker("test_circuit", mock_redis, config)


@pytest.mark.asyncio
async def test_circuit_starts_closed(breaker):
    state = await breaker.get_state()
    assert state == CircuitState.CLOSED


@pytest.mark.asyncio
async def test_circuit_opens_after_threshold_failures(breaker, mock_redis):
    # Record 5 failures
    for _ in range(5):
        await breaker._record_failure()

    state = await breaker.get_state()
    assert state == CircuitState.OPEN


@pytest.mark.asyncio
async def test_circuit_rejects_calls_when_open(breaker, mock_redis):
    # Force open
    await breaker._open_circuit()
    # Set opened_at to now (not expired)
    import time
    await mock_redis.set(f"{_REDIS_PREFIX}test_circuit:opened_at", str(time.time()))

    with pytest.raises(CircuitOpenError) as exc_info:
        await breaker._check()

    assert "test_circuit" in str(exc_info.value)


@pytest.mark.asyncio
async def test_circuit_transitions_to_half_open_after_timeout(breaker, mock_redis):
    import time
    await breaker._open_circuit()
    # Set opened_at to 61 seconds ago (past recovery_timeout=60s)
    await mock_redis.set(
        f"{_REDIS_PREFIX}test_circuit:opened_at",
        str(time.time() - 61),
    )

    # _check should NOT raise — should allow the probe through (→ HALF_OPEN)
    await breaker._check()  # Should not raise
    state = await breaker.get_state()
    assert state == CircuitState.HALF_OPEN


@pytest.mark.asyncio
async def test_circuit_closes_after_successful_half_open_calls(breaker, config):
    await breaker._set_state(CircuitState.HALF_OPEN)

    # Record enough successes to close
    for _ in range(config.half_open_max_calls):
        await breaker._record_success()

    state = await breaker.get_state()
    assert state == CircuitState.CLOSED


@pytest.mark.asyncio
async def test_circuit_reopens_after_failed_half_open_call(breaker):
    await breaker._set_state(CircuitState.HALF_OPEN)
    await breaker._record_failure()

    state = await breaker.get_state()
    assert state == CircuitState.OPEN


@pytest.mark.asyncio
async def test_circuit_state_persisted_in_redis(breaker, mock_redis):
    await breaker._set_state(CircuitState.OPEN)
    raw = await mock_redis.get(f"{_REDIS_PREFIX}test_circuit:state")
    assert raw == "OPEN" or raw == b"OPEN"


@pytest.mark.asyncio
async def test_new_breaker_inherits_state_from_redis(mock_redis, config):
    """A new CircuitBreaker instance reads state from Redis (simulates pod restart)."""
    # Set state in Redis as if a previous pod opened it
    await mock_redis.set(f"{_REDIS_PREFIX}test_circuit:state", "OPEN")

    new_breaker = CircuitBreaker("test_circuit", mock_redis, config)
    state = await new_breaker.get_state()
    assert state == CircuitState.OPEN


@pytest.mark.asyncio
async def test_force_close_resets_circuit(breaker):
    await breaker._set_state(CircuitState.OPEN)
    await breaker.force_close()
    state = await breaker.get_state()
    assert state == CircuitState.CLOSED


@pytest.mark.asyncio
async def test_context_manager_success_records_success(breaker):
    with patch.object(breaker, "_record_success") as mock_success, \
         patch.object(breaker, "_check", new=lambda: None):
        async with breaker:
            pass  # No exception

    mock_success.assert_called_once()


@pytest.mark.asyncio
async def test_context_manager_exception_records_failure(breaker):
    with patch.object(breaker, "_record_failure") as mock_failure, \
         patch.object(breaker, "_check"):
        with pytest.raises(ValueError):
            async with breaker:
                raise ValueError("downstream failure")

    mock_failure.assert_called_once()
