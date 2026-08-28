"""Provider-neutral redaction for user-visible tool and command summaries.

AgentEvent has consumers beyond the web UI (notably the CLI), so sensitive
tool values must be removed before an event leaves AgentLoop. The web adapter
applies the same functions again as defense in depth.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Sequence

_SAFE_FLAG_RE = re.compile(r"^-{1,2}[A-Za-z0-9][A-Za-z0-9._-]*$")
_SAFE_ASSIGNMENT_KEY_RE = re.compile(
    r"^(?:-{1,2}[A-Za-z0-9][A-Za-z0-9._-]*|[A-Za-z_][A-Za-z0-9_]*)$"
)
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]+")

PUBLIC_TOOL_TARGET_MAX_CHARS = 160


def redact_command_arg(part: str) -> str:
    """Keep standalone option flags; redact every operand or inline value."""
    if "=" in part:
        key, _value = part.split("=", 1)
        if _SAFE_ASSIGNMENT_KEY_RE.fullmatch(key):
            return f"{key}=***"
        return "***"
    if _SAFE_FLAG_RE.fullmatch(part):
        return part
    return "***"


def redact_argv(argv: Sequence[str]) -> List[str]:
    if not argv:
        return []
    return [argv[0], *[redact_command_arg(part) for part in argv[1:]]]


def redact_command_summary(argv: Any, cwd: Any = None) -> str:
    parts = argv if isinstance(argv, (list, tuple)) else []
    rendered = " ".join(str(part) for part in redact_argv([str(p) for p in parts]))
    if cwd is not None:
        rendered += f" (cwd={cwd})"
    return rendered[:160]


def redact_tool_arguments(tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """Return a redacted copy of normalized tool arguments."""
    redacted: Dict[str, Any] = {}
    for key, value in args.items():
        if tool_name == "write_file" and key == "content":
            redacted[key] = "***"
        elif tool_name == "edit_file" and key in ("old_string", "new_string"):
            redacted[key] = "***"
        elif key == "argv" and isinstance(value, (list, tuple)):
            redacted[key] = redact_argv([str(part) for part in value])
        else:
            redacted[key] = value
    return redacted


def format_public_tool_arguments(
    tool_name: str, args: Dict[str, Any], max_chars: int = 120
) -> str:
    """Render a bounded JSON summary only after sensitive values are gone."""
    try:
        text = json.dumps(
            redact_tool_arguments(tool_name, args),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError):
        return "<arguments redacted>"
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1] + "…"


def bound_public_tool_target(
    value: Any, max_chars: int = PUBLIC_TOOL_TARGET_MAX_CHARS
) -> Optional[str]:
    """Normalize one already-selected public target into a bounded UI label."""
    if not isinstance(value, str) or max_chars < 2:
        return None
    cleaned = _CONTROL_CHARS_RE.sub(" ", value).strip()
    if not cleaned:
        return None
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[: max_chars - 1] + "…"


def public_tool_target(tool_name: str, args: Dict[str, Any]) -> Optional[str]:
    """Return a safe, structured action target for the public event DTO.

    This is intentionally derived before the bounded argument summary is
    rendered.  UI code must never parse a display string that may have been
    truncated (and must not need access to unredacted tool arguments).
    """
    if tool_name == "run_command":
        argv = args.get("argv")
        if isinstance(argv, (list, tuple)) and argv and isinstance(argv[0], str):
            # The executable identifies the action without exposing command
            # operands, inline credentials, or other user-controlled values.
            executable = argv[0].replace("\\", "/").rsplit("/", 1)[-1]
            return bound_public_tool_target(executable)

    # Search rows should describe what was searched. A generic path-first
    # traversal rendered both glob and grep as the low-information target ".".
    if tool_name in ("glob", "grep"):
        pattern = bound_public_tool_target(args.get("pattern"))
        if pattern:
            return pattern
        return bound_public_tool_target(args.get("path"))

    key_order = {
        "read_file": ("path", "file_path"),
        "write_file": ("path", "file_path"),
        "edit_file": ("path", "file_path"),
    }.get(tool_name, ("path", "file_path", "query", "pattern"))
    for key in key_order:
        target = bound_public_tool_target(args.get(key))
        if target:
            return target
    return None


def format_public_tool_outcome(
    tool_name: str,
    ok: bool,
    data: Optional[Dict[str, Any]],
    error_code: Optional[str],
    fallback_summary: str,
) -> str:
    """Remove command arguments before a tool result reaches an event sink."""
    if tool_name != "run_command":
        return fallback_summary
    if not ok:
        return f"run_command {error_code or 'error'}"
    returncode = (data or {}).get("returncode")
    return f"run_command ok: returncode={returncode}"
