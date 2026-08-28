"""Bounded unified-diff generation for text artifacts.

The backend deliberately returns a simple structured line list (not HTML).
The frontend escapes every line before rendering.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from typing import List, Optional

MAX_DIFF_LINES = 20_000
MAX_FILE_BYTES = 1024 * 1024


@dataclass(frozen=True)
class DiffResult:
    lines: List[str]
    additions: int = 0
    deletions: int = 0
    truncated: bool = False
    line_count: int = 0


def has_newline_marker(text: str, line: str) -> bool:
    """Return whether ``line`` carries the standard no-newline-at-EOF marker."""
    return line.endswith("\\ No newline at end of file")


def build_diff(
    before: Optional[str],
    after: Optional[str],
    *,
    max_lines: int = MAX_DIFF_LINES,
) -> DiffResult:
    def bounded(lines: List[str], additions: int, deletions: int) -> DiffResult:
        return DiffResult(
            lines=lines[:max_lines],
            additions=additions,
            deletions=deletions,
            truncated=len(lines) > max_lines,
            line_count=len(lines),
        )

    if before is None and after is None:
        return DiffResult(lines=[], truncated=False)
    if before is None:
        lines = [f"+ {line}" for line in (after or "").splitlines()]
        return bounded(lines, len(lines), 0)
    if after is None:
        lines = [f"- {line}" for line in before.splitlines()]
        return bounded(lines, 0, len(lines))
    before_lines = before.splitlines()
    after_lines = after.splitlines()
    unified = list(
        difflib.unified_diff(
            before_lines,
            after_lines,
            fromfile="before",
            tofile="after",
            lineterm="",
            n=3,
        )
    )
    additions = sum(
        1 for line in unified if line.startswith("+") and not line.startswith("+++")
    )
    deletions = sum(
        1 for line in unified if line.startswith("-") and not line.startswith("---")
    )
    return bounded(unified, additions, deletions)
