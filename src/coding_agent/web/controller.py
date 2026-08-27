"""RunController: one active run, worker thread, cancellation and events.

The controller is an *adapter* around the existing AgentLoop: it never
reimplements agent logic. It spawns a worker thread so the HTTP event loop
is never blocked by a model or tool call, exposes a cancellation seam (the
same ``is_cancelled`` callable the loop already supports), keeps a bounded
event store (count + character budget) and produces exactly one terminal
snapshot per run.

Bounded storage detail: events are kept as a contiguous tail. When the tail
outgrows its budget the oldest events are dropped and ``events_retained_from``
moves forward; SSE clients that fall behind are told to reset and refetch
the snapshot instead of receiving a gap.
"""

from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from ..agent import AgentLoop
from ..completion import CompletionPolicy
from ..config import (
    DEFAULT_CHAR_BUDGET,
    DEFAULT_MAX_STEPS,
    ResolvedModelConnection,
    resolve_connection,
)
from ..context import ContextManager
from ..credentials import CredentialError, CredentialService
from ..errors import ConfigError
from ..model_client import ModelClient, ModelClientFactory
from ..models import AgentEvent, LoopPhase, RunResult, RunStatus, StopReason
from ..provider_config import (
    ProfileError,
    ProfileStore,
    ProviderProfile,
    default_home,
)
from ..tools import build_default_tools
from ..tools.executor import ToolExecutor
from ..tools.observation import FileObservationTracker
from ..tools.paths import Workspace
from ..tools.policy import WorkspaceToolPolicy
from .redaction import redact_public_payload, redact_verification_summary
from .schemas import (
    EVENT_PAYLOAD_KEYS,
    KNOWN_EVENT_KINDS,
    ErrorDetail,
    RunSnapshotDTO,
    RunStartRequest,
    ToolEventDTO,
    VerificationDTO,
)

logger = logging.getLogger("coding_agent.web.controller")

DEFAULT_MAX_EVENTS = 2_000
DEFAULT_MAX_EVENT_CHARS = 1_000_000

_TERMINAL_ERROR_MESSAGES: Dict[StopReason, Tuple[str, str]] = {
    StopReason.MAX_STEPS: (
        "run_failed",
        "达到最大逻辑步数，运行被终止；请缩小任务范围后再试",
    ),
    StopReason.MODEL_ERROR: (
        "model_error",
        "模型请求失败；请检查 profile 的 key、模型名与 base URL",
    ),
    StopReason.PROTOCOL_ERROR: (
        "protocol_error",
        "模型返回了无效的响应或工具调用，无法继续",
    ),
    StopReason.CONTEXT_OVERFLOW: (
        "context_overflow",
        "对话上下文超出预算，请缩小任务范围或重新开始",
    ),
    StopReason.TOOL_FAILURE_LIMIT: (
        "tool_failure_limit",
        "工具连续失败次数超出限制，运行被终止",
    ),
    StopReason.REPEATED_TOOL_CALL: (
        "repeated_tool_call",
        "模型重复调用同一工具签名，运行被终止",
    ),
    StopReason.INTERNAL_ERROR: (
        "internal_error",
        "AgentLoop 内部错误，已保持运行终态；请查看日志",
    ),
}


class RunControllerError(Exception):
    """Request-level failure with a stable machine-readable code."""

    def __init__(self, code: str, message: str, *, field: Optional[str] = None):
        super().__init__(message)
        self.code = code
        self.field = field


class _EventSinkAdapter:
    """Adapt a callable to the EventSink protocol used by AgentLoop."""

    __slots__ = ("_fn",)

    def __init__(self, fn) -> None:
        self._fn = fn

    def emit(self, event) -> None:
        self._fn(event)


class RunController:
    def __init__(
        self,
        *,
        home: Optional[Path] = None,
        env: Optional[Mapping[str, str]] = None,
        max_events: int = DEFAULT_MAX_EVENTS,
        max_event_chars: int = DEFAULT_MAX_EVENT_CHARS,
        client_factory: Callable[
            [ResolvedModelConnection], ModelClient
        ] = ModelClientFactory.create,
        loop_builder: Optional[Callable[..., AgentLoop]] = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        resolved_home = Path(home) if home is not None else default_home()
        self._env: Dict[str, str] = dict(env) if env is not None else dict(os.environ)
        self.profile_store = ProfileStore(resolved_home)
        self.credentials = CredentialService(resolved_home, self._env)
        self._client_factory = client_factory
        self._loop_builder = loop_builder
        self._sleeper = sleeper
        self._max_events = max_events
        self._max_event_chars = max_event_chars

        self._lock = threading.RLock()
        self._state = "idle"  # idle | running | terminal
        self._run_id: Optional[str] = None
        self._task: Optional[str] = None
        self._workspace: Optional[Path] = None
        self._profile_id: Optional[str] = None
        self._profile_display_name: Optional[str] = None
        self._model_label: Optional[str] = None
        self._cancel_event: Optional[threading.Event] = None
        self._worker: Optional[threading.Thread] = None
        self._events: List[ToolEventDTO] = []
        self._events_total = 0
        self._events_chars = 0
        self._event_seq = 0
        self._phase: Optional[str] = None
        self._started_wall: Optional[float] = None
        self._finished_wall: Optional[float] = None
        self._started_mono: Optional[float] = None
        self._finished_mono: Optional[float] = None
        self._result: Optional[RunResult] = None
        self._terminal_error: Optional[ErrorDetail] = None
        self._finished_flag = False
        # Live counters updated from the event stream so the inspector shows
        # real running-time facts instead of all-zero placeholders.
        self._live_step = 0
        self._live_provider_attempts = 0
        self._live_tool_count = 0
        self._live_verification: Optional[str] = None
        self._exception_status: Optional[str] = None
        self._exception_stop_reason: Optional[str] = None

    # ------------------------------------------------------------ read API

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    @property
    def run_id(self) -> Optional[str]:
        with self._lock:
            return self._run_id

    def snapshot(self) -> RunSnapshotDTO:
        with self._lock:
            return self._build_snapshot_locked()

    def take_events(self, last_id: Optional[int]) -> Tuple[List[ToolEventDTO], bool]:
        """Return events after ``last_id`` and whether the client must reset.

        Reset is required when the requested id is older than the retained
        tail (or the tail was emptied); the client should then replace its
        whole event list with the returned batch.
        """
        with self._lock:
            if last_id is None:
                return list(self._events), bool(self._events)
            if not self._events:
                # The tail was completely dropped: a client that is behind
                # must clear its stale events even though there is nothing
                # new to send.
                return [], last_id < self._event_seq
            head_id = self._events[0].id
            tail_id = self._events[-1].id
            if last_id < head_id - 1:
                return list(self._events), True
            if last_id >= tail_id:
                return [], False
            return [event for event in self._events if event.id > last_id], False

    # ------------------------------------------------------------ run start

    def start(self, request: RunStartRequest) -> RunSnapshotDTO:
        workspace = self._validate_workspace(request.workspace)
        task = self._validate_task(request.task)
        connection, profile = self._resolve_connection(request.profile_id)

        with self._lock:
            if self._state == "running":
                raise RunControllerError(
                    "run_already_active",
                    "已有正在运行的 Agent 任务，请先取消或等待其完成",
                )
            run_id = uuid.uuid4().hex
            cancel_event = threading.Event()
            builder = self._loop_builder or self._default_loop_builder
            # Build the loop before mutating any run state: if the builder
            # raises (invalid connection, programmer error, injected test
            # failure) the controller stays in its previous state instead of
            # being stuck in a phantom "running" state with no worker.
            loop = builder(
                connection=connection,
                workspace=workspace,
                task=task,
                run_id=run_id,
                cancel_event=cancel_event,
                sink=self._on_agent_event,
            )
            self._reset_run_state()
            self._state = "running"
            self._run_id = run_id
            self._task = task
            self._workspace = workspace
            self._profile_id = profile.id if profile else None
            self._profile_display_name = profile.display_name if profile else None
            self._model_label = f"{connection.model} @ {connection.base_url if connection.base_url else 'legacy env'}"
            self._cancel_event = cancel_event
            self._started_wall = time.time()
            self._started_mono = time.monotonic()
            self._finished_wall = None
            self._finished_mono = None
            self._phase = LoopPhase.INITIALIZING.value

            worker = threading.Thread(
                target=self._run_worker,
                args=(loop, task, run_id),
                name=f"coding-agent-run-{run_id[:8]}",
                daemon=True,
            )
            self._worker = worker
            worker.start()
            return self._build_snapshot_locked()

    def cancel(self) -> RunSnapshotDTO:
        with self._lock:
            if self._state == "idle":
                raise RunControllerError("run_not_found", "当前没有可取消的运行")
            if self._cancel_event is not None and self._state == "running":
                self._cancel_event.set()
            # Idempotent for terminal runs: return the current snapshot.
            return self._build_snapshot_locked()

    def shutdown(self, timeout: float = 5.0) -> None:
        with self._lock:
            event = self._cancel_event
            worker = self._worker
            state = self._state
        if state == "running" and event is not None:
            event.set()
        if worker is not None and worker.is_alive():
            worker.join(timeout=timeout)

    # ------------------------------------------------------------ validation

    @staticmethod
    def _validate_workspace(value: str) -> Path:
        if not isinstance(value, str) or not value.strip():
            raise RunControllerError(
                "invalid_workspace", "工作区路径不能为空", field="workspace"
            )
        try:
            path = Path(value.strip()).expanduser().resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise RunControllerError(
                "invalid_workspace",
                f"工作区不存在或不可访问：{value.strip()}",
                field="workspace",
            ) from exc
        if not path.is_dir():
            raise RunControllerError(
                "invalid_workspace",
                f"工作区不是目录：{path}",
                field="workspace",
            )
        return path

    @staticmethod
    def _validate_task(value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise RunControllerError("invalid_task", "任务描述不能为空", field="task")
        task = value.strip()
        if len(task) > 100_000:
            raise RunControllerError(
                "invalid_task", "任务描述过长（最多 100000 字符）", field="task"
            )
        return task

    def _resolve_connection(
        self, explicit_profile: Optional[str]
    ) -> Tuple[ResolvedModelConnection, Optional[ProviderProfile]]:
        try:
            config = self.profile_store.load()
            profiles = {p.id: p for p in config.profiles.values()}
            selected_id = explicit_profile or config.active_profile
            selected = profiles.get(selected_id) if selected_id else None
            connection = resolve_connection(
                profiles=profiles,
                active_profile=config.active_profile,
                explicit_profile=explicit_profile,
                env=self._env,
                credential_resolver=self.credentials.resolve,
            )
            return connection, selected
        except (ProfileError, ConfigError) as exc:
            field = getattr(exc, "field", None)
            code = getattr(exc, "code", "invalid_config")
            raise RunControllerError(code, str(exc), field=field) from exc
        except CredentialError as exc:
            raise RunControllerError(exc.code, str(exc), field=exc.field) from exc

    # ------------------------------------------------------------ internals

    def _reset_run_state(self) -> None:
        self._events = []
        self._events_total = 0
        self._events_chars = 0
        self._event_seq = 0
        self._result = None
        self._terminal_error = None
        self._finished_flag = False
        self._live_step = 0
        self._live_provider_attempts = 0
        self._live_tool_count = 0
        self._live_verification = None
        self._exception_status = None
        self._exception_stop_reason = None

    def _default_loop_builder(
        self,
        *,
        connection: ResolvedModelConnection,
        workspace: Path,
        task: str,  # noqa: ARG004 - kept for API symmetry with injected builders
        run_id: str,
        cancel_event: threading.Event,
        sink,
    ) -> AgentLoop:
        tracker = FileObservationTracker()
        registry = build_default_tools(
            Workspace(workspace), tracker, cancel_event.is_set
        )
        executor = ToolExecutor(registry, WorkspaceToolPolicy(), cancel_event.is_set)
        return AgentLoop(
            model_client=self._client_factory(connection),
            tool_registry=registry,
            tool_executor=executor,
            context_manager=ContextManager(DEFAULT_CHAR_BUDGET),
            completion_policy=CompletionPolicy(),
            run_id=run_id,
            max_steps=DEFAULT_MAX_STEPS,
            is_cancelled=cancel_event.is_set,
            event_sink=_EventSinkAdapter(sink),
        )

    def _run_worker(self, loop: AgentLoop, task: str, run_id: str) -> None:
        try:
            result = loop.run(task)
        except Exception as exc:  # defensive: keep a unique terminal snapshot
            self._finish_with_exception(run_id, exc)
            return
        self._finish_with_result(result)

    def _finish_with_result(self, result: RunResult) -> None:
        with self._lock:
            if self._finished_flag or self._run_id != result.run_id:
                return
            self._finished_flag = True
            self._result = result
            self._phase = result.final_phase.value
            self._state = "terminal"
            self._finished_wall = time.time()
            self._finished_mono = time.monotonic()
            self._terminal_error = self._error_for_result(result)

    def _finish_with_exception(self, run_id: str, exc: Exception) -> None:
        # Only the exception type is logged; exception text may originate
        # from third-party SDKs and could contain secrets. It is also never
        # copied into the public API, regardless of exception type.
        logger.warning(
            "run %s failed inside worker: %s",
            run_id[:8],
            type(exc).__name__,
        )
        with self._lock:
            if self._finished_flag or self._run_id != run_id:
                return
            self._finished_flag = True
            self._phase = LoopPhase.TERMINAL.value
            self._state = "terminal"
            self._exception_status = "ERROR"
            self._exception_stop_reason = "INTERNAL_ERROR"
            self._finished_wall = time.time()
            self._finished_mono = time.monotonic()
            self._terminal_error = ErrorDetail(
                code="internal_error",
                message="AgentLoop 内部错误，运行已终止",
            )

    @staticmethod
    def _error_for_result(result: RunResult) -> Optional[ErrorDetail]:
        if result.status is not RunStatus.ERROR:
            return None
        code, message = _TERMINAL_ERROR_MESSAGES.get(
            result.stop_reason, ("run_failed", "运行失败")
        )
        return ErrorDetail(code=code, message=message)

    def _on_agent_event(self, event: AgentEvent) -> None:
        if event.type not in KNOWN_EVENT_KINDS:
            return
        with self._lock:
            if self._run_id is not None and event.run_id != self._run_id:
                return
            self._event_seq += 1
            keys = EVENT_PAYLOAD_KEYS[event.type]
            payload: Dict[str, Any] = {
                key: value for key, value in event.payload.items() if key in keys
            }
            payload = redact_public_payload(event.type, payload)
            self._update_live_facts(event, payload)
            dto = ToolEventDTO(
                id=self._event_seq,
                kind=event.type,
                step=event.step,
                phase=event.phase.value,
                payload=payload,
            )
            self._append_event(dto)
            self._phase = event.phase.value

    def _update_live_facts(self, event: AgentEvent, payload: Dict[str, Any]) -> None:
        if event.type == "step_started":
            self._live_step = event.step
            self._live_provider_attempts += 1
        elif event.type == "model_retry":
            # A retry event means another provider attempt is about to start.
            self._live_provider_attempts += 1
        elif event.type == "tool_started":
            self._live_tool_count += 1
        elif event.type == "completion_deferred":
            value = payload.get("verification_status")
            if isinstance(value, str):
                self._live_verification = value

    def _append_event(self, dto: ToolEventDTO) -> None:
        self._events.append(dto)
        self._events_total += 1
        self._events_chars += _event_json_chars(dto)
        while (
            len(self._events) > self._max_events
            or self._events_chars > self._max_event_chars
        ):
            if not self._events:
                break
            dropped = self._events.pop(0)
            self._events_chars -= _event_json_chars(dropped)

    def _build_snapshot_locked(self) -> RunSnapshotDTO:
        result = self._result
        running = self._state == "running"
        last_verification = None
        if result is not None and result.last_verification is not None:
            last_verification = VerificationDTO(
                command=redact_verification_summary(
                    result.last_verification.get("command")
                ),
                exit_code=result.last_verification.get("exit_code"),
            )
        elapsed = None
        if self._started_mono is not None:
            end = self._finished_mono or time.monotonic()
            elapsed = int((end - self._started_mono) * 1000)
        head_id = self._events[0].id if self._events else self._event_seq + 1
        verification = None
        if result is not None:
            verification = result.verification_status.value
        elif running:
            # No verification conclusion exists yet during a run; showing
            # NOT_APPLICABLE here would be a false "nothing to verify".
            verification = self._live_verification or "NOT_RUN"
        return RunSnapshotDTO(
            run_id=self._run_id or "",
            state=self._state,
            status=(
                result.status.value if result is not None else self._exception_status
            ),
            phase=self._phase,
            stop_reason=(
                result.stop_reason.value
                if result is not None
                else self._exception_stop_reason
            ),
            verification_status=verification,
            final_text=result.final_text if result else None,
            task=self._task,
            step_count=(result.step_count if result else self._live_step),
            provider_attempt_count=(
                result.provider_attempt_count
                if result
                else self._live_provider_attempts
            ),
            tool_call_count=(
                result.tool_call_count if result else self._live_tool_count
            ),
            mutated_paths=list(result.mutated_paths) if result else [],
            last_verification=last_verification,
            started_at=self._started_wall,
            finished_at=self._finished_wall,
            elapsed_ms=elapsed,
            error=self._terminal_error,
            events=list(self._events),
            events_total=self._events_total,
            events_retained_from=head_id,
        )


def _event_json_chars(dto: ToolEventDTO) -> int:
    # Approximation used only for budgeting; deterministic and independent of
    # json module import costs.
    return (
        16
        + len(dto.kind)
        + len(dto.phase)
        + sum(len(str(k)) + len(str(v)) for k, v in dto.payload.items())
    )
