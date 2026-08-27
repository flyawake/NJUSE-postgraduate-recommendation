"""grep tool: bounded UTF-8 regex search over the workspace."""

from __future__ import annotations

import os
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
from .search import matches_glob, should_skip_dir, validate_glob_pattern

MAX_MATCHES = 200
MAX_PREVIEW_CHARS = 2_000

DESCRIPTION = (
    "Search UTF-8 text files under a workspace directory with a regular "
    "expression. Returns at most 200 matches with file path, 1-based line "
    "number and a preview capped at 2000 characters per line."
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
    total = 0
    skipped_files = 0
    for root, dirs, files in os.walk(base):
        dirs[:] = sorted(d for d in dirs if not should_skip_dir(d))
        for filename in sorted(files):
            full = os.path.join(root, filename)
            # Per-candidate canonical containment guard: a file symlink that
            # resolves outside the workspace is never opened.
            if not is_within_workspace(workspace_root, Path(full)):
                continue
            rel = os.path.relpath(full, base).replace(os.sep, "/")
            if include and not matches_glob(rel, filename, include):
                continue
            try:
                with open(full, "rb") as handle:
                    raw = handle.read()
                text = raw.decode("utf-8")
            except (OSError, UnicodeDecodeError):
                skipped_files += 1
                continue
            display_path = rel if rel_base == "." else f"{rel_base}/{rel}"
            for line_number, line in enumerate(text.splitlines(), start=1):
                if regex.search(line):
                    total += 1
                    if len(matches) < MAX_MATCHES:
                        preview = line
                        truncated = False
                        if len(preview) > MAX_PREVIEW_CHARS:
                            preview = preview[:MAX_PREVIEW_CHARS]
                            truncated = True
                        matches.append(
                            {
                                "file": display_path,
                                "line_number": line_number,
                                "text": preview,
                                "truncated": truncated,
                            }
                        )
    return {
        "path": rel_base,
        "pattern": pattern,
        "include": include,
        "match_count": total,
        "matches": matches,
        "omitted_count": max(0, total - len(matches)),
        "skipped_files": skipped_files,
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
