"""Explicit AgentLoop state machine and its orchestration invariants."""

from __future__ import annotations

import hashlib
import time
import uuid
from typing import Any, Callable, Dict, List, Optional, Sequence

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
from .planning import PlanLedger
from .prompt import SYSTEM_PROMPT
from .public_redaction import (
    format_public_tool_arguments,
    format_public_tool_outcome,
    public_tool_target,
    redact_command_summary,
)
from .streaming import (
    ModelRequestOptions,
    ReasoningDelta,
    ReasoningSummaryDelta,
    TextDelta,
    TurnStreamAccumulator,
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
    "重复调用提醒：已连续 {count} 次使用相同签名调用工具 {tool}（摘要 {signature_hash}）。"
    "请停止机械重试，改用其他参数、其他工具或重新分析工具结果。"
)

PLAN_COMPLETION_MESSAGE = (
    "The active plan still has pending or in-progress steps. Before giving the "
    "final answer, call update_plan with the full plan and mark every step "
    "completed or blocked. If work remains, continue it first."
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
        conversation_id: Optional[str] = None,
        turn_id: Optional[str] = None,
        inbox_port: Optional[Any] = None,
        max_steps: int = DEFAULT_MAX_STEPS,
        max_provider_attempts: int = DEFAULT_MAX_PROVIDER_ATTEMPTS,
        tool_failure_round_limit: int = DEFAULT_TOOL_FAILURE_ROUND_LIMIT,
        repeat_remind_at: int = REPEAT_REMIND_AT,
        repeat_abort_at: int = REPEAT_ABORT_AT,
        sleeper: Callable[[float], None] = time.sleep,
        is_cancelled: Optional[Callable[[], bool]] = None,
        system_prompt: str = SYSTEM_PROMPT,
        journal: Optional[Any] = None,
        plan_ledger: Optional[PlanLedger] = None,
        request_options: ModelRequestOptions = ModelRequestOptions(),
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
        self._conversation_id = conversation_id
        self._turn_id = turn_id
        self._inbox_port = inbox_port
        self._max_steps = max_steps
        self._max_provider_attempts = max_provider_attempts
        self._tool_failure_round_limit = tool_failure_round_limit
        self._repeat_remind_at = repeat_remind_at
        self._repeat_abort_at = repeat_abort_at
        self._sleeper = sleeper
        self._is_cancelled = is_cancelled or (lambda: False)
        self._system_prompt = system_prompt
        self._journal = journal
        self._plan = (
            plan_ledger or getattr(tool_registry, "plan_ledger", None) or PlanLedger()
        )
        self._request_options = request_options

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
        self._plan_completion_deferred = False
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

    def run(
        self,
        task: str,
        *,
        history: Optional[CanonicalHistory] = None,
        task_already_in_history: bool = False,
    ) -> RunResult:
        """Run one logical turn.

        ``history`` may carry canonical facts from previous turns of a
        conversation. When supplied, the existing system message is preserved
        and no duplicate system prompt is appended; otherwise a fresh
        system+user first turn is created exactly as before.
        """
        if self._finished:
            raise RuntimeError("AgentLoop.run() may only be called once")
        if self.phase is not LoopPhase.INITIALIZING:
            raise RuntimeError(f"run() called in phase {self.phase}")
        if history is not None:
            self._history = history
        else:
            self._history = CanonicalHistory()
        if task_already_in_history:
            if (
                not self._history.messages
                or not isinstance(self._history.messages[0], SystemMessage)
                or not isinstance(self._history.messages[-1], UserMessage)
                or self._history.messages[-1].content != task
            ):
                raise ValueError("persisted history does not end with current task")
        else:
            if not self._history.messages or not isinstance(
                self._history.messages[0], SystemMessage
            ):
                self._append_history(SystemMessage(self._system_prompt))
            self._append_history(UserMessage(task, source="user"))
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
                    details={
                        "metric": exc.metric,
                        "count": exc.count,
                        "budget": exc.budget,
                    },
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
                    details={
                        "error": type(exc).__name__,
                        "message": "internal operation failed",
                    },
                )
        return self._build_result()

    def run_turn(
        self,
        task: str,
        *,
        history: Optional[CanonicalHistory] = None,
        task_already_in_history: bool = False,
    ) -> RunResult:
        """Compatibility alias for task_004's run_turn entry point."""
        return self.run(
            task,
            history=history,
            task_already_in_history=task_already_in_history,
        )

    # ------------------------------------------------------------- history

    def _append_history(
        self, message: CanonicalMessage, *, persist: bool = True
    ) -> None:
        self._history.append(message)
        if persist and self._journal is not None:
            self._journal.append(message)

    def _poll_steer(self) -> bool:
        """Claim at most one pending steer at a safe boundary.

        Returns True if a steer user message was appended to canonical history.
        Cancellation always wins: a stop request means no steer is claimed here.
        """
        if self._inbox_port is None or self._is_cancelled():
            return False
        item = self._inbox_port.poll_steer(self._step_count)
        if item is None:
            return False
        item_id = str(item["id"])
        # The item may have been atomically demoted by terminal recovery
        # after poll but before claim.  Never append a stale steer merely
        # because the earlier read returned one.
        if not self._inbox_port.claim_steer(item_id):
            return False
        self._append_history(UserMessage(str(item["content"]), source="steer"))
        self._inbox_port.deliver_steer(item_id)
        self._emit(
            event_types.EVENT_STEER_DELIVERED,
            step=self._step_count,
            payload={"item_id": item_id, "chars": len(str(item["content"]))},
        )
        return True

    # ------------------------------------------------------------- states

    def _handle_ready(self) -> None:
        if self._step_count >= self._max_steps:
            self._finish(RunStatus.ERROR, StopReason.MAX_STEPS)
            return
        # Safe point 1: after the previous tool group/canonical commit is
        # fully settled and before building the next model request.
        self._poll_steer()
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
        successful_stream_attempt: Optional[int] = None
        stream_elapsed_ms: Optional[int] = None
        for attempt in range(1, self._max_provider_attempts + 1):
            if self._is_cancelled():
                self._finish(RunStatus.INTERRUPTED, StopReason.INTERRUPTED)
                return
            self._provider_attempt_count += 1
            stream_attempt = self._provider_attempt_count
            try:
                if hasattr(self._model_client, "stream"):
                    turn, _had_partial, stream_elapsed_ms = self._consume_stream(
                        stream_attempt
                    )
                    successful_stream_attempt = stream_attempt
                else:
                    turn = self._model_client.request(
                        self._last_view.messages, self._tool_schemas
                    )
                break
            except ModelRequestError as exc:
                if self._is_cancelled():
                    self._finish(RunStatus.INTERRUPTED, StopReason.INTERRUPTED)
                    return
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
        assistant_message = AssistantMessage(
            text=turn.text or "",
            tool_calls=tuple(turn.tool_calls),
            reasoning=turn.reasoning,
            continuations=turn.continuations,
        )
        # Validate IDs before the assistant tool group can cross the durable
        # journal boundary. Invalid provider output remains diagnosable in
        # this run's memory, but can never be replayed as canonical history.
        persist_assistant = not self._invalid_tool_call_indices(turn.tool_calls)
        self._append_history(assistant_message, persist=persist_assistant)
        self._last_assistant = turn
        self._emit(
            event_types.EVENT_ASSISTANT_RECEIVED,
            step=self._step_count,
            payload={
                "text_chars": len(turn.text),
                "tool_call_count": len(turn.tool_calls),
                **(
                    {"attempt": successful_stream_attempt}
                    if successful_stream_attempt is not None
                    else {}
                ),
                **(
                    {"elapsed_ms": stream_elapsed_ms}
                    if stream_elapsed_ms is not None
                    else {}
                ),
            },
        )
        self._transit(LoopPhase.HANDLING_RESPONSE)

    def _consume_stream(self, attempt: int):
        assert self._last_view is not None
        accumulator = TurnStreamAccumulator()
        started_at = time.monotonic()
        self._emit(
            event_types.EVENT_MODEL_STREAM_STARTED,
            step=self._step_count,
            payload={"attempt": attempt},
        )
        try:
            events = self._model_client.stream(
                self._last_view.messages,
                self._tool_schemas,
                options=self._request_options,
                cancel=self._is_cancelled,
            )
            for event in events:
                # Validate first. An illegal provider fragment must never be
                # published or checkpointed before the protocol boundary
                # rejects it.
                accumulator.absorb(event)
                if isinstance(event, TextDelta):
                    self._emit(
                        event_types.EVENT_ASSISTANT_TEXT_DELTA,
                        step=self._step_count,
                        payload={"delta": event.delta, "attempt": attempt},
                    )
                elif isinstance(event, ReasoningDelta):
                    if self._request_options.reasoning_mode != "off":
                        self._emit(
                            event_types.EVENT_REASONING_DELTA,
                            step=self._step_count,
                            payload={
                                "delta": event.delta,
                                "attempt": attempt,
                                "visibility": event.visibility,
                            },
                        )
                elif isinstance(event, ReasoningSummaryDelta):
                    if self._request_options.reasoning_mode != "off":
                        self._emit(
                            event_types.EVENT_REASONING_SUMMARY_DELTA,
                            step=self._step_count,
                            payload={
                                "delta": event.delta,
                                "summary_index": event.summary_index,
                                "attempt": attempt,
                            },
                        )
            turn = accumulator.to_turn()
            elapsed_ms = max(0, round((time.monotonic() - started_at) * 1000))
            return turn, accumulator.has_output, elapsed_ms
        except Exception:
            if accumulator.has_output:
                self._emit(
                    event_types.EVENT_STREAM_ATTEMPT_ABANDONED,
                    step=self._step_count,
                    payload={"attempt": attempt, "reason": "stream_failed"},
                )
            raise

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
        bad = self._invalid_tool_call_indices(calls)
        if bad:
            # Invalid provider output was kept out of the durable journal in
            # _handle_requesting. The abandon call is defense in depth for a
            # custom journal implementation; synthetic results remain local
            # to this run so call/result pairing is still auditable.
            if self._journal is not None and hasattr(
                self._journal, "abandon_current_tool_group"
            ):
                self._journal.abandon_current_tool_group()
            for index, call in enumerate(calls):
                self._tool_call_count += 1
                if index in bad:
                    self._append_error_result(
                        call,
                        PROTOCOL_ERROR,
                        "tool call id is empty or duplicated inside this assistant turn",
                        persist=False,
                    )
                else:
                    self._append_error_result(
                        call,
                        ABORTED_BEFORE_DISPATCH,
                        "not dispatched because the turn contained an invalid tool call id",
                        persist=False,
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
                    details={
                        "tool": prepared.tool_name,
                        "signature_sha256": hashlib.sha256(
                            prepared.signature.encode("utf-8")
                        ).hexdigest(),
                    },
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
                    **(
                        {"plan_step": self._plan.active_step}
                        if call.name != "update_plan" and self._plan.active_step
                        else {}
                    ),
                },
            )
            outcome = self._tool_executor.execute(prepared)
            self._append_outcome(outcome)
            self._record_outcome_effects(outcome)
            any_success = any_success or outcome.ok
            if outcome.ok and outcome.tool_name == "update_plan" and outcome.data:
                self._emit(
                    event_types.EVENT_PLAN_UPDATED,
                    step=self._step_count,
                    payload=dict(outcome.data),
                )
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
                    **(
                        {"plan_step": self._plan.active_step}
                        if call.name != "update_plan" and self._plan.active_step
                        else {}
                    ),
                },
            )

        if interrupted:
            self._finish(RunStatus.INTERRUPTED, StopReason.INTERRUPTED)
            return

        if reminder_due:
            signature = self._last_signature or ""
            self._append_history(
                UserMessage(
                    REPEAT_REMINDER_MESSAGE.format(
                        count=self._repeat_remind_at,
                        tool=calls[-1].name if calls else "",
                        signature_hash=hashlib.sha256(
                            signature.encode("utf-8")
                        ).hexdigest()[:12],
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
        # Safe point 2: the assistant text is already in canonical history and
        # no tool is executing; if the user queued a steer for this turn, put
        # it into model context before deciding whether the turn should end.
        if self._poll_steer():
            self._transit(LoopPhase.READY)
            return
        if self._plan.needs_completion_update and not self._plan_completion_deferred:
            self._plan_completion_deferred = True
            self._append_history(
                UserMessage(PLAN_COMPLETION_MESSAGE, source="plan_policy")
            )
            snapshot = self._plan.snapshot
            self._emit(
                event_types.EVENT_PLAN_COMPLETION_DEFERRED,
                step=self._step_count,
                payload={"revision": snapshot.revision if snapshot else 0},
            )
            self._transit(LoopPhase.READY)
            return
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
        self._append_history(UserMessage(decision.message, source="completion_policy"))
        self._emit(
            event_types.EVENT_COMPLETION_DEFERRED,
            step=self._step_count,
            payload={"verification_status": self._verification.value},
        )
        self._transit(LoopPhase.READY)

    # ------------------------------------------------------------ helpers

    @staticmethod
    def _invalid_tool_call_indices(calls: Sequence[ToolCall]) -> set[int]:
        seen: set[str] = set()
        bad: set[int] = set()
        for index, call in enumerate(calls):
            if not call.id or call.id in seen:
                bad.add(index)
            if call.id:
                seen.add(call.id)
        return bad

    def _append_outcome(self, outcome: ToolOutcome, *, persist: bool = True) -> None:
        data = outcome.data or {}
        file_path = None
        is_read_success = False
        if outcome.ok and outcome.tool_name == "read_file":
            path = data.get("path")
            if isinstance(path, str):
                file_path = path
                is_read_success = True
        self._append_history(
            ToolMessage(
                tool_call_id=outcome.call_id,
                content=outcome.model_content(),
                tool_name=outcome.tool_name,
                ok=outcome.ok,
                resource_key=outcome.resource_key(),
                is_read_success=is_read_success,
                file_path=file_path,
            ),
            persist=persist,
        )

    def _append_error_result(
        self,
        call: ToolCall,
        code: str,
        message: str,
        *,
        persist: bool = True,
    ) -> None:
        outcome = ToolOutcome(
            call_id=call.id,
            tool_name=call.name,
            ok=False,
            normalized_args={},
            error=ToolError(code, message),
        )
        self._append_outcome(outcome, persist=persist)

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
        try:
            terminal_plan = self._plan.finish(status)
        except Exception:
            # Plan finalization is secondary to the unique run terminal event.
            # The service performs an idempotent persistence fallback and
            # startup recovery marks any remaining active plans interrupted.
            terminal_plan = self._plan.snapshot
            self._error_details["plan_finalize_error"] = True
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
                **(
                    {
                        "plan_state": terminal_plan.state,
                        "plan_revision": terminal_plan.revision,
                    }
                    if terminal_plan is not None
                    else {}
                ),
            },
        )
        self._finished = True

    def _build_result(self) -> RunResult:
        plan = self._plan.snapshot
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
            plan_state=plan.state if plan is not None else None,
            plan_revision=plan.revision if plan is not None else None,
        )
