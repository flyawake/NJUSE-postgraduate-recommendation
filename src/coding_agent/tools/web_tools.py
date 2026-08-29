"""Self-owned web search and bounded visible-text fetch tools."""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from typing import Callable, Dict, Optional
from urllib.parse import parse_qs, quote_plus, urlsplit

from .base import UNSUPPORTED_CONTENT, ToolEffect, ToolExecutionError, ToolSpec
from .network import HttpResponse, SafeHttpClient

MAX_QUERY_CHARS = 500
MAX_RESULTS = 10
MAX_FETCH_CHARS = 20_000


def _charset(content_type: str) -> str:
    match = re.search(r"charset\s*=\s*[\"']?([^;\s\"']+)", content_type, re.I)
    return match.group(1) if match else "utf-8"


def _decode(response: HttpResponse) -> str:
    encoding = _charset(response.headers.get("content-type", ""))
    try:
        return response.body.decode(encoding, errors="replace")
    except LookupError:
        return response.body.decode("utf-8", errors="replace")


def _unwrap_search_url(url: str) -> str:
    if url.startswith("//"):
        url = "https:" + url
    parsed = urlsplit(url)
    if parsed.hostname and parsed.hostname.endswith("duckduckgo.com"):
        target = parse_qs(parsed.query).get("uddg")
        if target:
            return target[0]
    return url


class _SearchParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[dict[str, str]] = []
        self._active: Optional[dict[str, str]] = None
        self._capture: Optional[str] = None
        self._parts: list[str] = []
        self._in_bing_result = False
        self._in_bing_heading = False

    def handle_starttag(self, tag: str, attrs) -> None:
        values = dict(attrs)
        classes = set(values.get("class", "").split())
        if tag == "li" and "b_algo" in classes:
            self._in_bing_result = True
        elif tag == "h2" and self._in_bing_result:
            self._in_bing_heading = True
        elif tag == "a" and ("result__a" in classes or self._in_bing_heading):
            self._active = {
                "title": "",
                "url": _unwrap_search_url(values.get("href", "")),
                "snippet": "",
            }
            self._capture = "title"
            self._parts = []
        elif self._active is not None and "result__snippet" in classes:
            self._capture = "snippet"
            self._parts = []
        elif tag == "p" and self._active is not None and self._in_bing_result:
            self._capture = "snippet"
            self._parts = []

    def handle_data(self, data: str) -> None:
        if self._capture:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "h2":
            self._in_bing_heading = False
        if tag == "li" and self._in_bing_result:
            if (
                self._active is not None
                and self._active["title"]
                and self._active["url"]
            ):
                self.results.append(self._active)
            self._active = None
            self._capture = None
            self._in_bing_result = False
            return
        if self._active is None or self._capture is None:
            return
        if self._capture == "title" and tag == "a":
            self._active["title"] = _clean_text(" ".join(self._parts))
            self._capture = None
        elif self._capture == "snippet" and tag in {"a", "div", "span"}:
            self._active["snippet"] = _clean_text(" ".join(self._parts))
            if self._active["title"] and self._active["url"]:
                self.results.append(self._active)
            self._active = None
            self._capture = None


class _VisibleTextParser(HTMLParser):
    _SKIP = {"script", "style", "noscript", "svg", "canvas", "template"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.parts: list[str] = []
        self._skip_depth = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in self._SKIP:
            self._skip_depth += 1
        if tag == "title":
            self._in_title = True
        if tag in {"p", "div", "section", "article", "li", "br", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP and self._skip_depth:
            self._skip_depth -= 1
        if tag == "title":
            self._in_title = False
        if tag in {"p", "div", "section", "article", "li", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._in_title:
            self.title_parts.append(data)
        self.parts.append(data)


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def _validate_search(args: Dict) -> Dict:
    query = args.get("query")
    if not isinstance(query, str) or not query.strip():
        raise ToolExecutionError.invalid_argument("query must be a non-empty string")
    query = query.strip()
    if len(query) > MAX_QUERY_CHARS:
        raise ToolExecutionError.invalid_argument("query is too long")
    max_results = args.get("max_results", 5)
    if isinstance(max_results, bool) or not isinstance(max_results, int):
        raise ToolExecutionError.invalid_argument("max_results must be an integer")
    if not 1 <= max_results <= MAX_RESULTS:
        raise ToolExecutionError.invalid_argument(
            "max_results must be between 1 and 10"
        )
    return {"query": query, "max_results": max_results}


def _validate_fetch(args: Dict) -> Dict:
    url = args.get("url")
    if not isinstance(url, str) or not url.strip():
        raise ToolExecutionError.invalid_argument("url must be a non-empty string")
    max_chars = args.get("max_chars", 12_000)
    if isinstance(max_chars, bool) or not isinstance(max_chars, int):
        raise ToolExecutionError.invalid_argument("max_chars must be an integer")
    if not 1 <= max_chars <= MAX_FETCH_CHARS:
        raise ToolExecutionError.invalid_argument(
            "max_chars must be between 1 and 20000"
        )
    return {"url": url.strip(), "max_chars": max_chars}


def _search(
    args: Dict,
    client: SafeHttpClient,
    is_cancelled: Optional[Callable[[], bool]],
) -> Dict[str, object]:
    url = "https://www.bing.com/search?q=" + quote_plus(args["query"])
    response = client.get(url, is_cancelled=is_cancelled)
    parser = _SearchParser()
    parser.feed(_decode(response))
    results = parser.results[: args["max_results"]]
    return {
        "query": args["query"],
        "provider": "bing_html",
        "count": len(results),
        "results": results,
    }


def _fetch(
    args: Dict,
    client: SafeHttpClient,
    is_cancelled: Optional[Callable[[], bool]],
) -> Dict[str, object]:
    response = client.get(args["url"], is_cancelled=is_cancelled)
    content_type = response.headers.get("content-type", "").lower()
    if not any(kind in content_type for kind in ("text/", "html", "xhtml", "json")):
        raise ToolExecutionError(
            UNSUPPORTED_CONTENT,
            f"web_fetch supports textual pages, not {content_type or 'unknown content'}",
        )
    source = _decode(response)
    title = ""
    if "html" in content_type or "<html" in source[:500].lower():
        parser = _VisibleTextParser()
        parser.feed(source)
        title = _clean_text(" ".join(parser.title_parts))
        text = _clean_text(" ".join(parser.parts))
    else:
        text = _clean_text(source)
    limit = args["max_chars"]
    truncated = len(text) > limit
    return {
        "url": response.url,
        "title": title,
        "content_type": content_type.split(";", 1)[0],
        "text": text[:limit],
        "truncated": truncated,
    }


def build_web_search_spec(
    client: Optional[SafeHttpClient] = None,
    is_cancelled: Optional[Callable[[], bool]] = None,
) -> ToolSpec:
    http = client or SafeHttpClient()
    return ToolSpec(
        name="web_search",
        description=(
            "Search the public web for current information. Returns up to 10 "
            "titles, URLs and short snippets; use web_fetch to read a result."
        ),
        schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query."},
                "max_results": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 10,
                    "description": "Maximum results. Default 5.",
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        effect=ToolEffect.READ,
        validator=_validate_search,
        handler=lambda args: _search(args, http, is_cancelled),
    )


def build_web_fetch_spec(
    client: Optional[SafeHttpClient] = None,
    is_cancelled: Optional[Callable[[], bool]] = None,
) -> ToolSpec:
    http = client or SafeHttpClient()
    return ToolSpec(
        name="web_fetch",
        description=(
            "Read visible text from one public HTTP(S) page. Requests are "
            "bounded and reject private/local network destinations."
        ),
        schema={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Public page URL."},
                "max_chars": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 20000,
                    "description": "Maximum returned characters. Default 12000.",
                },
            },
            "required": ["url"],
            "additionalProperties": False,
        },
        effect=ToolEffect.READ,
        validator=_validate_fetch,
        handler=lambda args: _fetch(args, http, is_cancelled),
    )
