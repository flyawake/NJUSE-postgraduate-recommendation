"""glob tool: bounded standard-library file discovery."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List

from .base import PATH_NOT_ALLOWED, ToolEffect, ToolExecutionError, ToolSpec
from .paths import existing_directory, is_within_workspace, normalize_rel
from .search import WalkBudget, iter_bounded_files, matches_glob, validate_glob_pattern

MAX_MATCHES = 100
MAX_SCANNED_ENTRIES = 50_000

DESCRIPTION = (
    "Find files under a workspace directory using a glob pattern. Returns at "
    "most 100 relative paths, skips .git/.venv/node_modules and common "
    "cache/build directories, and stops after 101 matches or 50,000 entries."
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
    search_truncated = False
    walk_budget = WalkBudget()
    for full_path in iter_bounded_files(
        Path(base), max_entries=MAX_SCANNED_ENTRIES, budget=walk_budget
    ):
        if not is_within_workspace(workspace_root, full_path):
            continue
        rel = full_path.relative_to(base).as_posix()
        if matches_glob(rel, full_path.name, pattern):
            matches.append(rel if rel_base == "." else f"{rel_base}/{rel}")
            if len(matches) > MAX_MATCHES:
                search_truncated = True
                break
    search_truncated = search_truncated or walk_budget.truncated
    matches.sort()
    omitted = 1 if search_truncated else 0
    kept = matches[:MAX_MATCHES]
    return {
        "path": rel_base,
        "pattern": pattern,
        "count": len(kept),
        "matches": kept,
        "omitted_count": omitted,
        "omitted_count_is_lower_bound": search_truncated,
        "search_truncated": search_truncated,
        "scanned_entries": walk_budget.entries,
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
