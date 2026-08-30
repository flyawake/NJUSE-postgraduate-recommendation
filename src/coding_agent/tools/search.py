"""Shared helpers for glob and grep discovery."""

from __future__ import annotations

import fnmatch
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, List, Tuple

from .base import INVALID_ARGUMENT, ToolExecutionError

SKIPPED_DIR_NAMES = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    ".idea",
    ".vscode",
    "dist",
    "build",
    "target",
}


@dataclass
class WalkBudget:
    entries: int = 0
    truncated: bool = False


def iter_bounded_files(
    base: Path, *, max_entries: int, budget: WalkBudget
) -> Iterator[Path]:
    """Yield files without allowing ``os.walk`` to materialize huge dirs.

    At most ``max_entries`` directory entries are retained/visited across the
    walk. Directory symlinks are not followed. Entries are sorted within each
    visited directory for deterministic tests and provider observations.
    """
    stack: List[Path] = [Path(base)]
    while stack:
        current = stack.pop()
        entries: List[Tuple[str, Path, bool]] = []
        stop_after_current = False
        try:
            with os.scandir(current) as iterator:
                for entry in iterator:
                    if budget.entries >= max_entries:
                        budget.truncated = True
                        stop_after_current = True
                        break
                    budget.entries += 1
                    try:
                        if entry.is_symlink() and entry.is_dir(follow_symlinks=True):
                            continue
                        is_dir = entry.is_dir(follow_symlinks=False)
                    except OSError:
                        continue
                    entries.append((entry.name, Path(entry.path), is_dir))
        except OSError:
            continue
        entries.sort(key=lambda item: item[0])
        child_dirs: List[Path] = []
        for name, path, is_dir in entries:
            if is_dir:
                if not should_skip_dir(name):
                    child_dirs.append(path)
            else:
                yield path
        if stop_after_current:
            return
        stack.extend(reversed(child_dirs))


def should_skip_dir(name: str) -> bool:
    return name in SKIPPED_DIR_NAMES or name.endswith(".egg-info")


def validate_glob_pattern(pattern: str) -> None:
    if not isinstance(pattern, str) or not pattern.strip():
        raise ToolExecutionError.invalid_argument("pattern must be a non-empty string")
    drive, _ = os.path.splitdrive(pattern)
    if drive:
        raise ToolExecutionError(
            INVALID_ARGUMENT, "pattern must be relative, not absolute"
        )
    if pattern.startswith("/") or pattern.startswith("\\"):
        raise ToolExecutionError(
            INVALID_ARGUMENT, "pattern must be relative, not absolute"
        )
    parts = pattern.replace("\\", "/").split("/")
    if any(part == ".." for part in parts):
        raise ToolExecutionError(INVALID_ARGUMENT, "'..' is not allowed in pattern")
    try:
        re.compile(fnmatch.translate(pattern))
    except re.error as exc:
        raise ToolExecutionError(
            INVALID_ARGUMENT, f"invalid glob pattern: {exc}"
        ) from exc


def matches_glob(rel_path: str, basename: str, pattern: str) -> bool:
    """Match like a file glob.

    Patterns containing a separator match the full relative path; simple
    basename patterns (no separator) match at any depth. A leading ``**/``
    also matches the basename directly so ``**/*.py`` includes top-level
    files, mirroring ``glob(recursive=True)`` behavior.
    """
    normalized_pattern = pattern.replace("\\", "/")
    candidates = [normalized_pattern]
    if normalized_pattern.startswith("**/"):
        candidates.append(normalized_pattern[3:])
    for candidate in candidates:
        if "/" in candidate:
            if fnmatch.fnmatchcase(rel_path, candidate):
                return True
        elif fnmatch.fnmatchcase(rel_path, candidate) or fnmatch.fnmatchcase(
            basename, candidate
        ):
            return True
    return False
