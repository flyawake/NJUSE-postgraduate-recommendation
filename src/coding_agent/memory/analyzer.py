"""Deterministic hybrid lexical analyzer for memory text.

The analyzer is intentionally dependency-free and explainable. It normalizes
Unicode, keeps Latin/code-identifier tokens, and segments continuous CJK text
into overlapping 2-grams (falling back to single-character terms for very short
runs). The same analyzer is used for writes and queries so FTS5 and the terms
fallback return the same candidate set.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Iterable, List, Sequence

from .models import MEMORY_MAX_TERMS_PER_ENTRY

_LATIN_TOKEN_RE = re.compile(r"[A-Za-z0-9]+(?:[._-][A-Za-z0-9]+)*")
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+")
_WS_RE = re.compile(r"\s+")

_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "do",
        "does",
        "for",
        "from",
        "i",
        "in",
        "is",
        "it",
        "me",
        "my",
        "no",
        "not",
        "of",
        "on",
        "or",
        "so",
        "that",
        "the",
        "this",
        "to",
        "we",
        "with",
        "you",
        "your",
    }
)


def normalize_text(text: str) -> str:
    """Return NFC/case-folded, whitespace-collapsed text used for indexing."""
    if not text:
        return ""
    normalized = unicodedata.normalize("NFKC", text).casefold()
    normalized = _WS_RE.sub(" ", normalized).strip()
    return normalized


def _cjk_tokens(run: str) -> List[str]:
    if not run:
        return []
    # For very short runs, single characters are the only stable signal.
    if len(run) == 1:
        return [run]
    tokens: List[str] = []
    for index in range(len(run) - 1):
        tokens.append(run[index : index + 2])
    # Keep unigrams too for short queries and to avoid a 2-gram-only blind spot.
    if len(run) <= 4:
        tokens.extend(list(run))
    return tokens


def tokenize(text: str, *, max_terms: int = MEMORY_MAX_TERMS_PER_ENTRY) -> List[str]:
    """Return de-duplicated, bounded normalized search terms."""
    if not text:
        return []
    normalized = normalize_text(text)
    tokens: List[str] = []
    seen = set()

    def add(token: str) -> None:
        if not token or token in seen or len(tokens) >= max_terms:
            return
        if token in _STOP_WORDS:
            return
        seen.add(token)
        tokens.append(token)

    for match in _LATIN_TOKEN_RE.finditer(normalized):
        identifier = match.group(0)
        add(identifier)
        for part in re.split(r"[._-]+", identifier):
            add(part)
    for match in _CJK_RE.finditer(normalized):
        for token in _cjk_tokens(match.group(0)):
            add(token)
    return tokens


def searchable_text(text: str) -> str:
    """Join analyzer tokens into the string stored in FTS5."""
    return " ".join(tokenize(text))


def normalized_hash(content: str) -> str:
    """Stable hash of the normalized content for near-duplicate detection."""
    normalized = normalize_text(content)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def terms_for_query(query: str, *, max_terms: int = 24) -> List[str]:
    """Bound the number of query terms to keep FTS MATCH cheap."""
    return tokenize(query, max_terms=max_terms)


def format_query_terms(terms: Sequence[str]) -> str:
    """Escape each term and join with OR for FTS5 MATCH."""
    escaped = []
    for term in terms:
        # FTS5 phrase quoting; our normalized tokens contain no double quotes,
        # but escaping makes the boundary fail-closed.
        escaped.append('"' + term.replace('"', '""') + '"')
    return " OR ".join(escaped)


def dedupe_terms(terms: Iterable[str]) -> List[str]:
    result: List[str] = []
    seen = set()
    for term in terms:
        if term not in seen:
            seen.add(term)
            result.append(term)
    return result
