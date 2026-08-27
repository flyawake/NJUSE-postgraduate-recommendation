"""read_file tool: numbered UTF-8 window with domain limits."""

from __future__ import annotations

from typing import Any, Dict, List

from .base import (
    DECODE_ERROR,
    PATH_IS_DIRECTORY,
    PATH_NOT_ALLOWED,
    PATH_NOT_FOUND,
    ToolEffect,
    ToolExecutionError,
    ToolSpec,
)
from .observation import FileObservationTracker
from .paths import (
    PathIsDirectoryError,
    PathNotFoundError,
    existing_file,
    normalize_rel,
)

DEFAULT_OFFSET = 1
DEFAULT_LIMIT = 200
MAX_LIMIT = 500
MAX_WINDOW_BYTES = 50 * 1024
MAX_LINE_CHARS = 2_000

DESCRIPTION = (
    "Read a UTF-8 text file window with 1-based line numbers. Defaults to "
    "offset=1, limit=200; limit is capped at 500 lines and one window is "
    "capped at 50 KiB. Returns total_lines, a next-offset hint and a SHA-256 "
    "fingerprint of the raw file bytes."
)

SCHEMA = {
    "type": "object",
    "properties": {
        "path": {"type": "string", "description": "Workspace-relative file path."},
        "offset": {
            "type": "integer",
            "description": "1-based first line. Default 1.",
        },
        "limit": {
            "type": "integer",
            "description": "Maximum lines to return (1-500). Default 200.",
        },
    },
    "required": ["path"],
    "additionalProperties": False,
}


def _validate(args: Dict) -> Dict:
    path = args.get("path")
    if not isinstance(path, str) or not path.strip():
        raise ToolExecutionError.invalid_argument("path must be a non-empty string")
    offset = args.get("offset", DEFAULT_OFFSET)
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 1:
        raise ToolExecutionError.invalid_argument("offset must be an integer >= 1")
    limit = args.get("limit", DEFAULT_LIMIT)
    if (
        isinstance(limit, bool)
        or not isinstance(limit, int)
        or not (1 <= limit <= MAX_LIMIT)
    ):
        raise ToolExecutionError.invalid_argument(
            f"limit must be an integer between 1 and {MAX_LIMIT}"
        )
    return {"path": path, "offset": offset, "limit": limit}


def _handle(
    args: Dict, workspace_root, tracker: FileObservationTracker
) -> Dict[str, Any]:
    try:
        rel = normalize_rel(args["path"])
    except ValueError as exc:
        raise ToolExecutionError(PATH_NOT_ALLOWED, str(exc)) from exc
    try:
        target = existing_file(workspace_root, rel)
    except PathNotFoundError as exc:
        raise ToolExecutionError(
            PATH_NOT_FOUND, str(exc), recovery_hint="check the path spelling"
        ) from exc
    except PathIsDirectoryError as exc:
        raise ToolExecutionError(
            PATH_IS_DIRECTORY, str(exc), recovery_hint="point read_file at a file"
        ) from exc
    except ValueError as exc:
        raise ToolExecutionError(PATH_NOT_ALLOWED, str(exc)) from exc

    try:
        raw = target.read_bytes()
    except OSError as exc:
        raise ToolExecutionError(PATH_NOT_FOUND, f"cannot read {rel}: {exc}") from exc
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ToolExecutionError(
            DECODE_ERROR,
            f"{rel} is not valid UTF-8 text",
            recovery_hint="use a text file or run_command for binary inspection",
        ) from exc

    fingerprint = tracker.record(rel, raw)
    lines = text.splitlines()
    total_lines = len(lines)
    offset = args["offset"]
    limit = args["limit"]

    window: List[dict] = []
    window_bytes = 0
    window_truncated = False
    for index in range(offset - 1, min(offset - 1 + limit, total_lines)):
        line = lines[index]
        truncated = False
        if len(line) > MAX_LINE_CHARS:
            line = line[:MAX_LINE_CHARS]
            truncated = True
        encoded = line.encode("utf-8")
        if window_bytes + len(encoded) > MAX_WINDOW_BYTES:
            window_truncated = True
            break
        window_bytes += len(encoded)
        window.append({"number": index + 1, "text": line, "truncated": truncated})

    requested_end = min(offset - 1 + limit, total_lines)
    included_end = offset - 1 + len(window)
    omitted_lines = max(0, requested_end - included_end)
    next_offset = included_end + 1 if included_end < total_lines else None

    return {
        "path": rel,
        "offset": offset,
        "limit": limit,
        "total_lines": total_lines,
        "fingerprint": fingerprint,
        "window_bytes": window_bytes,
        "lines": window,
        "window_truncated": window_truncated or omitted_lines > 0,
        "omitted_lines": omitted_lines,
        "next_offset": next_offset,
    }


def build_read_spec(workspace_root, tracker: FileObservationTracker) -> ToolSpec:
    return ToolSpec(
        name="read_file",
        description=DESCRIPTION,
        schema=SCHEMA,
        effect=ToolEffect.READ,
        validator=_validate,
        handler=lambda args: _handle(args, workspace_root, tracker),
    )
