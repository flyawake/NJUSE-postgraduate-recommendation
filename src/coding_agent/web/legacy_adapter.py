"""Legacy ``/api/runs`` projection backed by ConversationService.

This compatibility layer owns no worker, model history, event journal, or
tool executor. It only remembers which persisted conversation/turn is exposed
through the old single-run API and projects those facts into RunSnapshotDTO.
"""

from __future__ import annotations

import threading
from datetime import datetime
from typing import List, Optional, Tuple

from ..conversations.service import ConversationService, ConversationServiceError
from .controller import RunControllerError
from .redaction import redact_public_payload, redact_verification_summary
from .schemas import (
    EVENT_PAYLOAD_KEYS,
    ErrorDetail,
    RunSnapshotDTO,
    RunStartRequest,
    ToolEventDTO,
    VerificationDTO,
)

_LIVE_PHASE_AFTER_EVENT = {
    "run_started": "READY",
    "step_started": "REQUESTING_MODEL",
    "model_retry": "REQUESTING_MODEL",
    "assistant_received": "HANDLING_RESPONSE",
    "tool_started": "EXECUTING_TOOLS",
    "tool_finished": "EXECUTING_TOOLS",
    "completion_deferred": "READY",
    "run_finished": "TERMINAL",
}

_ERRORS = {
    "MAX_STEPS": ("run_failed", "达到最大逻辑步数，运行被终止；请缩小任务范围后再试"),
    "MODEL_ERROR": (
        "model_error",
        "模型请求失败；请检查 profile 的 key、模型名与 base URL",
    ),
    "PROTOCOL_ERROR": ("protocol_error", "模型返回了无效的响应或工具调用，无法继续"),
    "CONTEXT_OVERFLOW": (
        "context_overflow",
        "对话上下文超出预算，请缩小任务范围或重新开始",
    ),
    "TOOL_FAILURE_LIMIT": (
        "tool_failure_limit",
        "工具连续失败次数超出限制，运行被终止",
    ),
    "REPEATED_TOOL_CALL": (
        "repeated_tool_call",
        "模型重复调用同一工具签名，运行被终止",
    ),
    "INTERNAL_ERROR": ("internal_error", "AgentLoop 内部错误，运行已终止"),
}


def _timestamp(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return None


class ConversationRunAdapter:
    """Project one persisted turn through the deprecated single-run shape."""

    def __init__(
        self,
        service: ConversationService,
        *,
        max_events: int = 2_000,
        max_event_chars: int = 1_000_000,
    ) -> None:
        self._service = service
        self._max_events = max_events
        self._max_event_chars = max_event_chars
        self._lock = threading.RLock()
        self._conversation_id: Optional[str] = None
        self._turn_id: Optional[str] = None

    def start(self, request: RunStartRequest) -> RunSnapshotDTO:
        with self._lock:
            if self._turn_id is not None and self.snapshot().state == "running":
                raise RunControllerError(
                    "run_already_active",
                    "已有正在运行的 Agent 任务，请先取消或等待其完成",
                )
        conversation = self._service.create_conversation(
            workspace_path=request.workspace,
            profile_id=request.profile_id,
            title=request.task.strip()[:40] or None,
        )
        try:
            turn = self._service.start_turn(conversation["id"], user_text=request.task)
        except ConversationServiceError as exc:
            self._cleanup_failed_start(conversation["id"])
            raise RunControllerError(exc.code, str(exc), field=exc.field) from exc
        with self._lock:
            self._conversation_id = conversation["id"]
            self._turn_id = turn["id"]
        return self.snapshot()

    def cancel(self) -> RunSnapshotDTO:
        with self._lock:
            conversation_id = self._conversation_id
            turn_id = self._turn_id
        if conversation_id is None or turn_id is None:
            raise RunControllerError("run_not_found", "当前没有运行")
        snap = self.snapshot()
        if snap.state == "terminal":
            return snap
        try:
            self._service.cancel_turn(conversation_id, turn_id)
        except ConversationServiceError as exc:
            if exc.code != "turn_not_active":
                raise RunControllerError(exc.code, str(exc), field=exc.field) from exc
        return self.snapshot()

    def snapshot(self) -> RunSnapshotDTO:
        with self._lock:
            conversation_id = self._conversation_id
            turn_id = self._turn_id
        if conversation_id is None or turn_id is None:
            return RunSnapshotDTO(run_id="", state="idle")
        try:
            turn = self._service.get_turn(conversation_id, turn_id)
            raw_events = self._service.get_events(
                conversation_id, turn_id, limit=10_000
            )
        except ConversationServiceError:
            return RunSnapshotDTO(run_id="", state="idle")
        events = self._public_events(raw_events)
        retained = self._bound_events(events)
        result = turn.get("result") or {}
        active = bool(turn.get("active"))
        started = _timestamp(turn.get("started_at") or turn.get("created_at"))
        finished = _timestamp(turn.get("finished_at"))
        end = finished
        elapsed = (
            int(((end or datetime.now().timestamp()) - started) * 1000)
            if started
            else None
        )
        last_event = events[-1] if events else None
        step_count = max((item.step for item in events), default=0)
        provider_attempts = sum(item.kind == "step_started" for item in events)
        provider_attempts += sum(item.kind == "model_retry" for item in events)
        tool_count = sum(item.kind == "tool_started" for item in events)
        verification = result.get("verification_status")
        if active and not verification:
            deferred = [
                item.payload.get("verification_status")
                for item in events
                if item.kind == "completion_deferred"
            ]
            verification = deferred[-1] if deferred else "NOT_RUN"
        last_verification = self._verification(result.get("last_verification"))
        stop_reason = result.get("stop_reason") or turn.get("error_code")
        error = self._terminal_error(result.get("status"), stop_reason)
        return RunSnapshotDTO(
            run_id=str(turn.get("run_id") or ""),
            state="running" if active else "terminal",
            status=result.get("status"),
            phase=(
                result.get("final_phase")
                if not active
                else _LIVE_PHASE_AFTER_EVENT.get(last_event.kind, last_event.phase)
                if last_event
                else "INITIALIZING"
            ),
            stop_reason=stop_reason,
            verification_status=verification,
            final_text=result.get("final_text"),
            task=turn.get("user_text"),
            step_count=int(result.get("step_count", step_count)),
            provider_attempt_count=int(
                result.get("provider_attempt_count", provider_attempts)
            ),
            tool_call_count=int(result.get("tool_call_count", tool_count)),
            mutated_paths=list(result.get("mutated_paths", [])),
            last_verification=last_verification,
            started_at=started,
            finished_at=finished,
            elapsed_ms=elapsed,
            error=error,
            events=retained,
            events_total=len(events),
            events_retained_from=(retained[0].id if retained else len(events) + 1),
        )

    def take_events(self, last_id: Optional[int]) -> Tuple[List[ToolEventDTO], bool]:
        snap = self.snapshot()
        events = snap.events
        if last_id is None:
            return events, bool(events)
        if not events:
            return [], last_id < snap.events_total
        if last_id < events[0].id - 1:
            return events, True
        return [event for event in events if event.id > last_id], False

    def _cleanup_failed_start(self, conversation_id: str) -> None:
        try:
            current = self._service.get_conversation(conversation_id)
            self._service.delete_conversation(
                conversation_id, expected_version=current["version"]
            )
        except ConversationServiceError:
            pass

    @staticmethod
    def _public_events(raw_events: list[dict]) -> List[ToolEventDTO]:
        result: List[ToolEventDTO] = []
        for raw in raw_events:
            kind = str(raw.get("kind", ""))
            keys = EVENT_PAYLOAD_KEYS.get(kind)
            if keys is None:
                continue
            payload = {
                key: value
                for key, value in dict(raw.get("payload") or {}).items()
                if key in keys
            }
            payload = redact_public_payload(kind, payload)
            raw_target = payload.pop("target", raw.get("target"))
            result.append(
                ToolEventDTO(
                    id=int(raw.get("id", 0)),
                    kind=kind,
                    step=int(raw.get("step", 0)),
                    phase=str(raw.get("phase", "INITIALIZING")),
                    target=raw_target if isinstance(raw_target, str) else None,
                    payload=payload,
                )
            )
        return result

    def _bound_events(self, events: List[ToolEventDTO]) -> List[ToolEventDTO]:
        retained: List[ToolEventDTO] = []
        chars = 0
        for event in reversed(events):
            size = (
                16
                + len(event.kind)
                + len(event.phase)
                + sum(
                    len(str(key)) + len(str(value))
                    for key, value in event.payload.items()
                )
            )
            if retained and (
                len(retained) >= self._max_events
                or chars + size > self._max_event_chars
            ):
                break
            if not retained and size > self._max_event_chars:
                continue
            retained.append(event)
            chars += size
        retained.reverse()
        return retained

    @staticmethod
    def _verification(raw: object) -> Optional[VerificationDTO]:
        if not isinstance(raw, dict):
            return None
        command = raw.get("command")
        display = (
            str(command or "")[:160]
            if raw.get("command_redacted") is True
            else redact_verification_summary(command)
        )
        return VerificationDTO(command=display, exit_code=raw.get("exit_code"))

    @staticmethod
    def _terminal_error(status: object, reason: object) -> Optional[ErrorDetail]:
        if status != "ERROR":
            return None
        code, message = _ERRORS.get(str(reason), ("run_failed", "运行失败"))
        return ErrorDetail(code=code, message=message)
