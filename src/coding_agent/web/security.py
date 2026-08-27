"""Loopback security guards: Host/Origin checks, session token, CSP.

The app is a local single-user tool, but any web page can attempt cross-site
requests to localhost. Defense in depth:

- ``Host`` must be a syntactically valid loopback host with a numeric port;
  malformed headers (including ``localhost:not-a-port``) are rejected, and
  IPv4/IPv6 literals are parsed structurally (no string-prefix tricks).
- State-changing requests must send the random per-process session token in
  ``X-Coding-Agent-Token``; browsers cannot attach this header from another
  origin without a CORS preflight, which we never grant.
- When an ``Origin`` header is present it must equal the request's effective
  scheme/host/port exactly; a different loopback port is a cross-origin
  request and is rejected.
- Responses carry a strict CSP that forbids external scripts, plus standard
  hardening headers. No wide CORS is ever configured.
"""

from __future__ import annotations

import secrets
from typing import Callable, Optional, Tuple
from urllib.parse import urlsplit

from starlette.requests import Request
from starlette.responses import JSONResponse

from ..netutil import is_loopback_host

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


def _parse_authority(value: str) -> Optional[Tuple[str, Optional[int]]]:
    """Parse a Host/Origin authority into ``(host, port)``.

    ``localhost:not-a-port`` and similar malformed values return None; IPv6
    literals must be bracketed like ``[::1]:8000``.
    """
    if not value or any(char.isspace() for char in value):
        return None
    # Authority headers never contain URL delimiters or userinfo. Reject them
    # before urlsplit can reinterpret the suffix as a path/query/fragment.
    if any(char in value for char in "/?#@\\"):
        return None
    if value.startswith("["):
        closing = value.find("]")
        if closing <= 1:
            return None
        suffix = value[closing + 1 :]
        if suffix and (not suffix.startswith(":") or not suffix[1:].isdigit()):
            return None
    else:
        if value.count(":") > 1:  # IPv6 literals must be bracketed.
            return None
        if ":" in value:
            host_text, port_text = value.rsplit(":", 1)
            if not host_text or not port_text.isdigit():
                return None
    try:
        parsed = urlsplit("//" + value)
    except ValueError:
        return None
    if (
        parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        return None
    try:
        port = parsed.port
    except ValueError:
        return None
    return parsed.hostname, port


def _effective_port(scheme: str, port: Optional[int]) -> Optional[int]:
    if port is not None:
        return port
    return {"http": 80, "https": 443}.get(scheme.lower())


def new_session_token() -> str:
    return secrets.token_urlsafe(24)


def guard_request(request: Request, session_token: str) -> None:
    """Raise ValueError with a stable code when the request is illegitimate."""
    request_authority = _parse_authority(request.headers.get("host", ""))
    if request_authority is None or not is_loopback_host(request_authority[0]):
        raise ValueError("bad_host")
    request_host, request_port = request_authority

    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        origin = request.headers.get("origin")
        if origin:
            parsed_origin = urlsplit(origin)
            origin_authority = _parse_authority(parsed_origin.netloc)
            if (
                parsed_origin.scheme != request.url.scheme
                or parsed_origin.username is not None
                or parsed_origin.password is not None
                or parsed_origin.path
                or parsed_origin.query
                or parsed_origin.fragment
                or origin_authority is None
                or origin_authority[0] != request_host
                or _effective_port(parsed_origin.scheme, origin_authority[1])
                != _effective_port(request.url.scheme, request_port)
            ):
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
