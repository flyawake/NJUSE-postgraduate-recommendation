"""Loopback security guards: Host/Origin checks, session token, CSP.

The app is a local single-user tool, but any web page can attempt cross-site
requests to localhost. Defense in depth:

- ``Host`` header must be a loopback host (anti-DNS-rebinding).
- State-changing requests must send the random per-process session token in
  ``X-Coding-Agent-Token``; browsers cannot attach this header from another
  origin without a CORS preflight, which we never grant.
- When an ``Origin`` header is present it must be same-origin loopback.
- Responses carry a strict CSP that forbids external scripts, plus standard
  hardening headers. No wide CORS is ever configured.
"""

from __future__ import annotations

import secrets
from typing import Callable
from urllib.parse import urlparse

from starlette.requests import Request
from starlette.responses import JSONResponse

SESSION_TOKEN_HEADER = "x-coding-agent-token"

CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "font-src 'self'; "
    "connect-src 'self'; "
    "object-src 'none'; "
    "base-uri 'none'; "
    "frame-ancestors 'none'; "
    "form-action 'self'"
)


def _extract_host(request: Request) -> str:
    host = request.headers.get("host", "")
    return host.split(":")[0].strip("[]").lower() if host else ""


def _is_loopback_host(host: str) -> bool:
    if not host:
        return False
    return host in ("127.0.0.1", "localhost", "::1") or host.startswith("127.")


def new_session_token() -> str:
    return secrets.token_urlsafe(24)


def guard_request(request: Request, session_token: str) -> None:
    """Raise ValueError with a stable code when the request is illegitimate."""
    host = _extract_host(request)
    if not _is_loopback_host(host):
        raise ValueError("bad_host")

    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        origin = request.headers.get("origin")
        if origin:
            parsed = urlparse(origin)
            if parsed.scheme != "http" or not _is_loopback_host(parsed.hostname or ""):
                raise ValueError("bad_origin")
        token = request.headers.get(SESSION_TOKEN_HEADER, "")
        if not secrets.compare_digest(token, session_token):
            raise ValueError("invalid_session_token")


def install_security(app, session_token: str) -> None:
    @app.middleware("http")
    async def security_middleware(request: Request, call_next: Callable):
        try:
            guard_request(request, session_token)
        except ValueError as exc:
            return JSONResponse(
                status_code=403,
                content={
                    "error": {
                        "code": str(exc),
                        "message": "请求来源校验失败",
                        "field": None,
                    }
                },
            )
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = CSP
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response
