"""grep tool: bounded UTF-8 regex search over the workspace."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List

from .base import (
    INVALID_ARGUMENT,
    PATH_NOT_ALLOWED,
    ToolEffect,
    ToolExecutionError,
    ToolSpec,
)
from .paths import existing_directory, is_within_workspace, normalize_rel
from .search import WalkBudget, iter_bounded_files, matches_glob, validate_glob_pattern

MAX_MATCHES = 200
MAX_PREVIEW_CHARS = 2_000
MAX_PATTERN_CHARS = 512
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_SCANNED_BYTES = 64 * 1024 * 1024
MAX_SCANNED_FILES = 10_000
MAX_SCANNED_ENTRIES = 50_000
MAX_SEARCH_LINE_CHARS = 20_000

DESCRIPTION = (
    "Search UTF-8 text files under a workspace directory with a regular "
    "expression. Returns at most 200 matches with file path, 1-based line "
    "number and a preview capped at 2000 characters per line. The search "
    "stops after 200 matches or a bounded 64 MiB/10,000-file scan."
)

SCHEMA = {
    "type": "object",
    "properties": {
        "pattern": {
            "type": "string",
            "description": "Python regular expression to search for.",
        },
        "path": {
            "type": "string",
            "description": "Workspace-relative directory to search. Default '.'.",
        },
        "include": {
            "type": "string",
            "description": "Optional glob filter for file names, e.g. '*.py'.",
        },
    },
    "required": ["pattern"],
    "additionalProperties": False,
}


def _validate(args: Dict) -> Dict:
    pattern = args.get("pattern")
    if not isinstance(pattern, str) or not pattern.strip():
        raise ToolExecutionError.invalid_argument("pattern must be a non-empty string")
    if len(pattern) > MAX_PATTERN_CHARS:
        raise ToolExecutionError.invalid_argument(
            f"pattern must be at most {MAX_PATTERN_CHARS} characters"
        )
    try:
        re.compile(pattern)
    except re.error as exc:
        raise ToolExecutionError(
            INVALID_ARGUMENT, f"invalid regular expression: {exc}"
        ) from exc
    path = args.get("path", ".")
    if not isinstance(path, str) or not path.strip():
        raise ToolExecutionError.invalid_argument("path must be a non-empty string")
    try:
        normalize_rel(path)
    except ValueError as exc:
        raise ToolExecutionError(PATH_NOT_ALLOWED, str(exc)) from exc
    include = args.get("include")
    if include is not None:
        validate_glob_pattern(include)
    return {"pattern": pattern, "path": path, "include": include}


def _handle(args: Dict, workspace_root) -> Dict[str, object]:
    pattern = args["pattern"]
    include = args.get("include")
    regex = re.compile(pattern)
    rel_base = normalize_rel(args["path"])
    base = existing_directory(workspace_root, args["path"])

    matches: List[dict] = []
    search_truncated = False
    skipped_files = 0
    oversized_files = 0
    scanned_files = 0
    scanned_bytes = 0
    walk_budget = WalkBudget()
    for full_path in iter_bounded_files(
        Path(base), max_entries=MAX_SCANNED_ENTRIES, budget=walk_budget
    ):
        if scanned_files >= MAX_SCANNED_FILES:
            search_truncated = True
            break
        if not is_within_workspace(workspace_root, full_path):
            continue
        rel = full_path.relative_to(base).as_posix()
        filename = full_path.name
        if include and not matches_glob(rel, filename, include):
            continue
        try:
            file_size = full_path.stat().st_size
            if file_size > MAX_FILE_BYTES:
                oversized_files += 1
                continue
            if scanned_bytes + file_size > MAX_SCANNED_BYTES:
                search_truncated = True
                break
            scanned_files += 1
            scanned_bytes += file_size
            handle = full_path.open("r", encoding="utf-8", errors="strict")
        except OSError:
            skipped_files += 1
            continue
        display_path = rel if rel_base == "." else f"{rel_base}/{rel}"
        matches_before_file = len(matches)
        try:
            with handle:
                for line_number, raw_line in enumerate(handle, start=1):
                    line = raw_line.rstrip("\r\n")
                    searchable = line[:MAX_SEARCH_LINE_CHARS]
                    if not regex.search(searchable):
                        continue
                    if len(matches) >= MAX_MATCHES:
                        search_truncated = True
                        break
                    preview = line[:MAX_PREVIEW_CHARS]
                    matches.append(
                        {
                            "file": display_path,
                            "line_number": line_number,
                            "text": preview,
                            "truncated": len(line) > MAX_PREVIEW_CHARS,
                        }
                    )
        except UnicodeDecodeError:
            del matches[matches_before_file:]
            skipped_files += 1
            continue
        if search_truncated:
            break
    search_truncated = search_truncated or walk_budget.truncated
    return {
        "path": rel_base,
        "pattern": pattern,
        "include": include,
        "match_count": len(matches),
        "matches": matches,
        "omitted_count": 1 if search_truncated else 0,
        "omitted_count_is_lower_bound": search_truncated,
        "search_truncated": search_truncated,
        "skipped_files": skipped_files,
        "oversized_files": oversized_files,
        "scanned_files": scanned_files,
        "scanned_bytes": scanned_bytes,
        "scanned_entries": walk_budget.entries,
    }


def build_grep_spec(workspace_root) -> ToolSpec:
    return ToolSpec(
        name="grep",
        description=DESCRIPTION,
        schema=SCHEMA,
        effect=ToolEffect.READ,
        validator=_validate,
        handler=lambda args: _handle(args, workspace_root),
    )
