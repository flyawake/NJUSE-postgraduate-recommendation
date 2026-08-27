"""Shared helpers for glob and grep discovery."""

from __future__ import annotations

import fnmatch
import os
import re

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
