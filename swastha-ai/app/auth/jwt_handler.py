"""
JWT verification against Keycloak JWKS endpoint.

Flow:
  1. Extract Bearer token from Authorization header or X-API-Key header.
  2. Fetch Keycloak's public keys from JWKS endpoint (cached in Redis 1hr).
  3. Verify the token signature, expiry, and audience.
  4. Extract user info and roles from the token claims.
  5. Upsert the user record in PostgreSQL.

This module is stateless — it never stores tokens, only validates them.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import HTTPException, Request, status
from jose import ExpiredSignatureError, JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import User, UserRoleEnum

logger = logging.getLogger(__name__)

# Sentinel hash used as the first prev_hash in the audit chain
GENESIS_HASH = "0" * 64

# Redis key for cached JWKS
_JWKS_REDIS_KEY = "swastha-ai:keycloak:jwks"
_JWKS_TTL_SECONDS = 3600  # 1 hour


class TokenData:
    """Parsed token payload, with user identity and role."""

    __slots__ = (
        "sub", "email", "full_name", "roles", "keycloak_id",
        "jti", "is_api_key", "api_key_role",
    )

    def __init__(
        self,
        sub: str,
        email: str,
        full_name: str,
        roles: list[str],
        keycloak_id: str,
        jti: str | None = None,
        is_api_key: bool = False,
        api_key_role: str | None = None,
    ) -> None:
        self.sub = sub
        self.email = email
        self.full_name = full_name
        self.roles = roles
        self.keycloak_id = keycloak_id
        self.jti = jti
        self.is_api_key = is_api_key
        self.api_key_role = api_key_role

    @property
    def primary_role(self) -> str:
        """Return the highest-privilege role the token holder has."""
        priority = ["admin", "reviewer", "portal_operator", "api_client"]
        for role in priority:
            if role in self.roles:
                return role
        return self.roles[0] if self.roles else ""


async def _fetch_jwks_from_keycloak(settings: Any) -> dict:
    """
    Fetch JWKS from Keycloak with a 10-second timeout.
    Raises HTTPException 503 if Keycloak is unreachable.
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(settings.keycloak_jwks_url)
            response.raise_for_status()
            return response.json()
    except httpx.TimeoutException:
        logger.error("Keycloak JWKS fetch timed out", extra={"url": settings.keycloak_jwks_url})
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service temporarily unavailable",
        )
    except httpx.HTTPStatusError as exc:
        logger.error(
            "Keycloak JWKS fetch failed",
            extra={"status": exc.response.status_code},
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service error",
        )


async def get_jwks(redis_client: Any) -> dict:
    """
    Return the Keycloak JWKS, using Redis as a 1-hour cache.

    If Redis is unavailable, falls back to a direct Keycloak request.
    """
    settings = get_settings()

    # Try Redis cache first
    try:
        cached = await redis_client.get(_JWKS_REDIS_KEY)
        if cached:
            return json.loads(cached)
    except Exception as exc:
        logger.warning("Redis JWKS cache read failed, fetching from Keycloak", extra={"error": str(exc)})

    # Cache miss — fetch from Keycloak
    jwks = await _fetch_jwks_from_keycloak(settings)

    # Store in Redis for 1 hour
    try:
        await redis_client.setex(_JWKS_REDIS_KEY, _JWKS_TTL_SECONDS, json.dumps(jwks))
    except Exception as exc:
        logger.warning("Redis JWKS cache write failed", extra={"error": str(exc)})

    return jwks


async def _verify_api_key(api_key: str) -> TokenData | None:
    """
    Verify an X-API-Key against the configured API key registry.

    Returns TokenData if valid, None if not found.
    This is used for machine-to-machine portal calls.
    """
    settings = get_settings()
    key_registry = settings.parsed_api_keys

    if api_key not in key_registry:
        return None

    entry = key_registry[api_key]
    role = entry.get("role", "api_client")
    description = entry.get("description", "api-client")

    # Synthetic sub for API keys — deterministic UUID from the key
    synthetic_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"apikey:{api_key}"))

    return TokenData(
        sub=synthetic_id,
        email=f"{description}@api.swastha-ai.internal",
        full_name=description,
        roles=[role],
        keycloak_id=synthetic_id,
        jti=None,
        is_api_key=True,
        api_key_role=role,
    )


def _extract_roles_from_claims(payload: dict, client_id: str) -> list[str]:
    """
    Extract role names from the token's resource_access claim.

    Keycloak puts client-specific roles under:
      payload["resource_access"][<client_id>]["roles"]
    Falls back to realm_access roles if client roles are absent.
    """
    roles: list[str] = []

    # Client-level roles (preferred)
    resource_access = payload.get("resource_access", {})
    client_access = resource_access.get(client_id, {})
    roles.extend(client_access.get("roles", []))

    # Realm-level roles (fallback)
    if not roles:
        realm_access = payload.get("realm_access", {})
        roles.extend(realm_access.get("roles", []))

    # Only accept known roles
    valid_roles = {r.value for r in UserRoleEnum}
    return [r for r in roles if r in valid_roles]


async def decode_and_verify_token(
    token: str,
    redis_client: Any,
) -> TokenData:
    """
    Decode and fully verify a Keycloak JWT token.

    Raises:
        HTTPException 401 — expired, invalid, or missing token
        HTTPException 403 — valid token but no recognised roles
    """
    settings = get_settings()

    try:
        # First pass: unverified decode to get the key ID (kid)
        unverified_header = jwt.get_unverified_header(token)
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token format",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Fetch JWKS (from Redis cache or Keycloak)
    jwks = await get_jwks(redis_client)

    # Find the matching public key by kid
    kid = unverified_header.get("kid")
    public_key = None
    for key in jwks.get("keys", []):
        if key.get("kid") == kid:
            public_key = key
            break

    if public_key is None:
        # kid not found — Keycloak may have rotated keys; refresh cache
        try:
            await redis_client.delete(_JWKS_REDIS_KEY)
        except Exception:
            pass
        jwks = await _fetch_jwks_from_keycloak(settings)
        for key in jwks.get("keys", []):
            if key.get("kid") == kid:
                public_key = key
                break

    if public_key is None:
        logger.error("JWT kid not found in JWKS", extra={"kid": kid})
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token signing key not found",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload = jwt.decode(
            token,
            public_key,
            algorithms=["RS256"],
            audience=settings.keycloak_client_id,
            options={"verify_exp": True, "verify_iat": True},
        )
    except ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except JWTError as exc:
        logger.warning("JWT decode failed", extra={"error": str(exc)})
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    roles = _extract_roles_from_claims(payload, settings.keycloak_client_id)
    if not roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Token does not contain any recognised roles",
        )

    return TokenData(
        sub=payload.get("sub", ""),
        email=payload.get("email", ""),
        full_name=payload.get("name", payload.get("preferred_username", "")),
        roles=roles,
        keycloak_id=payload.get("sub", ""),
        jti=payload.get("jti"),
    )


async def get_token_from_request(request: Request) -> str | None:
    """
    Extract the raw token string from the request.

    Priority: Authorization: Bearer <token> > X-API-Key: <key>
    Returns None if no token is present.
    """
    # Try Bearer token first
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[len("Bearer "):]

    # Try API key header
    api_key = request.headers.get("X-API-Key")
    if api_key:
        return f"__apikey__:{api_key}"

    return None


async def get_current_user(
    request: Request,
    db: AsyncSession,
    redis_client: Any,
) -> TokenData:
    """
    FastAPI dependency: verify auth and return the current user's TokenData.

    Supports both JWT Bearer tokens and X-API-Key headers.
    Upserts the user record in PostgreSQL on first login.
    """
    raw_token = await get_token_from_request(request)

    if raw_token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Handle API key auth
    if raw_token.startswith("__apikey__:"):
        api_key = raw_token[len("__apikey__:"):]
        token_data = await _verify_api_key(api_key)
        if token_data is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid API key",
            )
        try:
            await _upsert_user(db, token_data)
        except Exception:
            pass  # Upsert failure is logged inside; auth still succeeds
        return token_data

    # Handle JWT Bearer token
    token_data = await decode_and_verify_token(raw_token, redis_client)
    try:
        await _upsert_user(db, token_data)
    except Exception:
        pass  # Upsert failure is logged inside; auth still succeeds
    return token_data


async def _upsert_user(db: AsyncSession, token_data: TokenData) -> None:
    """
    Insert or update the user record in PostgreSQL from token claims.

    Uses the keycloak_id as both the lookup key AND the user UUID, ensuring
    that the user.id always matches the actor_id (current_user.sub) used in
    downstream tables, preventing FK constraint violations.
    """
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    user_id = uuid.UUID(token_data.keycloak_id)

    role_str = token_data.primary_role
    try:
        role = UserRoleEnum(role_str)
    except ValueError:
        role = UserRoleEnum.api_client

    try:
        # First try a simple lookup
        result = await db.execute(
            select(User).where(User.keycloak_id == token_data.keycloak_id)
        )
        user = result.scalar_one_or_none()

        if user is None:
            # Also check by user_id to avoid PK collisions
            result2 = await db.execute(
                select(User).where(User.id == user_id)
            )
            user = result2.scalar_one_or_none()

        if user is None:
            user = User(
                id=user_id,
                keycloak_id=token_data.keycloak_id,
                email=token_data.email,
                full_name=token_data.full_name,
                role=role,
                is_active=True,
                created_at=datetime.now(timezone.utc),
            )
            db.add(user)
        else:
            # Update existing — ensure id is correct for FK consistency
            user.keycloak_id = token_data.keycloak_id
            user.email = token_data.email
            user.full_name = token_data.full_name
            user.role = role

        await db.commit()

    except Exception as exc:
        logger.error(
            "User upsert failed — rolling back",
            extra={"error": str(exc), "keycloak_id": token_data.keycloak_id},
        )
        try:
            await db.rollback()
        except Exception:
            pass
        # Re-raise so the request fails with a clear 500 rather than a silent FK violation downstream
        raise
