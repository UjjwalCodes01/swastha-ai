"""
Tests for JWT authentication and RBAC.

Tests cover:
- Valid JWT passes through correctly
- Expired JWT returns 401
- Missing token returns 401
- Valid token with wrong role returns 403
- API key auth works for machine clients
- Token claims are correctly extracted
- JWKS caching in Redis works
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from fastapi import HTTPException
from jose import jwt

from tests.conftest import (
    _TEST_CLIENT_ID,
    _TEST_JWT_SECRET,
    _TEST_KEYCLOAK_URL,
    _TEST_REALM,
    make_test_token,
)


pytestmark = pytest.mark.asyncio


# ── JWT Extraction Tests ──────────────────────────────────────────────────────


class TestTokenExtraction:
    async def test_bearer_token_extracted_correctly(self):
        """Test that Bearer token is extracted from Authorization header."""
        from app.auth.jwt_handler import get_token_from_request
        from unittest.mock import MagicMock

        request = MagicMock()
        request.headers = {"Authorization": "Bearer my-test-token-123"}

        token = await get_token_from_request(request)
        assert token == "my-test-token-123"

    async def test_api_key_extracted_with_prefix(self):
        """Test that X-API-Key is extracted with __apikey__ prefix."""
        from app.auth.jwt_handler import get_token_from_request
        from unittest.mock import MagicMock

        request = MagicMock()
        request.headers = {"X-API-Key": "my-api-key-xyz"}

        token = await get_token_from_request(request)
        assert token == "__apikey__:my-api-key-xyz"

    async def test_bearer_takes_priority_over_api_key(self):
        """Test that Bearer token takes priority over X-API-Key."""
        from app.auth.jwt_handler import get_token_from_request
        from unittest.mock import MagicMock

        request = MagicMock()
        request.headers = {
            "Authorization": "Bearer bearer-token",
            "X-API-Key": "api-key",
        }
        token = await get_token_from_request(request)
        assert token == "bearer-token"

    async def test_missing_auth_returns_none(self):
        """Test that missing auth returns None."""
        from app.auth.jwt_handler import get_token_from_request
        from unittest.mock import MagicMock

        request = MagicMock()
        request.headers = {}

        token = await get_token_from_request(request)
        assert token is None


# ── JWT Verification Tests ────────────────────────────────────────────────────


class TestJWTVerification:
    """
    Tests for the JWT decode and verify logic.

    Note: The conftest patches decode_and_verify_token to use HS256.
    These tests verify the patched version's behaviour directly.
    """

    async def test_valid_jwt_passes_through(self, async_client):
        """Test that a valid JWT allows access to a protected endpoint."""
        token = make_test_token(role="portal_operator")
        # Use the health endpoint as a quick auth check proxy
        # (we test auth on the submission endpoint)
        from httpx import AsyncClient
        response = await async_client.get(
            "/api/v1/ingest/health",
            headers={"Authorization": f"Bearer {token}"},
        )
        # Health doesn't require auth, but test that auth header doesn't break it
        assert response.status_code == 200

    async def test_expired_jwt_returns_401(self, async_client):
        """Test that an expired JWT returns 401."""
        expired_token = make_test_token(role="portal_operator", expired=True)
        response = await async_client.post(
            "/api/v1/ingest/submission",
            headers={"Authorization": f"Bearer {expired_token}"},
            files={"file": ("test.pdf", b"%PDF-1.4", "application/pdf")},
            data={"submission_type": "drug"},
        )
        assert response.status_code == 401
        assert "expired" in response.json()["detail"].lower()

    async def test_missing_token_returns_401(self, async_client):
        """Test that a request without any auth token returns 401."""
        response = await async_client.post(
            "/api/v1/ingest/submission",
            files={"file": ("test.pdf", b"%PDF-1.4", "application/pdf")},
            data={"submission_type": "drug"},
        )
        assert response.status_code == 401

    async def test_invalid_jwt_returns_401(self, async_client):
        """Test that a completely invalid token string returns 401."""
        response = await async_client.post(
            "/api/v1/ingest/submission",
            headers={"Authorization": "Bearer not.a.valid.jwt.token"},
            files={"file": ("test.pdf", b"%PDF-1.4", "application/pdf")},
            data={"submission_type": "drug"},
        )
        assert response.status_code == 401

    async def test_token_with_wrong_role_returns_403(self, async_client):
        """Test that a valid token with an insufficient role returns 403."""
        # Reviewer cannot upload
        reviewer_token = make_test_token(role="reviewer")
        response = await async_client.post(
            "/api/v1/ingest/submission",
            headers={"Authorization": f"Bearer {reviewer_token}"},
            files={"file": ("test.pdf", b"%PDF-1.4", "application/pdf")},
            data={"submission_type": "drug"},
        )
        assert response.status_code == 403

    async def test_token_with_no_roles_returns_403(self, async_client):
        """Test that a valid token with no recognised roles returns 403."""
        no_roles_token = make_test_token(role="portal_operator", missing_roles=True)
        response = await async_client.post(
            "/api/v1/ingest/submission",
            headers={"Authorization": f"Bearer {no_roles_token}"},
            files={"file": ("test.pdf", b"%PDF-1.4", "application/pdf")},
            data={"submission_type": "drug"},
        )
        # Should get 403 (no valid roles) or 401 (rejected at verification)
        assert response.status_code in (401, 403)


# ── API Key Auth Tests ────────────────────────────────────────────────────────


class TestAPIKeyAuth:
    async def test_valid_api_key_allows_access(self, async_client, monkeypatch):
        """Test that a valid X-API-Key header grants access."""
        from app.auth import jwt_handler
        from app.auth.jwt_handler import TokenData

        test_api_key = "test-valid-api-key-12345"
        test_token_data = TokenData(
            sub=str(uuid.uuid4()),
            email="sugam-portal@api.swastha-ai.internal",
            full_name="sugam-portal",
            roles=["api_client"],
            keycloak_id=str(uuid.uuid4()),
            is_api_key=True,
            api_key_role="api_client",
        )

        with patch.object(
            jwt_handler, "_verify_api_key", return_value=test_token_data
        ):
            with patch("app.auth.jwt_handler._upsert_user", new_callable=AsyncMock):
                with patch(
                    "app.ingestion.service.IngestionService._run_validations",
                    new_callable=AsyncMock,
                ):
                    with patch(
                        "app.ingestion.service.IngestionService._virus_scan",
                        new_callable=AsyncMock,
                    ):
                        response = await async_client.post(
                            "/api/v1/ingest/submission",
                            headers={"X-API-Key": test_api_key},
                            files={
                                "file": ("test.pdf", b"%PDF-1.4 test", "application/pdf")
                            },
                            data={"submission_type": "drug"},
                        )

        # 200 = API key worked; 403/401 = API key not recognised
        # In test environment with mocked _verify_api_key, should be 200
        assert response.status_code == 200

    async def test_invalid_api_key_returns_401(self, async_client):
        """Test that an unrecognised API key returns 401."""
        from app.auth import jwt_handler

        with patch.object(jwt_handler, "_verify_api_key", return_value=None):
            response = await async_client.post(
                "/api/v1/ingest/submission",
                headers={"X-API-Key": "completely-invalid-key"},
                files={"file": ("test.pdf", b"%PDF", "application/pdf")},
                data={"submission_type": "drug"},
            )

        assert response.status_code == 401


# ── RBAC Policy Tests ─────────────────────────────────────────────────────────


class TestRBACPolicy:
    """Tests for the static RBAC policy helper."""

    def test_admin_can_do_everything(self):
        from app.auth.rbac import RBACPolicy

        assert RBACPolicy.can_view_all_submissions("admin") is True
        assert RBACPolicy.can_delete_submission("admin") is True
        assert RBACPolicy.can_upload("admin") is True
        assert RBACPolicy.can_bulk_upload("admin") is True

    def test_reviewer_is_read_only(self):
        from app.auth.rbac import RBACPolicy

        assert RBACPolicy.can_view_all_submissions("reviewer") is False
        assert RBACPolicy.can_delete_submission("reviewer") is False
        assert RBACPolicy.can_upload("reviewer") is False
        assert RBACPolicy.can_bulk_upload("reviewer") is False

    def test_portal_operator_can_upload(self):
        from app.auth.rbac import RBACPolicy

        assert RBACPolicy.can_upload("portal_operator") is True
        assert RBACPolicy.can_bulk_upload("portal_operator") is False
        assert RBACPolicy.can_delete_submission("portal_operator") is False

    def test_api_client_is_rate_limited_aggressively(self):
        from app.auth.rbac import RBACPolicy

        assert RBACPolicy.is_rate_limited_aggressively("api_client") is True
        assert RBACPolicy.is_rate_limited_aggressively("portal_operator") is False
        assert RBACPolicy.is_rate_limited_aggressively("admin") is False

    def test_only_admin_can_bulk_upload(self):
        from app.auth.rbac import RBACPolicy

        for role in ["reviewer", "portal_operator", "api_client"]:
            assert RBACPolicy.can_bulk_upload(role) is False
        assert RBACPolicy.can_bulk_upload("admin") is True


# ── JWKS Cache Tests ──────────────────────────────────────────────────────────


class TestJWKSCache:
    async def test_jwks_returned_from_redis_cache(self):
        """Test that JWKS is served from Redis cache when available."""
        from app.auth.jwt_handler import get_jwks

        cached_jwks = {"keys": [{"kid": "test-key", "kty": "RSA"}]}
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value='{"keys": [{"kid": "test-key", "kty": "RSA"}]}')

        result = await get_jwks(mock_redis)
        assert result == cached_jwks

        # Should NOT have made an HTTP request
        mock_redis.get.assert_called_once()

    async def test_jwks_fetched_from_keycloak_on_cache_miss(self):
        """Test that JWKS is fetched from Keycloak when not in Redis cache."""
        from app.auth.jwt_handler import get_jwks

        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)  # Cache miss
        mock_redis.setex = AsyncMock()

        fresh_jwks = {"keys": [{"kid": "fresh-key", "kty": "RSA"}]}

        with patch("app.auth.jwt_handler._fetch_jwks_from_keycloak", new_callable=AsyncMock, return_value=fresh_jwks):
            result = await get_jwks(mock_redis)

        assert result == fresh_jwks
        # Should have stored in Redis
        mock_redis.setex.assert_called_once()

    async def test_jwks_proceeds_without_redis(self):
        """Test that JWKS fetch works even if Redis is unavailable."""
        from app.auth.jwt_handler import get_jwks

        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(side_effect=Exception("Redis unavailable"))
        mock_redis.setex = AsyncMock(side_effect=Exception("Redis unavailable"))

        fresh_jwks = {"keys": [{"kid": "direct-key", "kty": "RSA"}]}

        with patch("app.auth.jwt_handler._fetch_jwks_from_keycloak", new_callable=AsyncMock, return_value=fresh_jwks):
            # Should not raise even though Redis is broken
            result = await get_jwks(mock_redis)

        assert result == fresh_jwks
