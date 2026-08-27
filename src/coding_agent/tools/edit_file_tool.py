"""edit_file tool: literal replacement with uniqueness and freshness rules."""

from __future__ import annotations

from typing import Any, Dict

from .base import (
    CONTENT_TOO_LARGE,
    DECODE_ERROR,
    EDIT_MULTIPLE_MATCH,
    EDIT_NO_MATCH,
    FILE_NOT_OBSERVED,
    FILE_STALE,
    PATH_IS_DIRECTORY,
    PATH_NOT_ALLOWED,
    PATH_NOT_FOUND,
    ToolEffect,
    ToolExecutionError,
    ToolSpec,
)
from .file_io import atomic_write_bytes
from .observation import FileObservationTracker
from .paths import (
    PathIsDirectoryError,
    PathNotFoundError,
    existing_file,
    normalize_rel,
)

MAX_CONTENT_BYTES = 1024 * 1024  # 1 MiB

DESCRIPTION = (
    "Replace literal text in a UTF-8 file inside the workspace. The file must "
    "have been read in this run and its version must still match. Default "
    "requires exactly one match; replace_all=true requires at least one."
)

SCHEMA = {
    "type": "object",
    "properties": {
        "path": {"type": "string", "description": "Workspace-relative file path."},
        "old_string": {"type": "string", "description": "Exact text to replace."},
        "new_string": {"type": "string", "description": "Replacement text."},
        "replace_all": {
            "type": "boolean",
            "description": "Replace every occurrence. Default false.",
        },
    },
    "required": ["path", "old_string", "new_string"],
    "additionalProperties": False,
}


def _validate(args: Dict) -> Dict:
    path = args.get("path")
    if not isinstance(path, str) or not path.strip():
        raise ToolExecutionError.invalid_argument("path must be a non-empty string")
    old_string = args.get("old_string")
    if not isinstance(old_string, str) or old_string == "":
        raise ToolExecutionError.invalid_argument(
            "old_string must be a non-empty string"
        )
    new_string = args.get("new_string")
    if not isinstance(new_string, str):
        raise ToolExecutionError.invalid_argument("new_string must be a string")
    replace_all = args.get("replace_all", False)
    if not isinstance(replace_all, bool):
        raise ToolExecutionError.invalid_argument("replace_all must be a boolean")
    try:
        normalize_rel(path)
    except ValueError as exc:
        raise ToolExecutionError(PATH_NOT_ALLOWED, str(exc)) from exc
    return {
        "path": path,
        "old_string": old_string,
        "new_string": new_string,
        "replace_all": replace_all,
    }


def _handle(
    args: Dict, workspace_root, tracker: FileObservationTracker
) -> Dict[str, Any]:
    rel = normalize_rel(args["path"])
    try:
        target = existing_file(workspace_root, rel)
    except PathNotFoundError as exc:
        raise ToolExecutionError(PATH_NOT_FOUND, str(exc)) from exc
    except PathIsDirectoryError as exc:
        raise ToolExecutionError(PATH_IS_DIRECTORY, str(exc)) from exc
    except ValueError as exc:
        raise ToolExecutionError(PATH_NOT_ALLOWED, str(exc)) from exc

    try:
        current = target.read_bytes()
    except OSError as exc:
        raise ToolExecutionError(FILE_STALE, f"cannot re-read {rel}: {exc}") from exc
    if not tracker.is_observed(rel):
        raise ToolExecutionError(
            FILE_NOT_OBSERVED,
            f"{rel} was not read in this run; read_file is required before editing",
            recovery_hint="call read_file first",
        )
    state, _ = tracker.check(rel, current)
    if state != "ok":
        raise ToolExecutionError(
            FILE_STALE,
            f"{rel} changed since it was last read",
            recovery_hint="call read_file again and retry",
        )
    try:
        text = current.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ToolExecutionError(
            DECODE_ERROR,
            f"{rel} is not valid UTF-8 text",
            recovery_hint="use write_file only for text files",
        ) from exc

    old_string = args["old_string"]
    new_string = args["new_string"]
    occurrences = text.count(old_string)
    if occurrences == 0:
        raise ToolExecutionError(
            EDIT_NO_MATCH,
            f"old_string not found in {rel}",
            recovery_hint="re-read the file and verify the exact text",
        )
    if not args["replace_all"] and occurrences > 1:
        raise ToolExecutionError(
            EDIT_MULTIPLE_MATCH,
            f"old_string matches {occurrences} times in {rel}",
            recovery_hint="use a larger unique old_string or replace_all=true",
        )

    new_text = (
        text.replace(old_string, new_string)
        if args["replace_all"]
        else text.replace(old_string, new_string, 1)
    )
    data = new_text.encode("utf-8")
    if len(data) > MAX_CONTENT_BYTES:
        raise ToolExecutionError(
            CONTENT_TOO_LARGE,
            f"resulting file would be {len(data)} bytes; maximum is {MAX_CONTENT_BYTES}",
            recovery_hint="split the change into smaller edits",
        )

    atomic_write_bytes(target, data)
    fingerprint = tracker.record(rel, data)
    replacements = occurrences if args["replace_all"] else 1
    return {"path": rel, "replacements": replacements, "fingerprint": fingerprint}


def build_edit_spec(workspace_root, tracker: FileObservationTracker) -> ToolSpec:
    return ToolSpec(
        name="edit_file",
        description=DESCRIPTION,
        schema=SCHEMA,
        effect=ToolEffect.WRITE,
        validator=_validate,
        handler=lambda args: _handle(args, workspace_root, tracker),
    )
