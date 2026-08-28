"""ToolExecutor pipeline.

Pipeline: decode JSON object -> registry lookup -> ToolSpec validation ->
ToolPolicy -> handler -> outcome normalization -> deterministic JSON model
rendering. AgentLoop owns call IDs, repeat/cancel guards and event emission;
it calls :meth:`prepare` first and :meth:`execute` only after its own loop
guards pass.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, Optional

from .base import (
    INTERNAL_ERROR,
    INVALID_JSON_ARGS,
    POLICY_DENIED,
    UNKNOWN_TOOL,
    PreparedCall,
    ToolError,
    ToolExecutionError,
    ToolOutcome,
    ToolSpec,
)
from .policy import PolicyDecision, ToolPolicy
from .registry import ToolRegistry


def format_args_summary(args: Dict[str, Any], max_chars: int = 120) -> str:
    try:
        text = json.dumps(
            args, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
    except (TypeError, ValueError):
        text = repr(args)
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1] + "…"


class ToolExecutor:
    def __init__(
        self,
        registry: ToolRegistry,
        policy: ToolPolicy,
        is_cancelled: Optional[Callable[[], bool]] = None,
        observer: Optional[Any] = None,
    ) -> None:
        self._registry = registry
        self._policy = policy
        self._is_cancelled = is_cancelled or (lambda: False)
        self._observer = observer

    def prepare(self, call) -> PreparedCall:
        name = call.name or ""
        call_id = call.id or ""
        try:
            raw = call.arguments_raw or "{}"
            decoded = json.loads(raw)
        except (json.JSONDecodeError, TypeError, ValueError):
            error = ToolError(
                INVALID_JSON_ARGS,
                "tool arguments must be valid JSON",
                recovery_hint="reply with corrected JSON object arguments",
            )
            return PreparedCall(
                call_id, name, {}, self._signature(name, raw), error=error
            )
        if not isinstance(decoded, dict):
            error = ToolError(
                INVALID_JSON_ARGS,
                "tool arguments must be a JSON object",
                recovery_hint="wrap arguments in a JSON object",
            )
            return PreparedCall(
                call_id, name, {}, self._signature(name, raw), error=error
            )

        spec = self._registry.get(name)
        if spec is None:
            error = ToolError(
                UNKNOWN_TOOL,
                f"unknown tool: {name!r}",
                recovery_hint=f"available tools: {', '.join(self._registry.names())}",
            )
            return PreparedCall(
                call_id, name, dict(decoded), self._signature(name, raw), error=error
            )

        try:
            normalized = spec.validator(dict(decoded))
        except ToolExecutionError as exc:
            return PreparedCall(
                call_id,
                name,
                dict(decoded),
                self._signature(name, raw),
                error=exc.to_tool_error(),
            )
        if not isinstance(normalized, dict):
            error = ToolError(INTERNAL_ERROR, "tool validator did not return an object")
            return PreparedCall(
                call_id, name, dict(decoded), self._signature(name, raw), error=error
            )

        signature = self._signature(name, normalized)
        result = self._policy.decide(spec.effect, name, normalized, call_id)
        if result.decision is PolicyDecision.DENY:
            error = ToolError(
                POLICY_DENIED,
                result.reason or "policy denied this tool call",
                recovery_hint="ask the user or change the requested operation",
            )
            return PreparedCall(
                call_id,
                name,
                normalized,
                signature,
                spec=spec,
                error=error,
                policy_denied=True,
            )
        return PreparedCall(call_id, name, normalized, signature, spec=spec)

    def execute(self, prepared: PreparedCall) -> ToolOutcome:
        if prepared.error is not None:
            return prepared.to_outcome()
        spec: ToolSpec = prepared.spec  # type: ignore[assignment]
        if self._observer is not None:
            self._observer.before_execute(prepared)
        try:
            data = spec.handler(prepared.normalized_args)
        except ToolExecutionError as exc:
            outcome = ToolOutcome(
                call_id=prepared.call_id,
                tool_name=prepared.tool_name,
                ok=False,
                normalized_args=prepared.normalized_args,
                error=exc.to_tool_error(),
            )
            if self._observer is not None:
                self._observer.after_execute(prepared, outcome)
            return outcome
        except Exception as exc:  # defensive: never leak a crash into the loop
            error = ToolError(
                INTERNAL_ERROR,
                f"{type(exc).__name__}: {exc}",
                recovery_hint="inspect the tool result and adjust arguments",
            )
            outcome = ToolOutcome(
                call_id=prepared.call_id,
                tool_name=prepared.tool_name,
                ok=False,
                normalized_args=prepared.normalized_args,
                error=error,
            )
            if self._observer is not None:
                self._observer.after_execute(prepared, outcome)
            return outcome
        if not isinstance(data, dict):
            error = ToolError(
                INTERNAL_ERROR,
                "tool handler returned a non-object result",
                recovery_hint="report this as a tool implementation bug",
            )
            outcome = ToolOutcome(
                call_id=prepared.call_id,
                tool_name=prepared.tool_name,
                ok=False,
                normalized_args=prepared.normalized_args,
                error=error,
            )
            if self._observer is not None:
                self._observer.after_execute(prepared, outcome)
            return outcome
        outcome = ToolOutcome(
            call_id=prepared.call_id,
            tool_name=prepared.tool_name,
            ok=True,
            normalized_args=prepared.normalized_args,
            data=data,
        )
        if self._observer is not None:
            self._observer.after_execute(prepared, outcome)
        return outcome

    def run(self, call) -> ToolOutcome:
        """Convenience wrapper: full pipeline in one call (used by tests)."""
        return self.execute(self.prepare(call))

    @staticmethod
    def _signature(name: str, args: Any) -> str:
        if isinstance(args, dict):
            return (
                name
                + ":"
                + json.dumps(
                    args, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                )
            )
        return name + ":raw:" + str(args)
