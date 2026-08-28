"""Explicit AgentLoop state machine and its orchestration invariants."""

from __future__ import annotations

import time
import uuid
from typing import Any, Callable, Dict, List, Optional

from . import events as event_types
from .completion import CompletionPolicy
from .context import CanonicalHistory, ContextManager, RequestView
from .errors import ContextOverflowError, ModelRequestError
from .model_client import ModelClient
from .models import (
    AgentEvent,
    AssistantMessage,
    AssistantTurn,
    CanonicalMessage,
    LoopPhase,
    RunResult,
    RunStatus,
    StopReason,
    SystemMessage,
    ToolCall,
    ToolMessage,
    UserMessage,
    VerificationStatus,
)
from .prompt import SYSTEM_PROMPT
from .public_redaction import (
    format_public_tool_arguments,
    format_public_tool_outcome,
    public_tool_target,
    redact_command_summary,
)
from .tools.base import (
    ABORTED_BEFORE_DISPATCH,
    PROTOCOL_ERROR,
    REPEATED_TOOL_CALL,
    ToolError,
    ToolOutcome,
)
from .tools.executor import ToolExecutor
from .tools.registry import ToolRegistry

DEFAULT_MAX_STEPS = 20
DEFAULT_MAX_PROVIDER_ATTEMPTS = 3
DEFAULT_TOOL_FAILURE_ROUND_LIMIT = 3
REPEAT_REMIND_AT = 3
REPEAT_ABORT_AT = 5

ALLOWED_TRANSITIONS: Dict[LoopPhase, frozenset] = {
    LoopPhase.INITIALIZING: frozenset({LoopPhase.READY, LoopPhase.TERMINAL}),
    LoopPhase.READY: frozenset(
        {LoopPhase.REQUESTING_MODEL, LoopPhase.TERMINAL, LoopPhase.INTERRUPTED}
    ),
    LoopPhase.REQUESTING_MODEL: frozenset(
        {LoopPhase.HANDLING_RESPONSE, LoopPhase.TERMINAL, LoopPhase.INTERRUPTED}
    ),
    LoopPhase.HANDLING_RESPONSE: frozenset(
        {
            LoopPhase.EXECUTING_TOOLS,
            LoopPhase.CHECKING_COMPLETION,
            LoopPhase.TERMINAL,
            LoopPhase.INTERRUPTED,
        }
    ),
    LoopPhase.EXECUTING_TOOLS: frozenset(
        {LoopPhase.READY, LoopPhase.TERMINAL, LoopPhase.INTERRUPTED}
    ),
    LoopPhase.CHECKING_COMPLETION: frozenset(
        {LoopPhase.READY, LoopPhase.TERMINAL, LoopPhase.INTERRUPTED}
    ),
    LoopPhase.INTERRUPTED: frozenset(),
    LoopPhase.TERMINAL: frozenset(),
}

REPEAT_REMINDER_MESSAGE = (
    "重复调用提醒：已连续 {count} 次使用相同签名调用工具 {tool}（{signature}）。"
    "请停止机械重试，改用其他参数、其他工具或重新分析工具结果。"
)


class AgentLoop:
    """Owns the explicit FSM, counters, pairing, repeat/cancel guards.

    All dependencies are injected; the loop never reads environment
    variables, never prints, and never touches SDK objects directly.
    """

    def __init__(
        self,
        *,
        model_client: ModelClient,
        tool_registry: ToolRegistry,
        tool_executor: ToolExecutor,
        context_manager: ContextManager,
        completion_policy: CompletionPolicy,
        event_sink,
        run_id: Optional[str] = None,
        max_steps: int = DEFAULT_MAX_STEPS,
        max_provider_attempts: int = DEFAULT_MAX_PROVIDER_ATTEMPTS,
        tool_failure_round_limit: int = DEFAULT_TOOL_FAILURE_ROUND_LIMIT,
        repeat_remind_at: int = REPEAT_REMIND_AT,
        repeat_abort_at: int = REPEAT_ABORT_AT,
        sleeper: Callable[[float], None] = time.sleep,
        is_cancelled: Optional[Callable[[], bool]] = None,
        system_prompt: str = SYSTEM_PROMPT,
    ) -> None:
        if max_steps < 1:
            raise ValueError("max_steps must be positive")
        self._model_client = model_client
        self._tool_schemas = tool_registry.provider_tools()
        self._tool_executor = tool_executor
        self._context = context_manager
        self._completion = completion_policy
        self._event_sink = event_sink
        self._run_id = run_id or uuid.uuid4().hex
        self._max_steps = max_steps
        self._max_provider_attempts = max_provider_attempts
        self._tool_failure_round_limit = tool_failure_round_limit
        self._repeat_remind_at = repeat_remind_at
        self._repeat_abort_at = repeat_abort_at
        self._sleeper = sleeper
        self._is_cancelled = is_cancelled or (lambda: False)
        self._system_prompt = system_prompt

        self.phase = LoopPhase.INITIALIZING
        self._history = CanonicalHistory()
        self._step_count = 0
        self._provider_attempt_count = 0
        self._tool_call_count = 0
        self._verification = VerificationStatus.NOT_APPLICABLE
        self._mutated_paths: Dict[str, str] = {}
        self._workspace_revision = 0
        self._last_verification: Optional[Dict[str, Any]] = None
        self._completion_deferred = False
        self._consecutive_failed_rounds = 0
        self._last_signature: Optional[str] = None
        self._repeat_count = 0
        self._event_sequence = 0
        self._finished = False
        self._last_view: Optional[RequestView] = None
        self._last_assistant: Optional[AssistantTurn] = None
        self._pending_calls: List[ToolCall] = []

        self._status = RunStatus.ERROR
        self._stop_reason = StopReason.INTERNAL_ERROR
        self._final_text: Optional[str] = None
        self._error_details: Dict[str, Any] = {}

    # ------------------------------------------------------------------ API

    @property
    def history(self) -> tuple[CanonicalMessage, ...]:
        """Immutable view of the append-only canonical history."""
        return self._history.messages

    def run(self, task: str) -> RunResult:
        if self._finished:
            raise RuntimeError("AgentLoop.run() may only be called once")
        if self.phase is not LoopPhase.INITIALIZING:
            raise RuntimeError(f"run() called in phase {self.phase}")
        self._history.append(SystemMessage(self._system_prompt))
        self._history.append(UserMessage(task, source="user"))
        self._transit(LoopPhase.READY)
        self._emit(
            event_types.EVENT_RUN_STARTED,
            step=0,
            payload={"task_chars": len(task)},
        )

        handlers = {
            LoopPhase.READY: self._handle_ready,
            LoopPhase.REQUESTING_MODEL: self._handle_requesting,
            LoopPhase.HANDLING_RESPONSE: self._handle_response,
            LoopPhase.EXECUTING_TOOLS: self._handle_executing,
            LoopPhase.CHECKING_COMPLETION: self._handle_completion,
        }
        while not self._is_terminal(self.phase):
            if self._is_cancelled():
                self._finish(RunStatus.INTERRUPTED, StopReason.INTERRUPTED)
                break
            try:
                handlers[self.phase]()
            except ContextOverflowError as exc:
                self._finish(
                    RunStatus.ERROR,
                    StopReason.CONTEXT_OVERFLOW,
                    details={"char_count": exc.char_count, "budget": exc.budget},
                )
            except ModelRequestError as exc:
                self._finish(
                    RunStatus.ERROR,
                    StopReason.MODEL_ERROR,
                    details={"reason": exc.reason},
                )
            except Exception as exc:
                self._finish(
                    RunStatus.ERROR,
                    StopReason.INTERNAL_ERROR,
                    details={"error": type(exc).__name__, "message": str(exc)[:200]},
                )
        return self._build_result()

    # ------------------------------------------------------------- states

    def _handle_ready(self) -> None:
        if self._step_count >= self._max_steps:
            self._finish(RunStatus.ERROR, StopReason.MAX_STEPS)
            return
        self._last_view = self._context.build_request(self._history)
        self._step_count += 1
        self._emit(
            event_types.EVENT_STEP_STARTED,
            step=self._step_count,
            payload={
                "char_count": self._last_view.char_count,
                "budget": self._context.char_budget,
            },
        )
        self._transit(LoopPhase.REQUESTING_MODEL)

    def _handle_requesting(self) -> None:
        assert self._last_view is not None
        turn: Optional[AssistantTurn] = None
        for attempt in range(1, self._max_provider_attempts + 1):
            if self._is_cancelled():
                self._finish(RunStatus.INTERRUPTED, StopReason.INTERRUPTED)
                return
            self._provider_attempt_count += 1
            try:
                turn = self._model_client.request(
                    self._last_view.messages, self._tool_schemas
                )
                break
            except ModelRequestError as exc:
                if not exc.retryable or attempt >= self._max_provider_attempts:
                    self._finish(
                        RunStatus.ERROR,
                        StopReason.MODEL_ERROR,
                        details={"attempts": attempt, "reason": exc.reason},
                    )
                    return
                self._emit(
                    event_types.EVENT_MODEL_RETRY,
                    step=self._step_count,
                    payload={
                        "attempt": attempt,
                        "next_attempt": attempt + 1,
                        "reason": exc.reason,
                    },
                )
                self._sleeper(2.0 ** (attempt - 1))
        if self._is_cancelled():
            self._finish(RunStatus.INTERRUPTED, StopReason.INTERRUPTED)
            return
        if turn is None:
            self._finish(RunStatus.ERROR, StopReason.MODEL_ERROR)
            return
        self._history.append(
            AssistantMessage(text=turn.text or "", tool_calls=tuple(turn.tool_calls))
        )
        self._last_assistant = turn
        self._emit(
            event_types.EVENT_ASSISTANT_RECEIVED,
            step=self._step_count,
            payload={
                "text_chars": len(turn.text),
                "tool_call_count": len(turn.tool_calls),
            },
        )
        self._transit(LoopPhase.HANDLING_RESPONSE)

    def _handle_response(self) -> None:
        turn = self._last_assistant
        assert turn is not None
        if not turn.tool_calls:
            if not turn.text or not turn.text.strip():
                self._finish(
                    RunStatus.ERROR,
                    StopReason.PROTOCOL_ERROR,
                    details={"reason": "empty assistant message without tool calls"},
                )
                return
            self._transit(LoopPhase.CHECKING_COMPLETION)
            return
        self._pending_calls = list(turn.tool_calls)
        self._transit(LoopPhase.EXECUTING_TOOLS)

    def _handle_executing(self) -> None:
        calls = list(self._pending_calls)
        if not calls:
            self._transit(LoopPhase.READY)
            return

        # Loop guard: call ids must be non-empty and unique within the turn.
        seen: set[str] = set()
        bad: set[int] = set()
        for index, call in enumerate(calls):
            if not call.id or call.id in seen:
                bad.add(index)
            if call.id:
                seen.add(call.id)
        if bad:
            for index, call in enumerate(calls):
                self._tool_call_count += 1
                if index in bad:
                    self._append_error_result(
                        call,
                        PROTOCOL_ERROR,
                        "tool call id is empty or duplicated inside this assistant turn",
                    )
                else:
                    self._append_error_result(
                        call,
                        ABORTED_BEFORE_DISPATCH,
                        "not dispatched because the turn contained an invalid tool call id",
                    )
            self._finish(
                RunStatus.ERROR,
                StopReason.PROTOCOL_ERROR,
                details={"reason": "invalid or duplicate tool_call_id"},
            )
            return

        interrupted = False
        reminder_due = False
        any_success = False
        for index, call in enumerate(calls):
            if self._is_cancelled():
                interrupted = True
                self._append_remaining_aborted(calls[index:])
                break

            prepared = self._tool_executor.prepare(call)
            # Second cancel guard: policy/prepare ran without side effects,
            # but cancellation may have arrived during them. The handler must
            # not start once the group is being aborted.
            if self._is_cancelled():
                interrupted = True
                self._append_remaining_aborted(calls[index:])
                break

            self._update_repeat(prepared.signature)
            if self._repeat_count >= self._repeat_abort_at:
                self._tool_call_count += 1
                self._append_error_result(
                    call,
                    REPEATED_TOOL_CALL,
                    f"same call signature repeated {self._repeat_count} times consecutively",
                )
                self._append_remaining_aborted(calls[index + 1 :])
                self._finish(
                    RunStatus.ERROR,
                    StopReason.REPEATED_TOOL_CALL,
                    details={"signature": prepared.signature},
                )
                return
            if self._repeat_count == self._repeat_remind_at:
                reminder_due = True

            self._tool_call_count += 1
            self._emit(
                event_types.EVENT_TOOL_STARTED,
                step=self._step_count,
                payload={
                    "call_id": call.id,
                    "name": call.name,
                    "arguments": format_public_tool_arguments(
                        call.name, prepared.normalized_args
                    ),
                    "target": public_tool_target(call.name, prepared.normalized_args),
                },
            )
            outcome = self._tool_executor.execute(prepared)
            self._append_outcome(outcome)
            self._record_outcome_effects(outcome)
            any_success = any_success or outcome.ok
            self._emit(
                event_types.EVENT_TOOL_FINISHED,
                step=self._step_count,
                payload={
                    "call_id": call.id,
                    "name": call.name,
                    "ok": outcome.ok,
                    "error_code": None
                    if outcome.ok
                    else outcome.error.code
                    if outcome.error
                    else None,
                    "summary": format_public_tool_outcome(
                        outcome.tool_name,
                        outcome.ok,
                        outcome.data,
                        outcome.error.code if outcome.error else None,
                        outcome.summary(),
                    ),
                },
            )

        if interrupted:
            self._finish(RunStatus.INTERRUPTED, StopReason.INTERRUPTED)
            return

        if reminder_due:
            signature = self._last_signature or ""
            self._history.append(
                UserMessage(
                    REPEAT_REMINDER_MESSAGE.format(
                        count=self._repeat_remind_at,
                        tool=calls[-1].name if calls else "",
                        signature=signature,
                    ),
                    source="loop_guard",
                )
            )

        if any_success:
            self._consecutive_failed_rounds = 0
        else:
            self._consecutive_failed_rounds += 1
        if self._consecutive_failed_rounds >= self._tool_failure_round_limit:
            self._finish(
                RunStatus.ERROR,
                StopReason.TOOL_FAILURE_LIMIT,
                details={
                    "consecutive_failed_rounds": self._consecutive_failed_rounds,
                    "limit": self._tool_failure_round_limit,
                },
            )
            return
        self._transit(LoopPhase.READY)

    def _handle_completion(self) -> None:
        turn = self._last_assistant
        assert turn is not None
        if not self._mutated_paths:
            self._verification = VerificationStatus.NOT_APPLICABLE
        decision = self._completion.decide(
            has_changes=bool(self._mutated_paths),
            verification=self._verification,
            step_count=self._step_count,
            max_steps=self._max_steps,
            deferred=self._completion_deferred,
        )
        if decision.complete:
            self._finish(
                RunStatus.SUCCESS, StopReason.FINAL_ANSWER, final_text=turn.text
            )
            return
        self._completion_deferred = True
        self._history.append(UserMessage(decision.message, source="completion_policy"))
        self._emit(
            event_types.EVENT_COMPLETION_DEFERRED,
            step=self._step_count,
            payload={"verification_status": self._verification.value},
        )
        self._transit(LoopPhase.READY)

    # ------------------------------------------------------------ helpers

    def _append_outcome(self, outcome: ToolOutcome) -> None:
        data = outcome.data or {}
        file_path = None
        is_read_success = False
        if outcome.ok and outcome.tool_name == "read_file":
            path = data.get("path")
            if isinstance(path, str):
                file_path = path
                is_read_success = True
        self._history.append(
            ToolMessage(
                tool_call_id=outcome.call_id,
                content=outcome.model_content(),
                tool_name=outcome.tool_name,
                ok=outcome.ok,
                resource_key=outcome.resource_key(),
                is_read_success=is_read_success,
                file_path=file_path,
            )
        )

    def _append_error_result(self, call: ToolCall, code: str, message: str) -> None:
        outcome = ToolOutcome(
            call_id=call.id,
            tool_name=call.name,
            ok=False,
            normalized_args={},
            error=ToolError(code, message),
        )
        self._append_outcome(outcome)

    def _append_remaining_aborted(self, calls: List[ToolCall]) -> None:
        for call in calls:
            self._tool_call_count += 1
            self._append_error_result(
                call,
                ABORTED_BEFORE_DISPATCH,
                "not dispatched because the run was interrupted or a guard aborted the group",
            )

    def _record_outcome_effects(self, outcome: ToolOutcome) -> None:
        if not outcome.ok or not outcome.data:
            return
        data = outcome.data
        if outcome.tool_name in ("write_file", "edit_file"):
            path = str(data.get("path", ""))
            fingerprint = str(data.get("fingerprint", ""))
            if path:
                self._mutated_paths[path] = fingerprint
            self._workspace_revision += 1
            self._verification = VerificationStatus.NOT_RUN
            self._last_verification = None
            return
        if outcome.tool_name == "run_command" and data.get("purpose") == "verify":
            exit_code = data.get("returncode", 1)
            self._last_verification = {
                "command": redact_command_summary(
                    data.get("argv", []), data.get("cwd", ".")
                ),
                "command_redacted": True,
                "exit_code": exit_code,
                "tool_call_id": outcome.call_id,
            }
            self._verification = (
                VerificationStatus.VERIFIED
                if exit_code == 0
                else VerificationStatus.FAILED
            )

    def _update_repeat(self, signature: str) -> None:
        if signature == self._last_signature:
            self._repeat_count += 1
        else:
            self._last_signature = signature
            self._repeat_count = 1

    def _transit(self, target: LoopPhase) -> None:
        if target not in ALLOWED_TRANSITIONS[self.phase]:
            raise RuntimeError(f"illegal phase transition: {self.phase} -> {target}")
        self.phase = target

    @staticmethod
    def _is_terminal(phase: LoopPhase) -> bool:
        return phase in (LoopPhase.TERMINAL, LoopPhase.INTERRUPTED)

    def _emit(self, event_type: str, step: int, payload: Dict[str, Any]) -> None:
        if self._finished:
            raise RuntimeError(f"event emitted after run finished: {event_type}")
        self._event_sequence += 1
        self._event_sink.emit(
            AgentEvent(
                sequence=self._event_sequence,
                run_id=self._run_id,
                type=event_type,
                step=step,
                phase=self.phase,
                payload=payload,
            )
        )

    def _finish(
        self,
        status: RunStatus,
        reason: StopReason,
        final_text: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        if self._finished:
            return
        self._status = status
        self._stop_reason = reason
        self._final_text = final_text
        if details:
            self._error_details.update(details)
        if status is RunStatus.INTERRUPTED:
            self._transit(LoopPhase.INTERRUPTED)
        else:
            self._transit(LoopPhase.TERMINAL)
        self._emit(
            event_types.EVENT_RUN_FINISHED,
            step=self._step_count,
            payload={
                "status": self._status.value,
                "stop_reason": self._stop_reason.value,
                "verification_status": self._verification.value,
                "mutated_paths": list(self._mutated_paths.keys()),
                "step_count": self._step_count,
                "provider_attempt_count": self._provider_attempt_count,
                "tool_call_count": self._tool_call_count,
            },
        )
        self._finished = True

    def _build_result(self) -> RunResult:
        return RunResult(
            run_id=self._run_id,
            status=self._status,
            stop_reason=self._stop_reason,
            final_text=self._final_text,
            step_count=self._step_count,
            provider_attempt_count=self._provider_attempt_count,
            tool_call_count=self._tool_call_count,
            verification_status=self._verification,
            mutated_paths=tuple(self._mutated_paths.keys()),
            last_verification=self._last_verification,
            final_phase=self.phase,
            details=self._error_details,
            context_char_count=(
                self._last_view.char_count if self._last_view is not None else None
            ),
        )
