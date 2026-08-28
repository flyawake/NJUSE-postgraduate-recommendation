"""Append-only canonical history and resource-aware request projection.

The canonical history is never rewritten: ContextManager builds a detached
request view each step and may compact old successful tool-result bodies
deterministically. Protected content includes the system prompt, the original
user task, every tool-call/result protocol skeleton, the latest two logical
steps, error results and each file's latest successful read_file window.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from .errors import ContextOverflowError
from .models import (
    AssistantMessage,
    CanonicalMessage,
    SystemMessage,
    ToolMessage,
    UserMessage,
)

DEFAULT_CHAR_BUDGET = 120_000
RECENT_STEPS_TO_KEEP = 2

_SOURCE_PREFIXES = {
    "completion_policy": "[completion-policy] ",
    "loop_guard": "[loop-guard] ",
}


class CanonicalHistory:
    """Append-only message history.

    There is deliberately no delete/update API; the only way to add derived
    content for the model is ContextManager's temporary request view.
    """

    def __init__(self) -> None:
        self._messages: List[CanonicalMessage] = []

    def append(self, message: CanonicalMessage) -> None:
        self._messages.append(message)

    @property
    def messages(self) -> Tuple[CanonicalMessage, ...]:
        return tuple(self._messages)

    def __len__(self) -> int:
        return len(self._messages)


@dataclass(frozen=True)
class RequestView:
    messages: Tuple[Dict[str, Any], ...]
    char_count: int
    compacted_results: int = 0
    omitted_chars: int = 0


def to_provider_message(message: CanonicalMessage) -> Dict[str, Any]:
    if isinstance(message, SystemMessage):
        return {"role": "system", "content": message.content}
    if isinstance(message, UserMessage):
        prefix = _SOURCE_PREFIXES.get(message.source, "")
        return {"role": "user", "content": prefix + message.content}
    if isinstance(message, AssistantMessage):
        base: Dict[str, Any] = {"role": "assistant", "content": message.text or None}
        if message.reasoning is not None:
            # DeepSeek-compatible providers that expose visible reasoning need
            # the original reasoning_content when a tool call continues in the
            # same logical turn. The adapter may strip this for providers that
            # do not declare that capability.
            base["reasoning_content"] = message.reasoning
        if message.continuations:
            base["_provider_continuations"] = [
                {
                    "wire_api": item.wire_api,
                    "item_id": item.item_id,
                    "encrypted_content": item.encrypted_content,
                    "summary": list(item.summary),
                }
                for item in message.continuations
            ]
        if message.tool_calls:
            base["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.name, "arguments": call.arguments_raw},
                }
                for call in message.tool_calls
            ]
        return base
    if isinstance(message, ToolMessage):
        return {
            "role": "tool",
            "tool_call_id": message.tool_call_id,
            "content": message.content,
        }
    raise TypeError(f"unknown canonical message type: {type(message)!r}")


def _message_chars(value: Any) -> int:
    if isinstance(value, str):
        return len(value)
    if isinstance(value, dict):
        return sum(_message_chars(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return sum(_message_chars(item) for item in value)
    if value is None:
        return 0
    return len(str(value))


def _compaction_marker(message: ToolMessage) -> str:
    marker: Dict[str, Any] = {
        "ok": True,
        "omitted": True,
        "tool": message.tool_name,
        "resource": message.resource_key,
        "original_chars": len(message.content),
        "omitted_chars": 0,
    }
    compact = json.dumps(
        marker, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    marker["omitted_chars"] = max(0, len(message.content) - len(compact))
    return json.dumps(marker, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class _Step:
    assistant_index: int
    tool_indices: Tuple[int, ...]


class ContextManager:
    def __init__(self, char_budget: int = DEFAULT_CHAR_BUDGET) -> None:
        if char_budget < 1:
            raise ValueError("char_budget must be positive")
        self.char_budget = char_budget

    def build_request(self, history: CanonicalHistory) -> RequestView:
        messages = list(history.messages)
        steps = self._segment_steps(messages)
        recent_indices: set[int] = set()
        for step in steps[-RECENT_STEPS_TO_KEEP:]:
            recent_indices.add(step.assistant_index)
            recent_indices.update(step.tool_indices)

        latest_read: Dict[str, int] = {}
        for index, message in enumerate(messages):
            if (
                isinstance(message, ToolMessage)
                and message.is_read_success
                and message.file_path is not None
            ):
                latest_read[message.file_path] = index

        replaceable: List[int] = []
        for index, message in enumerate(messages):
            if isinstance(message, ToolMessage):
                latest_for_file = latest_read.get(message.file_path) == index
                if index in recent_indices or not message.ok or latest_for_file:
                    continue
                replaceable.append(index)

        overrides: Dict[int, str] = {}
        rendered = self._render(messages, overrides)
        count = _message_chars(rendered)
        compacted = 0
        for index in replaceable:  # oldest first
            if count <= self.char_budget:
                break
            message = messages[index]
            assert isinstance(message, ToolMessage)
            overrides[index] = _compaction_marker(message)
            rendered = self._render(messages, overrides)
            count = _message_chars(rendered)
            compacted += 1
        if count > self.char_budget:
            raise ContextOverflowError(count, self.char_budget)

        omitted = sum(
            max(0, len(messages[index].content) - len(overrides[index]))
            for index in overrides
        )
        return RequestView(
            messages=tuple(rendered),
            char_count=count,
            compacted_results=compacted,
            omitted_chars=omitted,
        )

    @staticmethod
    def _segment_steps(messages: List[CanonicalMessage]) -> List[_Step]:
        steps: List[_Step] = []
        current: Optional[Tuple[int, List[int]]] = None
        for index, message in enumerate(messages):
            if isinstance(message, AssistantMessage) and message.tool_calls:
                current = (index, [])
                steps.append(_Step(index, ()))
                continue
            if isinstance(message, ToolMessage) and current is not None:
                assistant_index, tool_indices = current
                tool_indices.append(index)
                steps[-1] = _Step(assistant_index, tuple(tool_indices))
                continue
            current = None
        return steps

    @staticmethod
    def _render(
        messages: List[CanonicalMessage], overrides: Dict[int, str]
    ) -> List[Dict[str, Any]]:
        rendered: List[Dict[str, Any]] = []
        for index, message in enumerate(messages):
            if index in overrides and isinstance(message, ToolMessage):
                rendered.append(
                    {
                        "role": "tool",
                        "tool_call_id": message.tool_call_id,
                        "content": overrides[index],
                    }
                )
            else:
                rendered.append(to_provider_message(message))
        return rendered
