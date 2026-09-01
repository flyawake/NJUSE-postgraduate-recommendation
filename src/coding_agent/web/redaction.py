"""Field-level redaction for public run events and verification summaries.

The kernel redacts sensitive tool fields before emitting any AgentEvent. This
module applies the same provider-neutral helpers again at the HTTP/SSE boundary
as defense in depth, including compatibility with older stored summaries.
"""

from __future__ import annotations

import json
from typing import Any, Dict

from ..public_redaction import (
    bound_public_tool_target,
    redact_command_summary,
    redact_tool_arguments,
)


def redact_verification_summary(raw: Any) -> str:
    """Redact the kernel's rendered verification command summary.

    Current kernels store a redacted display string. Older snapshots stored
    structured JSON, so retain a fail-closed parser for that legacy format.
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

    redacted = redact_tool_arguments(tool_name, data)
    return json.dumps(
        redacted, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def redact_public_payload(kind: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Apply field-level redaction to a whitelisted event payload."""
    safe = dict(payload)
    tool_name = str(safe.get("name") or "")

    if kind == "tool_started" and "arguments" in safe:
        safe["arguments"] = _redact_tool_arguments(tool_name, str(safe["arguments"]))
    if kind == "tool_started" and "target" in safe:
        # Defense in depth for injected/legacy events that did not originate
        # from the current AgentLoop formatter.
        safe["target"] = bound_public_tool_target(safe["target"])
    if kind == "tool_finished" and tool_name == "run_command":
        # Defense in depth for events from older kernel versions.
        if safe.get("ok") is True:
            safe["summary"] = "run_command ok"
        else:
            code = str(safe.get("error_code") or "error")
            safe["summary"] = f"run_command {code}"
    if kind == "model_retry" and "reason" in safe:
        safe["reason"] = str(safe["reason"])[:200]
    if kind == "plan_updated":
        raw_steps = safe.get("steps")
        bounded_steps = []
        if isinstance(raw_steps, list):
            for item in raw_steps[:7]:
                if not isinstance(item, dict):
                    continue
                step = item.get("step")
                status = item.get("status")
                if not isinstance(step, str) or status not in {
                    "pending",
                    "in_progress",
                    "completed",
                    "blocked",
                }:
                    continue
                bounded_steps.append(
                    {"step": step.replace("\x00", "")[:240], "status": status}
                )
        plan_state = safe.get("state")
        if plan_state not in {
            "active",
            "completed",
            "blocked",
            "incomplete",
            "interrupted",
            "failed",
        }:
            plan_state = "active"
        safe = {
            "revision": (
                int(safe["revision"]) if isinstance(safe.get("revision"), int) else 0
            ),
            "state": plan_state,
            "explanation": str(safe.get("explanation") or "")[:1000],
            "steps": bounded_steps,
        }
    return safe
