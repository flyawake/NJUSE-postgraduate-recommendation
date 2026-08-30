"""Shared tool-layer types: specs, errors, outcomes.

Every tool returns a JSON-serializable result. Success is ``{"ok": true,
"data": ...}``; failure is ``{"ok": false, "error": {code, message,
retryable, recovery_hint?}}``. Error codes are stable strings owned by this
project.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, Optional

# Stable tool error codes.
INVALID_ARGUMENT = "INVALID_ARGUMENT"
INVALID_JSON_ARGS = "INVALID_JSON_ARGS"
UNKNOWN_TOOL = "UNKNOWN_TOOL"
POLICY_DENIED = "POLICY_DENIED"
PATH_NOT_ALLOWED = "PATH_NOT_ALLOWED"
PATH_NOT_FOUND = "PATH_NOT_FOUND"
PATH_IS_DIRECTORY = "PATH_IS_DIRECTORY"
DECODE_ERROR = "DECODE_ERROR"
FILE_NOT_OBSERVED = "FILE_NOT_OBSERVED"
FILE_STALE = "FILE_STALE"
FILE_ALREADY_EXISTS = "FILE_ALREADY_EXISTS"
EDIT_NO_MATCH = "EDIT_NO_MATCH"
EDIT_MULTIPLE_MATCH = "EDIT_MULTIPLE_MATCH"
CONTENT_TOO_LARGE = "CONTENT_TOO_LARGE"
RESOURCE_LIMIT = "RESOURCE_LIMIT"
TIMEOUT = "TIMEOUT"
TOOL_ABORTED = "TOOL_ABORTED"
COMMAND_NOT_FOUND = "COMMAND_NOT_FOUND"
COMMAND_FAILED = "COMMAND_FAILED"
WRITE_FAILED = "WRITE_FAILED"
NETWORK_DENIED = "NETWORK_DENIED"
NETWORK_ERROR = "NETWORK_ERROR"
HTTP_ERROR = "HTTP_ERROR"
UNSUPPORTED_CONTENT = "UNSUPPORTED_CONTENT"
INTERNAL_ERROR = "INTERNAL_ERROR"
# Loop-level codes rendered as tool results to keep history diagnosable.
REPEATED_TOOL_CALL = "REPEATED_TOOL_CALL"
ABORTED_BEFORE_DISPATCH = "ABORTED_BEFORE_DISPATCH"
PROTOCOL_ERROR = "PROTOCOL_ERROR"


class ToolEffect(str, Enum):
    READ = "READ"
    WRITE = "WRITE"
    EXECUTE = "EXECUTE"


@dataclass(frozen=True)
class ToolError:
    code: str
    message: str
    retryable: bool = False
    recovery_hint: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
        }
        if self.recovery_hint:
            result["recovery_hint"] = self.recovery_hint
        return result


class ToolExecutionError(Exception):
    """Raised inside tool validators/handlers; normalized by ToolExecutor.

    ToolExecutionError is an implementation detail of the pipeline. What the
    model sees is the JSON-serializable :class:`ToolError` structure.
    """

    def __init__(
        self,
        code: str,
        message: str,
        retryable: bool = False,
        recovery_hint: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.recovery_hint = recovery_hint

    @classmethod
    def invalid_argument(
        cls, message: str, hint: Optional[str] = None
    ) -> "ToolExecutionError":
        return cls(INVALID_ARGUMENT, message, recovery_hint=hint)

    def to_tool_error(self) -> ToolError:
        return ToolError(
            code=self.code,
            message=self.message,
            retryable=self.retryable,
            recovery_hint=self.recovery_hint,
        )


@dataclass(frozen=True)
class ToolSpec:
    """Provider schema and project-owned validator/handler for one tool.

    The JSON schema is sent to the model; the validator enforces the exact
    same contract on incoming arguments. Contract tests keep them in sync.
    """

    name: str
    description: str
    schema: Dict[str, Any]
    effect: ToolEffect
    validator: Callable[[Dict[str, Any]], Dict[str, Any]]
    handler: Callable[[Dict[str, Any]], Dict[str, Any]]


@dataclass(frozen=True)
class ToolOutcome:
    """Normalized result of one tool call.

    Three separated representations share this object: the structured
    ``data``/``error`` fields, the deterministic ``model_content`` JSON sent
    to the model, and a short redacted ``summary`` for events/CLI.
    """

    call_id: str
    tool_name: str
    ok: bool
    normalized_args: Dict[str, Any] = field(default_factory=dict)
    data: Optional[Dict[str, Any]] = None
    error: Optional[ToolError] = None

    def result_dict(self) -> Dict[str, Any]:
        if self.ok:
            return {"ok": True, "data": self.data or {}}
        error = self.error or ToolError(INTERNAL_ERROR, "unknown tool error")
        return {"ok": False, "error": error.to_dict()}

    def model_content(self) -> str:
        return json.dumps(
            self.result_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    def resource_key(self) -> str:
        data = self.data or {}
        if self.tool_name in ("glob", "grep"):
            return f"{data.get('path', '?')}::{data.get('pattern', '?')}"
        if "path" in data:
            return str(data["path"])
        if "argv" in data:
            return " ".join(str(part) for part in data["argv"])[:120]
        if "url" in data:
            return str(data["url"])[:120]
        if "query" in data:
            return str(data["query"])[:120]
        return self.tool_name

    def summary(self, max_chars: int = 160) -> str:
        if self.ok:
            detail = self._short_data(max_chars)
            return f"{self.tool_name} ok: {detail}"
        code = self.error.code if self.error else INTERNAL_ERROR
        message = (self.error.message if self.error else "").replace("\n", " ")
        return f"{self.tool_name} {code}: {message}"[:max_chars]

    def _short_data(self, max_chars: int) -> str:
        data = self.data or {}
        for key in ("path", "pattern", "url", "query", "argv", "returncode"):
            if key in data:
                value = str(data[key])
                if len(value) > max_chars - 12:
                    value = value[: max_chars - 13] + "…"
                return f"{key}={value}"
        return f"data_keys={sorted(data.keys())}"


@dataclass(frozen=True)
class PreparedCall:
    """A tool call after decode/lookup/validation/policy, before execution."""

    call_id: str
    tool_name: str
    normalized_args: Dict[str, Any]
    signature: str
    spec: Optional[ToolSpec] = None
    error: Optional[ToolError] = None
    policy_denied: bool = False

    def to_outcome(self) -> ToolOutcome:
        return ToolOutcome(
            call_id=self.call_id,
            tool_name=self.tool_name,
            ok=False,
            normalized_args=self.normalized_args,
            error=self.error,
        )
