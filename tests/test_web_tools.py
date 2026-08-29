from __future__ import annotations

import socket

import pytest

from coding_agent.tools import network as network_module
from coding_agent.tools.base import HTTP_ERROR, NETWORK_DENIED, ToolExecutionError
from coding_agent.tools.network import (
    HttpResponse,
    SafeHttpClient,
    _resolve_public,
    _validate_url,
)
from coding_agent.tools.web_tools import build_web_fetch_spec, build_web_search_spec


class FakeClient:
    def __init__(self, response: HttpResponse) -> None:
        self.response = response
        self.urls: list[str] = []

    def get(self, url, *, is_cancelled=None):
        self.urls.append(url)
        return self.response


def test_web_search_parses_and_unwraps_results():
    body = b"""
    <html><body>
      <div class="result">
        <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fdocs">Example &amp; docs</a>
        <a class="result__snippet">A useful <b>result</b>.</a>
      </div>
    </body></html>
    """
    fake = FakeClient(
        HttpResponse(
            "https://html.duckduckgo.com/html/?q=test",
            200,
            {"content-type": "text/html; charset=utf-8"},
            body,
        )
    )
    spec = build_web_search_spec(fake)
    result = spec.handler(spec.validator({"query": "test", "max_results": 3}))
    assert result["count"] == 1
    assert result["results"] == [
        {
            "title": "Example & docs",
            "url": "https://example.com/docs",
            "snippet": "A useful result .",
        }
    ]
    assert fake.urls[0].endswith("?q=test")


def test_web_fetch_extracts_visible_text_and_bounds_output():
    fake = FakeClient(
        HttpResponse(
            "https://example.com/page",
            200,
            {"content-type": "text/html"},
            b"<html><head><title>Title</title><style>hidden</style></head>"
            b"<body><h1>Hello</h1><script>secret</script><p>World</p></body></html>",
        )
    )
    spec = build_web_fetch_spec(fake)
    result = spec.handler(
        spec.validator({"url": "https://example.com/page", "max_chars": 11})
    )
    assert result["title"] == "Title"
    assert "secret" not in result["text"]
    assert "hidden" not in result["text"]
    assert result["truncated"] is True
    assert len(result["text"]) == 11


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "https://user:pass@example.com/",
        "https://example.com:8443/",
    ],
)
def test_url_validation_rejects_unsafe_forms(url):
    with pytest.raises(ToolExecutionError) as excinfo:
        _validate_url(url)
    assert excinfo.value.code == NETWORK_DENIED


def test_dns_validation_rejects_any_private_result(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443)),
        ],
    )
    with pytest.raises(ToolExecutionError) as excinfo:
        _resolve_public("example.com", 443)
    assert excinfo.value.code == NETWORK_DENIED


def test_validators_reject_unknown_or_out_of_range_values():
    search = build_web_search_spec(FakeClient(None))
    fetch = build_web_fetch_spec(FakeClient(None))
    with pytest.raises(ToolExecutionError):
        search.validator({"query": "x", "max_results": 11})
    with pytest.raises(ToolExecutionError):
        fetch.validator({"url": "https://example.com", "max_chars": 20001})


class FakeWireResponse:
    def __init__(self, status, headers=(), body=b""):
        self.status = status
        self._headers = headers
        self._body = body

    def getheaders(self):
        return self._headers

    def read(self, _limit):
        return self._body


class FakeConnection:
    def __init__(self, response):
        self.response = response
        self.request_headers = None

    def request(self, method, path, headers):
        self.request_headers = headers

    def getresponse(self):
        return self.response

    def close(self):
        return None


def test_redirect_target_is_resolved_and_private_rebinding_is_denied(monkeypatch):
    connection = FakeConnection(
        FakeWireResponse(302, [("Location", "https://private.example/secret")])
    )
    monkeypatch.setattr(
        network_module,
        "_PinnedHTTPSConnection",
        lambda *args, **kwargs: connection,
    )

    def resolver(host, port):
        if host == "private.example":
            raise ToolExecutionError(
                NETWORK_DENIED, "hostname resolves to a non-public network address"
            )
        return "93.184.216.34"

    with pytest.raises(ToolExecutionError) as excinfo:
        SafeHttpClient(resolver=resolver).get("https://example.com/start")
    assert excinfo.value.code == NETWORK_DENIED
    assert "authorization" not in {key.lower() for key in connection.request_headers}


def test_http_rate_limit_is_stable_and_retryable(monkeypatch):
    connection = FakeConnection(FakeWireResponse(429))
    monkeypatch.setattr(
        network_module,
        "_PinnedHTTPSConnection",
        lambda *args, **kwargs: connection,
    )
    with pytest.raises(ToolExecutionError) as excinfo:
        SafeHttpClient(resolver=lambda host, port: "93.184.216.34").get(
            "https://example.com/"
        )
    assert excinfo.value.code == HTTP_ERROR
    assert excinfo.value.retryable is True
