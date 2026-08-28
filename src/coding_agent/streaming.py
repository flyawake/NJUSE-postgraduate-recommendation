"""Provider-neutral streaming contract for task_005.

This module is the anti-corruption boundary: SDK chunk objects never cross it.
Adapters translate chunks into :class:`ModelStreamEvent`, and
:class:`TurnStreamAccumulator` validates/aggregates them into the single
:class:`AssistantTurn` that AgentLoop may commit to canonical history.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .errors import ModelRequestError
from .models import AssistantTurn, ProviderContinuation, ToolCall

REASONING_NONE = "none"
REASONING_RAW_VISIBLE = "raw_visible"
REASONING_SUMMARY = "summary"
REASONING_OPAQUE = "opaque"


@dataclass(frozen=True)
class ModelCapabilities:
    wire_api: str
    streaming: bool = True
    tool_calling: bool = True
    parallel_tool_calls: bool = False
    visible_reasoning: str = REASONING_NONE
    reasoning_efforts: Tuple[str, ...] = ()
    usage_in_stream: bool = False
    supports_cancel: bool = True


@dataclass(frozen=True)
class ModelRequestOptions:
    reasoning_mode: str = "auto"  # auto | off | visible
    reasoning_effort: Optional[str] = None
    stream: bool = True


@dataclass(frozen=True)
class StreamStarted:
    response_id: Optional[str] = None


@dataclass(frozen=True)
class TextDelta:
    output_index: int
    delta: str


@dataclass(frozen=True)
class ReasoningDelta:
    output_index: int
    delta: str
    visibility: str = REASONING_RAW_VISIBLE


@dataclass(frozen=True)
class ReasoningSummaryDelta:
    output_index: int
    summary_index: int
    delta: str


@dataclass(frozen=True)
class ToolCallStarted:
    output_index: int
    tool_index: int
    call_id: Optional[str] = None
    name: Optional[str] = None


@dataclass(frozen=True)
class ToolCallArgumentsDelta:
    output_index: int
    tool_index: int
    delta: str


@dataclass(frozen=True)
class RefusalDelta:
    output_index: int
    delta: str


@dataclass(frozen=True)
class UsageReceived:
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    reasoning_tokens: Optional[int] = None


@dataclass(frozen=True)
class OpaqueContinuationReceived:
    wire_api: str
    item_id: str
    encrypted_content: str
    summary: Tuple[str, ...] = ()


@dataclass(frozen=True)
class StreamCompleted:
    finish_reason: str
    raw_status: Optional[str] = None


@dataclass(frozen=True)
class StreamFailed:
    code: str = "stream_error"
    message: str = "provider stream failed"
    retryable: bool = False


ModelStreamEvent = (
    StreamStarted
    | TextDelta
    | ReasoningDelta
    | ReasoningSummaryDelta
    | ToolCallStarted
    | ToolCallArgumentsDelta
    | RefusalDelta
    | UsageReceived
    | OpaqueContinuationReceived
    | StreamCompleted
    | StreamFailed
)


@dataclass
class _ToolDraft:
    call_id: Optional[str]
    name: Optional[str]
    arguments: List[str] = field(default_factory=list)


class TurnStreamAccumulator:
    """Aggregate neutral stream events into one validated AssistantTurn.

    Text, reasoning and tool-call fragments use independent buffers. A stream
    is only considered complete after :class:`StreamCompleted` plus the checks
    in :meth:`to_turn`; until then no canonical history is written.
    """

    def __init__(self) -> None:
        self._text_parts: Dict[int, List[str]] = {}
        self._reasoning_parts: Dict[int, List[str]] = {}
        self._summary_parts: Dict[Tuple[int, int], List[str]] = {}
        self._tools: Dict[Tuple[int, int], _ToolDraft] = {}
        self._tool_order: List[Tuple[int, int]] = []
        self._refusal_parts: List[str] = []
        self._finish_reason: Optional[str] = None
        self._completed = False
        self._has_output = False
        self._usage: Optional[UsageReceived] = None
        self._response_id: Optional[str] = None
        self._continuations: List[ProviderContinuation] = []
        self._started = False

    @property
    def has_output(self) -> bool:
        """True once any text/reasoning/tool fragment has been accepted."""
        return self._has_output

    @property
    def completed(self) -> bool:
        return self._completed

    @property
    def finish_reason(self) -> Optional[str]:
        return self._finish_reason

    @property
    def usage(self) -> Optional[UsageReceived]:
        return self._usage

    def absorb(self, event: ModelStreamEvent) -> None:
        if self._completed:
            raise ModelRequestError(
                "stream event arrived after completion", retryable=False
            )
        if isinstance(event, StreamStarted):
            if self._started:
                raise ModelRequestError("duplicate stream start", retryable=False)
            self._started = True
            self._response_id = event.response_id
            return
        if not self._started:
            raise ModelRequestError(
                "stream event arrived before stream start", retryable=False
            )
        if isinstance(event, TextDelta):
            self._require_index(event.output_index, "output_index")
            self._text_parts.setdefault(event.output_index, []).append(event.delta)
            self._has_output = True
            return
        if isinstance(event, ReasoningDelta):
            self._require_index(event.output_index, "output_index")
            self._reasoning_parts.setdefault(event.output_index, []).append(event.delta)
            self._has_output = True
            return
        if isinstance(event, ReasoningSummaryDelta):
            self._require_index(event.output_index, "output_index")
            self._require_index(event.summary_index, "summary_index")
            key = (event.output_index, event.summary_index)
            self._summary_parts.setdefault(key, []).append(event.delta)
            self._has_output = True
            return
        if isinstance(event, ToolCallStarted):
            self._require_index(event.output_index, "output_index")
            self._require_index(event.tool_index, "tool_index")
            key = (event.output_index, event.tool_index)
            draft = self._tools.get(key)
            if draft is None:
                draft = _ToolDraft(event.call_id, event.name)
                self._tools[key] = draft
                self._tool_order.append(key)
            else:
                if event.call_id and draft.call_id and event.call_id != draft.call_id:
                    raise ModelRequestError(
                        "conflicting tool call id for stream index", retryable=False
                    )
                if event.name and draft.name and event.name != draft.name:
                    raise ModelRequestError(
                        "conflicting tool name for stream index", retryable=False
                    )
                draft.call_id = draft.call_id or event.call_id
                draft.name = draft.name or event.name
            self._has_output = True
            return
        if isinstance(event, ToolCallArgumentsDelta):
            self._require_index(event.output_index, "output_index")
            self._require_index(event.tool_index, "tool_index")
            key = (event.output_index, event.tool_index)
            draft = self._tools.get(key)
            if draft is None:
                raise ModelRequestError(
                    "tool arguments arrived before tool call start", retryable=False
                )
            draft.arguments.append(event.delta)
            self._has_output = True
            return
        if isinstance(event, RefusalDelta):
            self._require_index(event.output_index, "output_index")
            self._refusal_parts.append(event.delta)
            self._has_output = True
            return
        if isinstance(event, UsageReceived):
            self._usage = event
            return
        if isinstance(event, OpaqueContinuationReceived):
            if not event.item_id or not event.encrypted_content:
                raise ModelRequestError(
                    "opaque continuation is incomplete", retryable=False
                )
            self._continuations.append(
                ProviderContinuation(
                    wire_api=event.wire_api,
                    item_id=event.item_id,
                    encrypted_content=event.encrypted_content,
                    summary=event.summary,
                )
            )
            return
        if isinstance(event, StreamCompleted):
            if event.finish_reason not in {"stop", "tool_calls", "refusal"}:
                raise ModelRequestError(
                    f"unsupported stream finish reason: {event.finish_reason}",
                    retryable=False,
                )
            self._finish_reason = event.finish_reason
            self._completed = True
            return
        if isinstance(event, StreamFailed):
            raise ModelRequestError(event.message, retryable=event.retryable)

    @property
    def text(self) -> str:
        return "".join(
            part
            for output_index in sorted(self._text_parts)
            for part in self._text_parts[output_index]
        )

    @property
    def reasoning_text(self) -> str:
        return "".join(
            part
            for output_index in sorted(self._reasoning_parts)
            for part in self._reasoning_parts[output_index]
        )

    @property
    def reasoning_summary(self) -> str:
        return "".join(
            part
            for key in sorted(self._summary_parts)
            for part in self._summary_parts[key]
        )

    def to_turn(self) -> AssistantTurn:
        if not self._completed:
            raise ModelRequestError("stream has not completed", retryable=False)
        if self.finish_reason == "tool_calls" and not self._tools:
            raise ModelRequestError(
                "finish_reason=tool_calls but no tool call started",
                retryable=False,
            )
        tool_calls: List[ToolCall] = []
        for key in sorted(self._tool_order):
            draft = self._tools[key]
            call_id = draft.call_id
            name = draft.name
            if not call_id or not name:
                raise ModelRequestError(
                    "tool call missing id/name at stream completion",
                    retryable=False,
                )
            arguments = "".join(draft.arguments)
            if not arguments:
                arguments = "{}"
            tool_calls.append(ToolCall(id=call_id, name=name, arguments_raw=arguments))
        if self.finish_reason in ("stop", "tool_calls") and not tool_calls:
            if not self.text.strip() and not self.refusal:
                raise ModelRequestError(
                    "assistant produced neither text nor tool call", retryable=False
                )
        reasoning = self.reasoning_text or self.reasoning_summary or None
        text = self.text or self.refusal
        return AssistantTurn(
            text=text,
            tool_calls=tuple(tool_calls),
            reasoning=reasoning,
            continuations=tuple(self._continuations),
        )

    @property
    def refusal(self) -> str:
        return "".join(self._refusal_parts)

    @staticmethod
    def _require_index(value: int, name: str) -> None:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ModelRequestError(f"invalid {name} in stream", retryable=False)
