"""glob tool: bounded standard-library file discovery."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List

from .base import PATH_NOT_ALLOWED, ToolEffect, ToolExecutionError, ToolSpec
from .paths import existing_directory, is_within_workspace, normalize_rel
from .search import matches_glob, should_skip_dir, validate_glob_pattern

MAX_MATCHES = 100

DESCRIPTION = (
    "Find files under a workspace directory using a glob pattern. Returns at "
    "most 100 relative paths, skips .git/.venv/node_modules and common "
    "cache/build directories, and reports omitted counts."
)

SCHEMA = {
    "type": "object",
    "properties": {
        "pattern": {
            "type": "string",
            "description": "Glob pattern, e.g. '**/*.py' or '*.txt'.",
        },
        "path": {
            "type": "string",
            "description": "Workspace-relative directory to search. Default '.'.",
        },
    },
    "required": ["pattern"],
    "additionalProperties": False,
}


def _validate(args: Dict) -> Dict:
    pattern = args.get("pattern")
    validate_glob_pattern(pattern)
    path = args.get("path", ".")
    if not isinstance(path, str) or not path.strip():
        raise ToolExecutionError.invalid_argument("path must be a non-empty string")
    try:
        normalize_rel(path)
    except ValueError as exc:
        raise ToolExecutionError(PATH_NOT_ALLOWED, str(exc)) from exc
    return {"pattern": pattern, "path": path}


def _handle(args: Dict, workspace_root) -> Dict[str, object]:
    pattern = args["pattern"]
    rel_base = normalize_rel(args["path"])
    base = existing_directory(workspace_root, args["path"])
    matches: List[str] = []
    for root, dirs, files in os.walk(base):
        dirs[:] = sorted(d for d in dirs if not should_skip_dir(d))
        for filename in sorted(files):
            full = os.path.join(root, filename)
            if not is_within_workspace(workspace_root, Path(full)):
                continue
            rel = os.path.relpath(full, base).replace(os.sep, "/")
            if matches_glob(rel, filename, pattern):
                matches.append(rel if rel_base == "." else f"{rel_base}/{rel}")
    matches.sort()
    omitted = max(0, len(matches) - MAX_MATCHES)
    kept = matches[:MAX_MATCHES]
    return {
        "path": rel_base,
        "pattern": pattern,
        "count": len(kept),
        "matches": kept,
        "omitted_count": omitted,
        "hint": "narrow pattern or path to see omitted matches" if omitted else None,
    }


def build_glob_spec(workspace_root) -> ToolSpec:
    return ToolSpec(
        name="glob",
        description=DESCRIPTION,
        schema=SCHEMA,
        effect=ToolEffect.READ,
        validator=_validate,
        handler=lambda args: _handle(args, workspace_root),
    )
