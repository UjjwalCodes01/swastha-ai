"""
Redis sliding window rate limiter for the SwasthaAI ingestion API.

Two limits enforced simultaneously:
  1. Per-IP address (configurable, default 60/min)
  2. Per-authenticated-user (configurable, default 20/min)

After 10 violations from the same IP in 1 hour, the IP is blocked for 24h.
The block state is stored in Redis for fast lookup on every request.

IP whitelist: known CDSCO portal IPs bypass all rate limits.

Algorithm: Sliding window using Redis ZADD/ZREMRANGEBYSCORE
  - Each request is a member of a sorted set scored by timestamp
  - Old timestamps are pruned before counting
  - This is more accurate than fixed-window (no boundary bursting)
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import RateLimitViolation

logger = logging.getLogger(__name__)

# Redis key prefixes
_IP_RATE_KEY_PREFIX = "swastha-ai:ratelimit:ip:"
_USER_RATE_KEY_PREFIX = "swastha-ai:ratelimit:user:"
_IP_BLOCK_KEY_PREFIX = "swastha-ai:ratelimit:blocked_ip:"
_IP_VIOLATION_KEY_PREFIX = "swastha-ai:ratelimit:violations:ip:"

# Window duration: 60 seconds
_WINDOW_SECONDS = 60

# Violation tracking window: 1 hour
_VIOLATION_WINDOW_SECONDS = 3600

# Block duration: 24 hours
_BLOCK_DURATION_SECONDS = 86400

# Violation threshold before blocking
_BLOCK_THRESHOLD = 10


async def _sliding_window_check(
    redis: Any,
    key: str,
    limit: int,
    window_seconds: int = _WINDOW_SECONDS,
) -> tuple[bool, int, int]:
    """
    Sliding window rate limit check using a Redis sorted set.

    Returns:
        (allowed: bool, current_count: int, ttl_seconds: int)
    """
    now = time.time()
    window_start = now - window_seconds
    member = f"{now}:{uuid.uuid4().hex}"

    pipeline = redis.pipeline()
    # Remove expired entries
    pipeline.zremrangebyscore(key, "-inf", window_start)
    # Add current request
    pipeline.zadd(key, {member: now})
    # Count requests in window
    pipeline.zcard(key)
    # Set TTL on the key to clean up automatically
    pipeline.expire(key, window_seconds + 5)
    results = await pipeline.execute()

    current_count = results[2]
    ttl_seconds = max(0, int(window_start - now + window_seconds) + 1)

    return current_count <= limit, current_count, ttl_seconds


async def _is_ip_blocked(redis: Any, ip: str) -> bool:
    """Check if an IP is currently in the block list."""
    key = f"{_IP_BLOCK_KEY_PREFIX}{ip}"
    try:
        return await redis.exists(key) > 0
    except Exception:
        return False  # On Redis error, don't block (fail open)


async def _record_violation(
    redis: Any,
    db: AsyncSession,
    ip: str,
    endpoint: str,
) -> None:
    """
    Record a rate limit violation and block the IP if threshold is exceeded.

    Uses Redis for fast violation counting (sorted set within 1-hour window).
    Writes to PostgreSQL for the audit trail and blocking persistence.
    """
    now = time.time()
    violation_key = f"{_IP_VIOLATION_KEY_PREFIX}{ip}"
    member = f"{now}:{uuid.uuid4().hex}"

    try:
        pipeline = redis.pipeline()
        pipeline.zremrangebyscore(violation_key, "-inf", now - _VIOLATION_WINDOW_SECONDS)
        pipeline.zadd(violation_key, {member: now})
        pipeline.zcard(violation_key)
        pipeline.expire(violation_key, _VIOLATION_WINDOW_SECONDS)
        results = await pipeline.execute()
        violation_count = results[2]
    except Exception as exc:
        logger.error("Redis violation tracking failed", extra={"error": str(exc), "ip": ip})
        return

    logger.warning(
        "Rate limit violation",
        extra={"ip": ip, "endpoint": endpoint, "violation_count": violation_count},
    )

    if violation_count >= _BLOCK_THRESHOLD:
        block_key = f"{_IP_BLOCK_KEY_PREFIX}{ip}"
        try:
            await redis.setex(block_key, _BLOCK_DURATION_SECONDS, "1")
            logger.warning(
                "IP blocked for 24 hours after repeated violations",
                extra={"ip": ip, "violation_count": violation_count},
            )
        except Exception as exc:
            logger.error("Redis block write failed", extra={"error": str(exc), "ip": ip})

    # Write to PostgreSQL (non-blocking — don't raise on failure)
    try:
        now_dt = datetime.now(timezone.utc)
        record = RateLimitViolation(
            id=uuid.uuid4(),
            ip_address=ip,
            endpoint=endpoint,
            violation_count=int(violation_count),
            last_violation_at=now_dt,
            blocked_until=(
                now_dt + timedelta(seconds=_BLOCK_DURATION_SECONDS)
                if violation_count >= _BLOCK_THRESHOLD
                else None
            ),
        )
        db.add(record)
        await db.commit()
    except Exception as exc:
        logger.error("DB violation record write failed", extra={"error": str(exc)})
        try:
            await db.rollback()
        except Exception:
            pass


async def check_rate_limit(
    request: Request,
    db: AsyncSession,
    redis: Any,
    user_id: str | None = None,
    role: str | None = None,
) -> None:
    """
    Enforce rate limits for the current request.

    Checks (in order):
      1. IP whitelist — whitelisted IPs skip all checks
      2. IP block list — blocked IPs get 429 immediately
      3. Per-IP sliding window limit
      4. Per-user sliding window limit (if authenticated)

    Raises:
        HTTPException 429 with Retry-After header if any limit is exceeded.
    """
    settings = get_settings()
    client_ip = _get_client_ip(request)
    endpoint = str(request.url.path)

    # 1. Whitelist check
    if client_ip in settings.rate_limit_whitelist:
        return

    # 2. Block list check
    if await _is_ip_blocked(redis, client_ip):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="IP address is blocked due to repeated rate limit violations",
            headers={"Retry-After": str(_BLOCK_DURATION_SECONDS)},
        )

    # 3. Per-IP limit
    ip_limit = settings.rate_limit_per_minute
    ip_key = f"{_IP_RATE_KEY_PREFIX}{client_ip}"

    try:
        ip_allowed, ip_count, ip_ttl = await _sliding_window_check(
            redis, ip_key, ip_limit
        )
    except Exception as exc:
        logger.error("Redis rate limit check failed", extra={"error": str(exc)})
        return  # Fail open on Redis error

    if not ip_allowed:
        await _record_violation(redis, db, client_ip, endpoint)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limit exceeded: {ip_limit} requests per minute per IP",
            headers={
                "Retry-After": str(ip_ttl),
                "X-RateLimit-Limit": str(ip_limit),
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": str(int(time.time()) + ip_ttl),
            },
        )

    # 4. Per-user limit (if authenticated)
    if user_id:
        # api_client gets a more aggressive limit
        if role == "api_client":
            user_limit = settings.rate_limit_api_client_per_minute
        else:
            user_limit = settings.rate_limit_user_per_minute

        user_key = f"{_USER_RATE_KEY_PREFIX}{user_id}"

        try:
            user_allowed, user_count, user_ttl = await _sliding_window_check(
                redis, user_key, user_limit
            )
        except Exception as exc:
            logger.error("Redis user rate limit check failed", extra={"error": str(exc)})
            return  # Fail open

        if not user_allowed:
            await _record_violation(redis, db, client_ip, endpoint)
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit exceeded: {user_limit} requests per minute per user",
                headers={
                    "Retry-After": str(user_ttl),
                    "X-RateLimit-Limit": str(user_limit),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(int(time.time()) + user_ttl),
                },
            )


def _get_client_ip(request: Request) -> str:
    """
    Extract the real client IP from the request.

    Respects X-Forwarded-For header (set by reverse proxies like nginx).
    Falls back to the direct client IP.
    """
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        # X-Forwarded-For can contain a chain: "client, proxy1, proxy2"
        return forwarded_for.split(",")[0].strip()

    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip.strip()

    if request.client:
        return request.client.host

    return "unknown"
