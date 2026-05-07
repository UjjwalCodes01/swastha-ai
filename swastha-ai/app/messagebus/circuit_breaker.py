"""
Circuit Breaker — protects all external service calls.

Three states:
  CLOSED    → normal operation
  OPEN      → downstream failing; all calls rejected immediately
  HALF_OPEN → test probe: one call allowed; success → CLOSED, fail → OPEN

State is persisted in Redis so pod restarts inherit circuit state
and don't hammer recovering services.

Usage:
    breaker = CircuitBreaker("minio_circuit", redis_client)
    async with breaker:
        await minio_client.put_object(...)
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Type

logger = logging.getLogger(__name__)


class CircuitState(str, Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitOpenError(RuntimeError):
    """Raised when a call is rejected because the circuit is OPEN."""
    def __init__(self, circuit_name: str, recover_in: float) -> None:
        self.circuit_name = circuit_name
        self.recover_in = recover_in
        super().__init__(
            f"Circuit '{circuit_name}' is OPEN. "
            f"Recovers in ~{recover_in:.0f}s."
        )


@dataclass
class CircuitBreakerConfig:
    failure_threshold: int = 5          # Consecutive failures before OPEN
    recovery_timeout: float = 60.0      # Seconds in OPEN before HALF_OPEN
    half_open_max_calls: int = 3        # Successful calls needed to CLOSE
    expected_exceptions: tuple[Type[Exception], ...] = field(
        default_factory=lambda: (Exception,)
    )


# Pre-configured circuits for each external dependency
CIRCUIT_CONFIGS: dict[str, CircuitBreakerConfig] = {
    "minio_circuit":     CircuitBreakerConfig(failure_threshold=5, recovery_timeout=30.0),
    "postgres_circuit":  CircuitBreakerConfig(failure_threshold=5, recovery_timeout=30.0),
    "chromadb_circuit":  CircuitBreakerConfig(failure_threshold=5, recovery_timeout=60.0),
    "llm_circuit":       CircuitBreakerConfig(failure_threshold=3, recovery_timeout=120.0),
    "tika_circuit":      CircuitBreakerConfig(failure_threshold=5, recovery_timeout=60.0),
}

_REDIS_PREFIX = "circuit_breaker:"
_REDIS_TTL = 3600  # 1 hour


class CircuitBreaker:
    """
    Async context-manager based circuit breaker.

    Redis keys:
      circuit_breaker:{name}:state       — CLOSED | OPEN | HALF_OPEN
      circuit_breaker:{name}:failures    — consecutive failure count
      circuit_breaker:{name}:opened_at   — epoch float when circuit opened
      circuit_breaker:{name}:half_open_successes — successes in HALF_OPEN
    """

    def __init__(
        self,
        name: str,
        redis_client: Any,
        config: CircuitBreakerConfig | None = None,
    ) -> None:
        self.name = name
        self._redis = redis_client
        self._config = config or CIRCUIT_CONFIGS.get(name, CircuitBreakerConfig())
        self._local_state = CircuitState.CLOSED  # local cache; Redis is source of truth

    # ── context manager ───────────────────────────────────────────────────────

    async def __aenter__(self) -> "CircuitBreaker":
        await self._check()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, tb: Any) -> bool:
        if exc_type is None:
            await self._record_success()
        elif exc_type and issubclass(exc_type, self._config.expected_exceptions):
            await self._record_failure()
        # Don't suppress the exception
        return False

    # ── public helpers ────────────────────────────────────────────────────────

    async def call(self, func: Callable, *args: Any, **kwargs: Any) -> Any:
        """Wrap an async callable in the circuit breaker."""
        async with self:
            return await func(*args, **kwargs)

    async def get_state(self) -> CircuitState:
        raw = await self._redis.get(f"{_REDIS_PREFIX}{self.name}:state")
        if raw:
            return CircuitState(raw.decode() if isinstance(raw, bytes) else raw)
        return CircuitState.CLOSED

    async def force_close(self) -> None:
        """Admin operation — force circuit to CLOSED."""
        await self._set_state(CircuitState.CLOSED)
        await self._redis.delete(
            f"{_REDIS_PREFIX}{self.name}:failures",
            f"{_REDIS_PREFIX}{self.name}:opened_at",
            f"{_REDIS_PREFIX}{self.name}:half_open_successes",
        )
        logger.warning(f"Circuit '{self.name}' force-closed by admin")

    # ── state machine internals ───────────────────────────────────────────────

    async def _check(self) -> None:
        state = await self.get_state()

        if state == CircuitState.CLOSED:
            return

        if state == CircuitState.OPEN:
            opened_at_raw = await self._redis.get(f"{_REDIS_PREFIX}{self.name}:opened_at")
            opened_at = float(opened_at_raw) if opened_at_raw else time.time()
            elapsed = time.time() - opened_at

            if elapsed >= self._config.recovery_timeout:
                await self._set_state(CircuitState.HALF_OPEN)
                logger.info(f"Circuit '{self.name}' → HALF_OPEN (probing)")
                return  # allow this test call through

            recover_in = self._config.recovery_timeout - elapsed
            raise CircuitOpenError(self.name, recover_in)

        # HALF_OPEN — allow calls through (success/failure handled in __aexit__)

    async def _record_success(self) -> None:
        state = await self.get_state()
        if state == CircuitState.CLOSED:
            # Reset failure counter on any success
            await self._redis.set(
                f"{_REDIS_PREFIX}{self.name}:failures", 0, ex=_REDIS_TTL
            )
            return

        if state == CircuitState.HALF_OPEN:
            key = f"{_REDIS_PREFIX}{self.name}:half_open_successes"
            successes = await self._redis.incr(key)
            await self._redis.expire(key, _REDIS_TTL)
            if successes >= self._config.half_open_max_calls:
                await self._set_state(CircuitState.CLOSED)
                await self._redis.delete(
                    f"{_REDIS_PREFIX}{self.name}:failures",
                    f"{_REDIS_PREFIX}{self.name}:opened_at",
                    key,
                )
                logger.info(f"Circuit '{self.name}' → CLOSED (recovered)")

    async def _record_failure(self) -> None:
        state = await self.get_state()

        if state == CircuitState.HALF_OPEN:
            # Single failure in HALF_OPEN → immediately back to OPEN
            await self._open_circuit()
            logger.warning(f"Circuit '{self.name}' HALF_OPEN probe failed → OPEN")
            return

        failures_key = f"{_REDIS_PREFIX}{self.name}:failures"
        failures = await self._redis.incr(failures_key)
        await self._redis.expire(failures_key, _REDIS_TTL)

        logger.debug(f"Circuit '{self.name}' failure #{failures}")

        if failures >= self._config.failure_threshold:
            await self._open_circuit()
            logger.error(
                f"Circuit '{self.name}' → OPEN after {failures} failures"
            )

    async def _open_circuit(self) -> None:
        await self._set_state(CircuitState.OPEN)
        await self._redis.set(
            f"{_REDIS_PREFIX}{self.name}:opened_at",
            str(time.time()),
            ex=_REDIS_TTL,
        )
        # Reset half-open probe counter
        await self._redis.delete(f"{_REDIS_PREFIX}{self.name}:half_open_successes")

    async def _set_state(self, state: CircuitState) -> None:
        await self._redis.set(
            f"{_REDIS_PREFIX}{self.name}:state",
            state.value,
            ex=_REDIS_TTL,
        )
        self._local_state = state


class CircuitBreakerRegistry:
    """Singleton registry of all circuit breakers in the process."""

    _instance: "CircuitBreakerRegistry | None" = None

    def __init__(self) -> None:
        self._breakers: dict[str, CircuitBreaker] = {}
        self._redis: Any = None

    @classmethod
    def get(cls) -> "CircuitBreakerRegistry":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def initialize(self, redis_client: Any) -> None:
        self._redis = redis_client
        for name in CIRCUIT_CONFIGS:
            self._breakers[name] = CircuitBreaker(name, redis_client)

    def breaker(self, name: str) -> CircuitBreaker:
        if name not in self._breakers:
            if self._redis is None:
                raise RuntimeError("CircuitBreakerRegistry not initialized")
            self._breakers[name] = CircuitBreaker(name, self._redis)
        return self._breakers[name]

    async def get_all_states(self) -> dict[str, str]:
        states: dict[str, str] = {}
        for name, breaker in self._breakers.items():
            state = await breaker.get_state()
            states[name] = state.value
        return states
