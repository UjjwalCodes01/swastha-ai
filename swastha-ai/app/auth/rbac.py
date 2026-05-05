"""
Role-Based Access Control (RBAC) for the SwasthaAI ingestion API.

Defines the permission matrix for each role and provides a FastAPI
dependency factory `require_role()` for protecting endpoints.

Role hierarchy (highest to lowest):
  admin > reviewer > portal_operator > api_client
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from functools import wraps
from typing import Any

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt_handler import TokenData
from app.db.models import UserRoleEnum

logger = logging.getLogger(__name__)

# ── Permission Matrix ──────────────────────────────────────────────────────────
# Maps endpoint categories to the roles that may access them.
# This is used for documentation and enforcement.

ROLE_PERMISSIONS: dict[str, list[UserRoleEnum]] = {
    "upload_submission": [
        UserRoleEnum.admin,
        UserRoleEnum.portal_operator,
        UserRoleEnum.api_client,
    ],
    "bulk_upload": [UserRoleEnum.admin],
    "view_submission": [
        UserRoleEnum.admin,
        UserRoleEnum.reviewer,
        UserRoleEnum.portal_operator,
        UserRoleEnum.api_client,
    ],
    "delete_submission": [UserRoleEnum.admin],
    "view_admin": [UserRoleEnum.admin],
    "view_health": [],  # public — no auth required
}


def require_role(allowed_roles: list[UserRoleEnum]) -> Callable:
    """
    FastAPI dependency factory that enforces role-based access control.

    Usage:
        @router.post(
            "/endpoint",
            dependencies=[Depends(require_role([UserRoleEnum.admin]))]
        )

    Or to also receive the current user:
        async def endpoint(
            current_user: TokenData = Depends(require_role([UserRoleEnum.admin]))
        ):
            ...

    Raises:
        401 — if no valid auth token is present
        403 — if the token holder's role is not in allowed_roles
    """
    from app.dependencies import get_db, get_redis  # avoid circular import

    async def _dependency(
        request: Request,
        db: AsyncSession = Depends(get_db),
        redis_client: Any = Depends(get_redis),
    ) -> TokenData:
        from app.auth import jwt_handler

        token_data = await jwt_handler.get_current_user(request, db, redis_client)

        # Check if the user's primary role is in the allowed list
        user_role = token_data.primary_role
        allowed_role_values = [r.value for r in allowed_roles]

        if user_role not in allowed_role_values:
            logger.warning(
                "Access denied — insufficient role",
                extra={
                    "user_role": user_role,
                    "allowed_roles": allowed_role_values,
                    "endpoint": str(request.url.path),
                    "actor": token_data.email,
                },
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{user_role}' is not authorised for this operation. "
                       f"Required: {allowed_role_values}",
            )

        logger.debug(
            "RBAC check passed",
            extra={
                "user_role": user_role,
                "endpoint": str(request.url.path),
                "actor": token_data.email,
            },
        )
        return token_data

    return _dependency


def require_any_authenticated_role() -> Callable:
    """
    Dependency that requires any valid authenticated user (any role).

    Useful for endpoints like GET /status/{doc_id} that all roles can access
    but with different data filtering applied in the service layer.
    """
    return require_role([r for r in UserRoleEnum])


class RBACPolicy:
    """
    Static helper for programmatic permission checks within service code.

    Use `require_role()` for endpoint-level enforcement.
    Use this class for fine-grained checks within business logic
    (e.g., "can this reviewer see this specific submission?").
    """

    @staticmethod
    def can_view_all_submissions(role: str) -> bool:
        return role == UserRoleEnum.admin.value

    @staticmethod
    def can_delete_submission(role: str) -> bool:
        return role == UserRoleEnum.admin.value

    @staticmethod
    def can_upload(role: str) -> bool:
        return role in {
            UserRoleEnum.admin.value,
            UserRoleEnum.portal_operator.value,
            UserRoleEnum.api_client.value,
        }

    @staticmethod
    def can_bulk_upload(role: str) -> bool:
        return role == UserRoleEnum.admin.value

    @staticmethod
    def is_rate_limited_aggressively(role: str) -> bool:
        """api_client roles get more aggressive rate limiting."""
        return role == UserRoleEnum.api_client.value
