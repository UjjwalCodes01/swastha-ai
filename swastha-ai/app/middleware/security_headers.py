"""
Security headers middleware.

Adds all required security headers to every HTTP response and removes
the Server header to avoid fingerprinting the technology stack.

Headers applied:
  - X-Content-Type-Options: nosniff
  - X-Frame-Options: DENY
  - X-XSS-Protection: 1; mode=block
  - Strict-Transport-Security: max-age=31536000; includeSubDomains
  - Content-Security-Policy: default-src 'self'
  - Referrer-Policy: strict-origin-when-cross-origin
  - Permissions-Policy: geolocation=(), microphone=(), camera=()

Note: HSTS is included even in development — it causes no harm locally
and prevents accidentally shipping without it in production.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

_SECURITY_HEADERS: dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "X-XSS-Protection": "1; mode=block",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "Content-Security-Policy": (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline' cdn.jsdelivr.net; "
        "img-src 'self' data: fastapi.tiangolo.com cdn.jsdelivr.net; "
        "font-src 'self' cdn.jsdelivr.net"
    ),
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
}

_HEADERS_TO_REMOVE = {"server", "x-powered-by"}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """
    ASGI middleware that enforces security response headers.

    Applied unconditionally to every response, including error responses,
    redirects, and streaming responses.
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        response = await call_next(request)

        # Add security headers
        for header, value in _SECURITY_HEADERS.items():
            response.headers[header] = value

        # Remove headers that expose server technology
        for header in _HEADERS_TO_REMOVE:
            if header in response.headers:
                del response.headers[header]

        return response
