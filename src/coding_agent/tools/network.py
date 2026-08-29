"""Bounded HTTP client with SSRF and redirect protections.

The implementation deliberately bypasses ambient proxy configuration and pins
each connection to the public IP address that was validated for that hop.
"""

from __future__ import annotations

import gzip
import http.client
import ipaddress
import socket
import ssl
import zlib
from dataclasses import dataclass
from typing import Callable, Mapping, Optional
from urllib.parse import urljoin, urlsplit, urlunsplit

from .base import (
    CONTENT_TOO_LARGE,
    HTTP_ERROR,
    NETWORK_DENIED,
    NETWORK_ERROR,
    TIMEOUT,
    TOOL_ABORTED,
    ToolExecutionError,
)

MAX_RESPONSE_BYTES = 1_000_000
MAX_REDIRECTS = 3
DEFAULT_TIMEOUT_SECONDS = 12.0
USER_AGENT = "CodingAgent/0.1 (+local bounded web tool)"


@dataclass(frozen=True)
class HttpResponse:
    url: str
    status: int
    headers: Mapping[str, str]
    body: bytes


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host: str, port: int, ip: str, timeout: float) -> None:
        super().__init__(host, port, timeout=timeout)
        self._pinned_ip = ip

    def connect(self) -> None:
        self.sock = socket.create_connection(
            (self._pinned_ip, self.port), self.timeout, self.source_address
        )


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(
        self, host: str, port: int, ip: str, timeout: float, context: ssl.SSLContext
    ) -> None:
        super().__init__(host, port, timeout=timeout, context=context)
        self._pinned_ip = ip

    def connect(self) -> None:
        raw = socket.create_connection(
            (self._pinned_ip, self.port), self.timeout, self.source_address
        )
        self.sock = self._context.wrap_socket(raw, server_hostname=self.host)


def _validate_url(raw_url: str) -> tuple[str, str, int, str]:
    try:
        parsed = urlsplit(raw_url)
    except ValueError as exc:
        raise ToolExecutionError.invalid_argument("url is invalid") from exc
    if parsed.scheme not in {"http", "https"}:
        raise ToolExecutionError(NETWORK_DENIED, "only http and https URLs are allowed")
    if not parsed.hostname:
        raise ToolExecutionError.invalid_argument("url must include a hostname")
    if parsed.username is not None or parsed.password is not None:
        raise ToolExecutionError(
            NETWORK_DENIED, "URLs containing credentials are not allowed"
        )
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as exc:
        raise ToolExecutionError.invalid_argument("url port is invalid") from exc
    if port not in {80, 443}:
        raise ToolExecutionError(
            NETWORK_DENIED, "only standard HTTP and HTTPS ports are allowed"
        )
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query
    normalized = urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path or "/", parsed.query, "")
    )
    return normalized, parsed.hostname, port, path


def _resolve_public(host: str, port: int) -> str:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ToolExecutionError(
            NETWORK_ERROR, "hostname could not be resolved", retryable=True
        ) from exc
    addresses: list[str] = []
    for info in infos:
        address = str(info[4][0])
        if address not in addresses:
            addresses.append(address)
    if not addresses:
        raise ToolExecutionError(NETWORK_ERROR, "hostname returned no addresses")
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError as exc:
            raise ToolExecutionError(NETWORK_DENIED, "invalid DNS result") from exc
        if not ip.is_global:
            raise ToolExecutionError(
                NETWORK_DENIED,
                "hostname resolves to a non-public network address",
            )
    return addresses[0]


def _decode_body(body: bytes, encoding: str) -> bytes:
    encoding = encoding.lower().strip()
    try:
        if encoding == "gzip":
            decoded = gzip.decompress(body)
        elif encoding == "deflate":
            decoded = zlib.decompress(body)
        elif encoding in {"", "identity"}:
            decoded = body
        else:
            raise ToolExecutionError(
                NETWORK_ERROR, f"unsupported content encoding: {encoding}"
            )
    except (OSError, zlib.error) as exc:
        raise ToolExecutionError(
            NETWORK_ERROR, "response compression is invalid"
        ) from exc
    if len(decoded) > MAX_RESPONSE_BYTES:
        raise ToolExecutionError(CONTENT_TOO_LARGE, "web response is too large")
    return decoded


class SafeHttpClient:
    """Small GET-only client whose network boundary is straightforward to audit."""

    def __init__(
        self,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_response_bytes: int = MAX_RESPONSE_BYTES,
        resolver: Callable[[str, int], str] = _resolve_public,
        ssl_context: Optional[ssl.SSLContext] = None,
    ) -> None:
        self._timeout = timeout_seconds
        self._max_bytes = max_response_bytes
        self._resolver = resolver
        self._ssl_context = ssl_context or ssl.create_default_context()

    def get(
        self,
        url: str,
        *,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> HttpResponse:
        cancelled = is_cancelled or (lambda: False)
        current = url
        for redirect_count in range(MAX_REDIRECTS + 1):
            if cancelled():
                raise ToolExecutionError(TOOL_ABORTED, "web request was cancelled")
            normalized, host, port, path = _validate_url(current)
            ip = self._resolver(host, port)
            try:
                if normalized.startswith("https:"):
                    connection: http.client.HTTPConnection = _PinnedHTTPSConnection(
                        host, port, ip, self._timeout, self._ssl_context
                    )
                else:
                    connection = _PinnedHTTPConnection(host, port, ip, self._timeout)
                connection.request(
                    "GET",
                    path,
                    headers={
                        "Host": host if port in {80, 443} else f"{host}:{port}",
                        "User-Agent": USER_AGENT,
                        "Accept": "text/html,text/plain,application/xhtml+xml;q=0.9,*/*;q=0.1",
                        "Accept-Encoding": "gzip, deflate",
                        "Connection": "close",
                    },
                )
                response = connection.getresponse()
                raw = response.read(self._max_bytes + 1)
                headers = {key.lower(): value for key, value in response.getheaders()}
                status = response.status
            except (socket.timeout, TimeoutError) as exc:
                raise ToolExecutionError(
                    TIMEOUT, "web request timed out", True
                ) from exc
            except (OSError, http.client.HTTPException, ssl.SSLError) as exc:
                raise ToolExecutionError(
                    NETWORK_ERROR, f"web request failed: {type(exc).__name__}", True
                ) from exc
            finally:
                if "connection" in locals():
                    connection.close()
            if len(raw) > self._max_bytes:
                raise ToolExecutionError(CONTENT_TOO_LARGE, "web response is too large")
            if status in {301, 302, 303, 307, 308}:
                location = headers.get("location")
                if not location:
                    raise ToolExecutionError(
                        HTTP_ERROR, "redirect response has no location"
                    )
                if redirect_count >= MAX_REDIRECTS:
                    raise ToolExecutionError(HTTP_ERROR, "too many redirects")
                current = urljoin(normalized, location)
                continue
            if status < 200 or status >= 300:
                raise ToolExecutionError(
                    HTTP_ERROR,
                    f"web server returned HTTP {status}",
                    retryable=status in {408, 429} or status >= 500,
                )
            body = _decode_body(raw, headers.get("content-encoding", ""))
            return HttpResponse(normalized, status, headers, body)
        raise ToolExecutionError(HTTP_ERROR, "too many redirects")
