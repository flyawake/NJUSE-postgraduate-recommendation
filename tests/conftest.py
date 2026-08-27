"""Shared test fixtures and helpers.

All tests are offline: ScriptedModel fakes the provider, tools run against
tmp_path workspaces, and no test reads a real API key.
"""

from __future__ import annotations

import copy
import json
from collections import deque
from typing import Any, Callable, Deque, Dict, List, Optional, Sequence, Tuple

import pytest

from coding_agent.agent import AgentLoop
from coding_agent.completion import CompletionPolicy
from coding_agent.context import ContextManager
from coding_agent.models import (
    AgentEvent,
    AssistantMessage,
    AssistantTurn,
    ToolCall,
    ToolMessage,
)
from coding_agent.tools import build_default_tools
from coding_agent.tools.executor import ToolExecutor
from coding_agent.tools.observation import FileObservationTracker
from coding_agent.tools.paths import Workspace
from coding_agent.tools.policy import ToolPolicy, WorkspaceToolPolicy

_counter = iter(range(100000))


def call_id() -> str:
    return f"call_{next(_counter)}"


def make_tool_call(
    name: str,
    args: Dict[str, Any] | None = None,
    call_id_value: Optional[str] = None,
) -> ToolCall:
    raw = json.dumps(args or {}, ensure_ascii=False, sort_keys=True)
    resolved_id = call_id() if call_id_value is None else call_id_value
    return ToolCall(id=resolved_id, name=name, arguments_raw=raw)


def turn(text: str = "", calls: Sequence[ToolCall] = ()) -> AssistantTurn:
    return AssistantTurn(text=text, tool_calls=tuple(calls))


class ScriptedModel:
    """Offline model client that replays pre-scripted AssistantTurns."""

    def __init__(self, turns: Sequence[AssistantTurn]) -> None:
        self._turns: Deque[AssistantTurn] = deque(turns)
        self.requests: List[Dict[str, Any]] = []

    def request(self, messages: List[dict], tools: List[dict]) -> AssistantTurn:
        self.requests.append(
            {"messages": copy.deepcopy(messages), "tools": copy.deepcopy(tools)}
        )
        if not self._turns:
            raise AssertionError("scripted model ran out of turns")
        return self._turns.popleft()


class FlakyModel:
    """Raises retryable ModelRequestError ``failures`` times, then succeeds."""

    def __init__(self, failures: int, success: AssistantTurn) -> None:
        from coding_agent.errors import ModelRequestError

        self._error = ModelRequestError("注入的连接错误", retryable=True)
        self._failures = failures
        self._success = success
        self.requests: List[Dict[str, Any]] = []

    def request(self, messages: List[dict], tools: List[dict]) -> AssistantTurn:
        self.requests.append(
            {"messages": copy.deepcopy(messages), "tools": copy.deepcopy(tools)}
        )
        if self._failures > 0:
            self._failures -= 1
            raise self._error
        return self._success


class AlwaysFailModel:
    def __init__(self, retryable: bool = True) -> None:
        from coding_agent.errors import ModelRequestError

        self._error = ModelRequestError("注入的模型错误", retryable=retryable)
        self.requests: List[Dict[str, Any]] = []

    def request(self, messages: List[dict], tools: List[dict]) -> AssistantTurn:
        self.requests.append(
            {"messages": copy.deepcopy(messages), "tools": copy.deepcopy(tools)}
        )
        raise self._error


class RecordingSink:
    def __init__(self) -> None:
        self.events: List[AgentEvent] = []

    def emit(self, event: AgentEvent) -> None:
        self.events.append(event)

    def types(self) -> List[str]:
        return [event.type for event in self.events]


def assert_valid_event_stream(
    events: Sequence[AgentEvent], expected_first: str = "run_started"
) -> None:
    assert events, "expected at least one event"
    assert events[0].type == expected_first
    assert [event.type for event in events].count("run_finished") == 1
    assert events[-1].type == "run_finished"
    sequences = [event.sequence for event in events]
    assert sequences == sorted(sequences)
    assert len(set(sequences)) == len(sequences)
    assert all(event.run_id == events[0].run_id for event in events)


def history_is_paired(history) -> bool:
    """Each tool call id has exactly as many results as calls with it."""
    from collections import Counter

    calls: Counter = Counter()
    results: Counter = Counter()
    for message in history:
        if isinstance(message, AssistantMessage):
            for call in message.tool_calls:
                calls[call.id] += 1
        elif isinstance(message, ToolMessage):
            results[message.tool_call_id] += 1
    return calls and calls == results


def build_loop(
    tmp_path,
    model,
    *,
    sink: Optional[RecordingSink] = None,
    policy: Optional[ToolPolicy] = None,
    cancelled: Optional[Callable[[], bool]] = None,
    max_steps: int = 20,
    budget: int = 120_000,
    failure_round_limit: int = 3,
    sleeper: Callable[[float], None] = lambda _seconds: None,
) -> Tuple[AgentLoop, RecordingSink, Any]:
    tracker = FileObservationTracker()
    registry = build_default_tools(Workspace(tmp_path), tracker, cancelled)
    executor = ToolExecutor(registry, policy or WorkspaceToolPolicy(), cancelled)
    resolved_sink = sink or RecordingSink()
    loop = AgentLoop(
        model_client=model,
        tool_registry=registry,
        tool_executor=executor,
        context_manager=ContextManager(budget),
        completion_policy=CompletionPolicy(),
        event_sink=resolved_sink,
        run_id="run-test",
        max_steps=max_steps,
        tool_failure_round_limit=failure_round_limit,
        sleeper=sleeper,
        is_cancelled=cancelled,
    )
    return loop, resolved_sink, model


@pytest.fixture
def workspace(tmp_path):
    return tmp_path
