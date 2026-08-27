"""write_file tool: create or overwrite with observation freshness check."""

from __future__ import annotations

from typing import Any, Dict

from .base import (
    CONTENT_TOO_LARGE,
    FILE_NOT_OBSERVED,
    FILE_STALE,
    PATH_IS_DIRECTORY,
    PATH_NOT_ALLOWED,
    WRITE_FAILED,
    ToolEffect,
    ToolExecutionError,
    ToolSpec,
)
from .file_io import atomic_write_bytes
from .observation import FileObservationTracker
from .paths import PathAccessError, normalize_rel, resolve_inside

MAX_CONTENT_BYTES = 1024 * 1024  # 1 MiB

DESCRIPTION = (
    "Create or overwrite a UTF-8 text file inside the workspace. Parent "
    "directories are created as needed. Creating requires the target not to "
    "exist; overwriting requires a fresh read_file observation in this run. "
    "Content is capped at 1 MiB and written atomically via os.replace."
)

SCHEMA = {
    "type": "object",
    "properties": {
        "path": {"type": "string", "description": "Workspace-relative target path."},
        "content": {"type": "string", "description": "Full UTF-8 file content."},
    },
    "required": ["path", "content"],
    "additionalProperties": False,
}


def _validate(args: Dict) -> Dict:
    path = args.get("path")
    if not isinstance(path, str) or not path.strip():
        raise ToolExecutionError.invalid_argument("path must be a non-empty string")
    content = args.get("content")
    if not isinstance(content, str):
        raise ToolExecutionError.invalid_argument("content must be a string")
    if len(content.encode("utf-8")) > MAX_CONTENT_BYTES:
        raise ToolExecutionError(
            CONTENT_TOO_LARGE,
            f"content is {len(content.encode('utf-8'))} bytes; maximum is {MAX_CONTENT_BYTES}",
            recovery_hint="split the change into smaller writes",
        )
    try:
        normalize_rel(path)
    except ValueError as exc:
        raise ToolExecutionError(PATH_NOT_ALLOWED, str(exc)) from exc
    return {"path": path, "content": content}


def _handle(
    args: Dict, workspace_root, tracker: FileObservationTracker
) -> Dict[str, Any]:
    rel = normalize_rel(args["path"])
    content = args["content"]
    data = content.encode("utf-8")
    try:
        target = resolve_inside(workspace_root, rel, must_exist=False)
    except PathAccessError as exc:
        raise ToolExecutionError(PATH_NOT_ALLOWED, str(exc)) from exc

    created = False
    if target.exists():
        if target.is_dir():
            raise ToolExecutionError(PATH_IS_DIRECTORY, f"target is a directory: {rel}")
        if not tracker.is_observed(rel):
            raise ToolExecutionError(
                FILE_NOT_OBSERVED,
                f"{rel} was not read in this run; read_file is required before overwrite",
                recovery_hint="call read_file first",
            )
        try:
            current = target.read_bytes()
        except OSError as exc:
            raise ToolExecutionError(
                FILE_STALE, f"cannot re-read {rel}: {exc}"
            ) from exc
        state, _ = tracker.check(rel, current)
        if state != "ok":
            raise ToolExecutionError(
                FILE_STALE,
                f"{rel} changed since it was last read",
                recovery_hint="call read_file again and retry",
            )
    else:
        created = True

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_bytes(target, data)
    except ToolExecutionError:
        raise
    except OSError as exc:
        raise ToolExecutionError(
            WRITE_FAILED, f"cannot create parent directories for {rel}: {exc}"
        ) from exc

    fingerprint = tracker.record(rel, data)
    return {
        "path": rel,
        "bytes": len(data),
        "fingerprint": fingerprint,
        "created": created,
    }


def build_write_spec(workspace_root, tracker: FileObservationTracker) -> ToolSpec:
    return ToolSpec(
        name="write_file",
        description=DESCRIPTION,
        schema=SCHEMA,
        effect=ToolEffect.WRITE,
        validator=_validate,
        handler=lambda args: _handle(args, workspace_root, tracker),
    )
