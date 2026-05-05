"""
Request ID middleware.

Generates a unique UUID for every incoming request and:
1. Attaches it as X-Request-ID response header
2. Stores it in a contextvar so all log entries within the request
   automatically include the request ID (via the logging formatter).

If the client sends an X-Request-ID header, that value is used instead
(for distributed tracing across services).
"""

from __future__ import annotations

import logging
import uuid
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger(__name__)

# ContextVar that holds the current request ID for the duration of the request.
# This is safe across async tasks — each request gets its own context.
request_id_var: ContextVar[str] = ContextVar("request_id", default="")


def get_request_id() -> str:
    """Return the current request ID, or empty string if not in a request context."""
    return request_id_var.get("")


class RequestIDMiddleware(BaseHTTPMiddleware):
    """
    ASGI middleware that assigns a unique X-Request-ID to every request.

    The request ID is available to:
    - Response headers (X-Request-ID)
    - Log entries (via get_request_id())
    - Downstream services (forward the header in outbound calls)
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        # Use client-supplied ID if present, otherwise generate a new UUID
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())

        # Store in context var so logging can access it
        token = request_id_var.set(request_id)

        try:
            response = await call_next(request)
        finally:
            # Always reset the context var, even if the handler raises
            request_id_var.reset(token)

        response.headers["X-Request-ID"] = request_id
        return response
