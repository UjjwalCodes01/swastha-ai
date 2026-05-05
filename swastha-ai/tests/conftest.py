"""
Pytest fixtures for the SwasthaAI test suite.

Provides:
- Test PostgreSQL session (rolls back after each test)
- Mocked MinIO client
- Mocked Kafka producer
- Mocked Redis client
- Test JWT generator (issues valid tokens for any role)
- FastAPI test client with all dependencies overridden
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from jose import jwt
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.models import Base


os.environ.setdefault(
    "SECRET_KEY",
    "test-secret-key-for-swastha-ai-layer-zero-with-at-least-thirty-two-chars",
)
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://swastha-ai:swastha_ai_secret@localhost:5432/swastha_ai_db",
)
os.environ.setdefault("MINIO_ENDPOINT", "localhost:9000")
os.environ.setdefault("MINIO_ACCESS_KEY", "swastha_ai_minio")
os.environ.setdefault("MINIO_SECRET_KEY", "swastha_ai_minio_secret")
os.environ.setdefault("KAFKA_BOOTSTRAP_SERVERS", "localhost:29092")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("KEYCLOAK_URL", "http://test-keycloak:8080")


class JSONDeleteAsyncClient(AsyncClient):
    """httpx 0.27-compatible client that allows DELETE requests with JSON."""

    async def delete(self, url: str, **kwargs: Any):
        json_body = kwargs.pop("json", None)
        if json_body is not None:
            return await self.request("DELETE", url, json=json_body, **kwargs)
        return await super().delete(url, **kwargs)


# ── Event Loop ────────────────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def event_loop_policy():
    return asyncio.DefaultEventLoopPolicy()


# ── JWT Test Utilities ────────────────────────────────────────────────────────

# Test RSA-ish secret — we use HS256 in tests for simplicity
# In production, Keycloak uses RS256; tests use HS256 with a mock verifier
_TEST_JWT_SECRET = "test-secret-key-for-swastha-ai-testing-only-not-production"
_TEST_KID = "test-key-id-001"
_TEST_KEYCLOAK_URL = "http://test-keycloak:8080"
_TEST_REALM = "swastha-ai"
_TEST_CLIENT_ID = "swastha-ai-api"


def make_test_token(
    sub: str | None = None,
    email: str = "testuser@swastha-ai.test",
    full_name: str = "Test User",
    role: str = "portal_operator",
    expired: bool = False,
    missing_roles: bool = False,
    client_id: str = _TEST_CLIENT_ID,
) -> str:
    """
    Generate a valid JWT for testing.

    Uses HS256 (symmetric) instead of RS256 (asymmetric) for simplicity.
    The JWT verifier in tests is patched to accept these tokens.
    """
    user_sub = sub or str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    if expired:
        exp = now - timedelta(hours=1)
    else:
        exp = now + timedelta(hours=1)

    claims: dict[str, Any] = {
        "sub": user_sub,
        "email": email,
        "name": full_name,
        "preferred_username": email.split("@")[0],
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
        "aud": client_id,
        "iss": f"{_TEST_KEYCLOAK_URL}/realms/{_TEST_REALM}",
        "jti": str(uuid.uuid4()),
    }

    if not missing_roles:
        claims["resource_access"] = {
            client_id: {"roles": [role]}
        }

    return jwt.encode(claims, _TEST_JWT_SECRET, algorithm="HS256")


# ── Database Fixtures ─────────────────────────────────────────────────────────


@pytest_asyncio.fixture(scope="function")
async def db_engine():
    """Create an in-memory SQLite engine for testing."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def db_session(db_engine) -> AsyncGenerator[AsyncSession, None]:
    """
    Provide a test database session that rolls back after each test.

    This keeps tests isolated without requiring a real PostgreSQL instance.
    """
    session_factory = async_sessionmaker(
        bind=db_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )
    async with session_factory() as session:
        yield session
        await session.rollback()


# ── Mock Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def mock_minio():
    """Mock MinIO client that records calls but does not hit real MinIO."""
    client = AsyncMock()
    client.upload_object = AsyncMock(return_value="raw/drug/2024/01/15/DOC-ABC123/test.pdf")
    client.generate_presigned_url = AsyncMock(return_value="https://minio.test/presigned")
    client.health_check = AsyncMock(return_value=True)
    client.startup = AsyncMock()
    client.shutdown = AsyncMock()
    return client


@pytest.fixture
def mock_document_store(mock_minio):
    """Mock DocumentStore backed by the mock MinIO client."""
    from app.storage.document_store import DocumentStore
    store = MagicMock(spec=DocumentStore)
    store.store_document = AsyncMock(
        return_value="raw/drug/2024/01/15/DOC-ABC123/test.pdf"
    )
    store.get_presigned_download_url = AsyncMock(
        return_value="https://minio.test/presigned"
    )
    return store


@pytest.fixture
def mock_kafka():
    """Mock Kafka producer that records published messages."""
    producer = AsyncMock()
    producer.publish = AsyncMock(return_value=True)
    producer.health_check = AsyncMock(return_value=True)
    producer.start = AsyncMock()
    producer.stop = AsyncMock()
    published_messages = []

    async def record_publish(topic: str, payload: dict, key: str | None = None) -> bool:
        published_messages.append({"topic": topic, "payload": payload, "key": key})
        return True

    producer.publish = record_publish
    producer.published_messages = published_messages
    return producer


@pytest.fixture
def mock_redis():
    """Mock Redis client."""
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.set = AsyncMock(return_value=True)
    redis.setex = AsyncMock(return_value=True)
    redis.delete = AsyncMock(return_value=1)
    redis.exists = AsyncMock(return_value=0)
    redis.ping = AsyncMock(return_value=True)

    # Sliding window: allow all requests through in tests
    pipeline = AsyncMock()
    pipeline.execute = AsyncMock(return_value=[0, 1, 1, True])
    pipeline.zremrangebyscore = AsyncMock()
    pipeline.zadd = AsyncMock()
    pipeline.zcard = AsyncMock()
    pipeline.expire = AsyncMock()
    pipeline.__aenter__ = AsyncMock(return_value=pipeline)
    pipeline.__aexit__ = AsyncMock(return_value=None)
    redis.pipeline = MagicMock(return_value=pipeline)

    return redis


# ── Test App Client ───────────────────────────────────────────────────────────


@pytest.fixture
def test_app(db_session, mock_document_store, mock_kafka, mock_redis):
    """
    FastAPI test app with all external dependencies overridden.

    Uses the real business logic (service.py) but mock adapters.
    """
    from app.dependencies import get_db, get_document_store, get_kafka, get_redis
    from app.main import app

    # Override dependencies
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_document_store] = lambda: mock_document_store
    app.dependency_overrides[get_kafka] = lambda: mock_kafka
    app.dependency_overrides[get_redis] = lambda: mock_redis

    yield app

    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def async_client(test_app) -> AsyncGenerator[AsyncClient, None]:
    """Async HTTP client for testing async endpoints."""
    async with JSONDeleteAsyncClient(
        transport=ASGITransport(app=test_app), base_url="http://test"
    ) as client:
        yield client


# ── Auth Headers ──────────────────────────────────────────────────────────────


def auth_headers(role: str = "portal_operator", **kwargs) -> dict[str, str]:
    """Return Authorization headers for a test token with the given role."""
    token = make_test_token(role=role, **kwargs)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def admin_headers() -> dict[str, str]:
    return auth_headers("admin")


@pytest.fixture
def portal_operator_headers() -> dict[str, str]:
    return auth_headers("portal_operator")


@pytest.fixture
def reviewer_headers() -> dict[str, str]:
    return auth_headers("reviewer")


@pytest.fixture
def api_client_headers() -> dict[str, str]:
    return auth_headers("api_client")


# ── Sample File Fixtures ──────────────────────────────────────────────────────


@pytest.fixture
def sample_pdf_bytes() -> bytes:
    """Minimal valid PDF binary for testing."""
    return (
        b"%PDF-1.4\n"
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>\nendobj\n"
        b"xref\n0 4\n0000000000 65535 f\n"
        b"trailer\n<< /Size 4 /Root 1 0 R >>\nstartxref\n9\n%%EOF"
    )


@pytest.fixture
def sample_xml_bytes() -> bytes:
    """Valid XML without XXE for testing."""
    return b'<?xml version="1.0" encoding="UTF-8"?><root><drug><name>TestDrug</name></drug></root>'


@pytest.fixture
def malicious_xml_bytes() -> bytes:
    """XML with XXE injection attempt."""
    return (
        b'<?xml version="1.0"?>'
        b'<!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
        b"<root><data>&xxe;</data></root>"
    )


@pytest.fixture
def sample_zip_bytes() -> bytes:
    """Valid zip containing a small text file."""
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("test_doc.txt", "This is a test document for SwasthaAI testing.")
    return buf.getvalue()


@pytest.fixture
def zip_bomb_bytes() -> bytes:
    """
    Simulated zip bomb: highly compressed zeros.

    Creates a zip with a high compression ratio but small actual size
    (safe for tests — the ratio is what triggers detection).
    """
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        # 10MB of zeros compresses to ~10KB — ratio ~1000:1
        zf.writestr("bomb.txt", b"\x00" * (10 * 1024 * 1024))
    return buf.getvalue()


# ── Patch JWT Verification ────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def patch_jwt_verification(mock_redis):
    """
    Patch the JWT verification to use HS256 with the test secret.

    This replaces the Keycloak JWKS fetch with a simple symmetric verification,
    allowing tests to generate valid tokens without a running Keycloak instance.
    """
    from app.auth import jwt_handler

    async def mock_decode_and_verify(token: str, redis_client: Any):
        from app.auth.jwt_handler import TokenData
        try:
            payload = jwt.decode(
                token,
                _TEST_JWT_SECRET,
                algorithms=["HS256"],
                audience=_TEST_CLIENT_ID,
                options={"verify_exp": True},
            )
            resource_access = payload.get("resource_access", {})
            client_roles = resource_access.get(_TEST_CLIENT_ID, {}).get("roles", [])
            return TokenData(
                sub=payload["sub"],
                email=payload.get("email", ""),
                full_name=payload.get("name", ""),
                roles=client_roles,
                keycloak_id=payload["sub"],
                jti=payload.get("jti"),
            )
        except Exception as e:
            from fastapi import HTTPException, status
            if "expired" in str(e).lower() or "Signature has expired" in str(e):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Token has expired",
                )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token",
            )

    with patch.object(jwt_handler, "decode_and_verify_token", mock_decode_and_verify):
        with patch.object(jwt_handler, "_upsert_user", AsyncMock()):
            yield
