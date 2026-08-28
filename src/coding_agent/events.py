"""Event protocol and names.

AgentLoop emits events through an injected EventSink; it never prints. The
CLI consumes events and is the only place that renders them.
"""

from __future__ import annotations

from typing import Protocol

from .models import AgentEvent

EVENT_RUN_STARTED = "run_started"
EVENT_STEP_STARTED = "step_started"
EVENT_MODEL_RETRY = "model_retry"
EVENT_MODEL_STREAM_STARTED = "model_stream_started"
EVENT_ASSISTANT_TEXT_DELTA = "assistant_text_delta"
EVENT_REASONING_DELTA = "reasoning_delta"
EVENT_REASONING_SUMMARY_DELTA = "reasoning_summary_delta"
EVENT_STREAM_ATTEMPT_ABANDONED = "stream_attempt_abandoned"
EVENT_ASSISTANT_RECEIVED = "assistant_received"
EVENT_TOOL_STARTED = "tool_started"
EVENT_TOOL_FINISHED = "tool_finished"
EVENT_COMPLETION_DEFERRED = "completion_deferred"
EVENT_RUN_FINISHED = "run_finished"

REQUIRED_EVENT_TYPES: tuple[str, ...] = (
    EVENT_RUN_STARTED,
    EVENT_STEP_STARTED,
    EVENT_MODEL_RETRY,
    EVENT_MODEL_STREAM_STARTED,
    EVENT_ASSISTANT_TEXT_DELTA,
    EVENT_REASONING_DELTA,
    EVENT_REASONING_SUMMARY_DELTA,
    EVENT_STREAM_ATTEMPT_ABANDONED,
    EVENT_ASSISTANT_RECEIVED,
    EVENT_TOOL_STARTED,
    EVENT_TOOL_FINISHED,
    EVENT_COMPLETION_DEFERRED,
    EVENT_RUN_FINISHED,
)


class EventSink(Protocol):
    def emit(self, event: AgentEvent) -> None:  # noqa: D102 - protocol docstring
        ...
