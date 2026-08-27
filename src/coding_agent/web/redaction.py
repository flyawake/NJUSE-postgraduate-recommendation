"""Field-level redaction for public run events and verification summaries.

The kernel already produces short summaries, but "short" is not "secret-free":
write/edit contents and command arguments can still contain tokens, passwords
or keys. This module is the single gate between AgentLoop payloads and the
public DTOs: every payload field that crosses the HTTP/SSE boundary passes
through here, so a sentinel secret can never reach snapshot, SSE or the DOM.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List

_SENSITIVE_PART_RE = re.compile(
    r"(?i)(api[_-]?key|secret|token|password|passwd|authorization|bearer|credential|private[_-]?key)"
)

_SAFE_FLAG_RE = re.compile(r"^-{1,2}[A-Za-z0-9][A-Za-z0-9._-]*$")


def redact_command_arg(part: str) -> str:
    """Redact one argv element conservatively.

    - ``argv[0]`` (the executable) is kept by the caller.
    - Safe flags like ``-m``/``--verify`` are kept.
    - ``key=value`` keeps the key and redacts the value when the key is
      sensitive; a non-sensitive ``key=value`` keeps both.
    - Everything else is replaced with ``***``.
    """
    if "=" in part:
        key, value = part.split("=", 1)
        if _SENSITIVE_PART_RE.search(key):
            return f"{key}=***"
        return part
    if _SAFE_FLAG_RE.match(part):
        return part
    return "***"


def redact_argv(argv: List[str]) -> List[str]:
    if not argv:
        return []
    return [argv[0], *[redact_command_arg(part) for part in argv[1:]]]


def redact_command_summary(argv: Any, cwd: Any = None) -> str:
    parts = argv if isinstance(argv, list) else []
    rendered = " ".join(str(part) for part in redact_argv([str(p) for p in parts]))
    if cwd is not None:
        rendered += f" (cwd={cwd})"
    return rendered[:160]


def redact_verification_summary(raw: Any) -> str:
    """Redact the kernel's rendered verification command summary.

    The kernel stores ``json.dumps({"argv": [...], "cwd": ...})``; parse it
    back to structured form and apply argv redaction. Anything unparseable is
    never shown at all.
    """
    text = str(raw or "")
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return "verification command redacted"
    if not isinstance(data, dict) or not isinstance(data.get("argv"), list):
        return "verification command redacted"
    return redact_command_summary(
        [str(part) for part in data["argv"]], data.get("cwd", ".")
    )


def _redact_tool_arguments(tool_name: str, summary: str) -> str:
    """Redact a rendered arguments summary using the tool schema knowledge."""
    try:
        data = json.loads(summary)
    except (json.JSONDecodeError, TypeError):
        return "<arguments redacted>"
    if not isinstance(data, dict):
        return "<arguments redacted>"

    redacted: Dict[str, Any] = {}
    for key, value in data.items():
        if tool_name == "write_file" and key == "content":
            redacted[key] = "***"
        elif tool_name == "edit_file" and key in ("old_string", "new_string"):
            redacted[key] = "***"
        elif key == "argv" and isinstance(value, list):
            redacted[key] = redact_argv([str(part) for part in value])
        else:
            redacted[key] = value
    return json.dumps(
        redacted, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def redact_public_payload(kind: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Apply field-level redaction to a whitelisted event payload."""
    safe = dict(payload)
    tool_name = str(safe.get("name") or "")

    if kind == "tool_started" and "arguments" in safe:
        safe["arguments"] = _redact_tool_arguments(tool_name, str(safe["arguments"]))
    if kind == "tool_finished" and tool_name == "run_command":
        # The kernel summary can contain the raw argv; rebuild a redacted one.
        if safe.get("ok") is True:
            safe["summary"] = "run_command ok"
        else:
            code = str(safe.get("error_code") or "error")
            safe["summary"] = f"run_command {code}"
    if kind == "model_retry" and "reason" in safe:
        safe["reason"] = str(safe["reason"])[:200]
    return safe
