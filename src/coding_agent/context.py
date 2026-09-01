"""Append-only canonical history and resource-aware request projection.

The canonical history is never rewritten: ContextManager builds a detached
request view each step and may compact old successful tool-result bodies
deterministically. Protected content includes the system prompt, the original
user task, every tool-call/result protocol skeleton, the latest two logical
steps, error results and each file's latest successful read_file window.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from .errors import ContextOverflowError
from .models import (
    AssistantMessage,
    AttachmentRef,
    CanonicalMessage,
    SystemMessage,
    ToolMessage,
    UserMessage,
)

DEFAULT_CHAR_BUDGET = 258_000
DEFAULT_CONTEXT_WINDOW_TOKENS = 128_000
DEFAULT_CONTEXT_TOKEN_RESERVE = 8_000
DEFAULT_REQUEST_BYTE_BUDGET = 32 * 1024 * 1024
RECENT_STEPS_TO_KEEP = 2
MAX_REASONING_CHARS = 800
MAX_ASSISTANT_TEXT_CHARS = 4_000
MAX_INLINE_ATTACHMENT_CHARS = 50_000

_SOURCE_PREFIXES = {
    "completion_policy": "[completion-policy] ",
    "plan_policy": "[plan-policy] ",
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
class MemoryProjection:
    """Immutable memory context returned by a per-turn provider.

    ``block`` is a pre-rendered, XML-escaped, untrusted-reference block. The
    provider is responsible for retrieving once and recording usage in the
    turn's audit table.
    """

    block: str
    entries: Tuple[Any, ...] = ()
    snapshot_hash: str = ""
    reason: str = ""
    omitted_count: int = 0
    commit_usage: Optional[Callable[[], None]] = None


@dataclass(frozen=True)
class RequestView:
    messages: Tuple[Dict[str, Any], ...]
    char_count: int
    estimated_token_count: int = 0
    request_byte_count: int = 0
    compacted_results: int = 0
    compacted_assistants: int = 0
    omitted_chars: int = 0
    memory_projection: Optional[MemoryProjection] = None


def to_provider_message(
    message: CanonicalMessage,
    *,
    include_attachments: bool = False,
    attachment_loader: Optional[Callable[[AttachmentRef], bytes]] = None,
) -> Dict[str, Any]:
    if isinstance(message, SystemMessage):
        return {"role": "system", "content": message.content}
    if isinstance(message, UserMessage):
        prefix = _SOURCE_PREFIXES.get(message.source, "")
        text = prefix + message.content
        if not message.attachments:
            return {"role": "user", "content": text}
        names = "、".join(item.filename for item in message.attachments)
        if not include_attachments:
            marker = f"[附件已在较早消息中提供：{names}]"
            return {
                "role": "user",
                "content": f"{text}\n{marker}".strip(),
            }
        if attachment_loader is None:
            raise RuntimeError("attachment loader is required")
        content: List[Dict[str, Any]] = []
        annotation = f"附件：{names}"
        content.append(
            {
                "type": "input_text",
                "text": f"{text}\n{annotation}".strip(),
            }
        )
        for item in message.attachments:
            raw = attachment_loader(item)
            if item.media_type.startswith("text/") or item.media_type in {
                "application/json",
                "application/xml",
            }:
                decoded = raw.decode("utf-8", errors="replace")
                truncated = len(decoded) > MAX_INLINE_ATTACHMENT_CHARS
                suffix = "\n…[附件文本已截断]" if truncated else ""
                content.append(
                    {
                        "type": "input_text",
                        "text": (
                            f"\n--- 附件 {item.filename} ---\n"
                            f"{decoded[:MAX_INLINE_ATTACHMENT_CHARS]}{suffix}"
                        ),
                    }
                )
                continue
            encoded = base64.b64encode(raw).decode("ascii")
            data_url = f"data:{item.media_type};base64,{encoded}"
            if item.kind == "image":
                content.append(
                    {
                        "type": "input_image",
                        "image_url": data_url,
                        "detail": "auto",
                    }
                )
            else:
                content.append(
                    {
                        "type": "input_file",
                        "filename": item.filename,
                        "file_data": data_url,
                    }
                )
        return {"role": "user", "content": content}
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
        total = 0
        for key, item in value.items():
            if key in {"file_data", "image_url"} and isinstance(item, str):
                if item.startswith("data:"):
                    continue
            total += _message_chars(item)
        return total
    if isinstance(value, (list, tuple)):
        return sum(_message_chars(item) for item in value)
    if value is None:
        return 0
    return len(str(value))


def _message_bytes(value: Any) -> int:
    """Bounded recursive estimate that includes inline base64 payload bytes."""
    if isinstance(value, str):
        return len(value) if value.isascii() else len(value.encode("utf-8"))
    if isinstance(value, dict):
        return sum(
            _message_bytes(key) + _message_bytes(item) + 2
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return sum(_message_bytes(item) + 1 for item in value)
    if value is None:
        return 4
    return len(str(value).encode("utf-8"))


def _estimated_tokens(value: Any) -> int:
    """Provider-neutral conservative token estimate for projected messages.

    Images are charged a bounded visual-input allowance rather than their
    base64 transport size. Generic input files are conservatively charged by
    their encoded payload, while the separate byte budget always accounts for
    the full HTTP request cost.
    """
    if isinstance(value, str):
        return max(1, (len(value) + 3) // 4)
    if isinstance(value, dict):
        part_type = value.get("type")
        if part_type == "input_image" and isinstance(value.get("image_url"), str):
            metadata = {key: item for key, item in value.items() if key != "image_url"}
            return 4_096 + _estimated_tokens(metadata)
        if part_type == "input_file" and isinstance(value.get("file_data"), str):
            payload = value["file_data"]
            metadata = {key: item for key, item in value.items() if key != "file_data"}
            return max(1, (len(payload) + 3) // 4) + _estimated_tokens(metadata)
        return sum(
            _estimated_tokens(key) + _estimated_tokens(item)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return sum(_estimated_tokens(item) for item in value)
    return 1


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
    def __init__(
        self,
        char_budget: int = DEFAULT_CHAR_BUDGET,
        *,
        memory_provider: Optional[Callable[[], Optional[MemoryProjection]]] = None,
        attachment_loader: Optional[Callable[[AttachmentRef], bytes]] = None,
        context_window_tokens: Optional[int] = None,
        context_token_reserve: int = DEFAULT_CONTEXT_TOKEN_RESERVE,
        request_byte_budget: int = DEFAULT_REQUEST_BYTE_BUDGET,
    ) -> None:
        if char_budget < 1:
            raise ValueError("char_budget must be positive")
        self.char_budget = char_budget
        if (
            context_window_tokens is not None
            and context_window_tokens <= context_token_reserve
        ):
            raise ValueError("context_window_tokens must exceed the reserved tokens")
        if request_byte_budget < 1:
            raise ValueError("request_byte_budget must be positive")
        self.context_window_tokens = context_window_tokens
        self.context_token_reserve = context_token_reserve
        self.token_budget = (
            context_window_tokens - context_token_reserve
            if context_window_tokens is not None
            else None
        )
        self.request_byte_budget = request_byte_budget
        self._memory_provider = memory_provider
        self._attachment_loader = attachment_loader
        self._memory_projection_loaded = False
        self._memory_projection: Optional[MemoryProjection] = None
        self._memory_projection_committed = False

    def _get_memory_projection(self) -> Optional[MemoryProjection]:
        """Retrieve memory once per ContextManager instance (one turn)."""
        if not self._memory_projection_loaded:
            self._memory_projection_loaded = True
            if self._memory_provider is not None:
                self._memory_projection = self._memory_provider()
        return self._memory_projection

    def build_request(self, history: CanonicalHistory) -> RequestView:
        messages = list(history.messages)
        memory_projection = self._get_memory_projection()
        prefix = memory_projection.block if memory_projection else None
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

        overrides: Dict[int, Any] = {}
        rendered = self._render(messages, overrides, prefix=prefix)
        count = _message_chars(rendered)
        compacted_results = 0
        compacted_assistants = 0

        # Phase 1: replaceable old successful tool bodies (oldest first).
        for index in replaceable:
            if count <= self.char_budget:
                break
            message = messages[index]
            assert isinstance(message, ToolMessage)
            overrides[index] = _compaction_marker(message)
            rendered = self._render(messages, overrides, prefix=prefix)
            count = _message_chars(rendered)
            compacted_results += 1

        # Phase 2: memory is lower priority than every protected canonical
        # item.  If the escaped projection does not fit, omit it as a whole
        # rather than squeezing out root policy, the current user request,
        # protocol skeletons, errors, or latest file observations.
        if count > self.char_budget and prefix:
            rendered_without_memory = self._render(messages, overrides)
            count_without_memory = _message_chars(rendered_without_memory)
            if count_without_memory <= self.char_budget:
                rendered = rendered_without_memory
                count = count_without_memory
                memory_projection = None

        # Phase 3: automatically compact old assistant reasoning/text so a
        # long conversation can continue instead of failing at the request
        # boundary.  Recent steps are kept intact; the canonical history is
        # still never mutated.
        if count > self.char_budget:
            candidates: List[Tuple[int, AssistantMessage]] = []
            for index, message in enumerate(messages):
                if (
                    isinstance(message, AssistantMessage)
                    and index not in recent_indices
                ):
                    candidates.append((index, message))

            def saving(index: int, message: AssistantMessage) -> int:
                compact = self._compact_assistant(message)
                if compact is None:
                    return 0
                original = _message_chars(to_provider_message(message))
                replaced = _message_chars(compact)
                return max(0, original - replaced)

            candidates.sort(key=lambda pair: (-saving(pair[0], pair[1]), pair[0]))
            for index, message in candidates:
                if count <= self.char_budget:
                    break
                compact = self._compact_assistant(message)
                if compact is None:
                    continue
                if _message_chars(to_provider_message(message)) <= _message_chars(
                    compact
                ):
                    continue
                overrides[index] = compact
                rendered = self._render(messages, overrides, prefix=prefix)
                count = _message_chars(rendered)
                compacted_assistants += 1

        if count > self.char_budget:
            raise ContextOverflowError(count, self.char_budget)

        request_bytes = _message_bytes(rendered)
        if request_bytes > self.request_byte_budget:
            raise ContextOverflowError(
                request_bytes, self.request_byte_budget, metric="request_bytes"
            )
        estimated_tokens = _estimated_tokens(rendered)
        if self.token_budget is not None and estimated_tokens > self.token_budget:
            raise ContextOverflowError(
                estimated_tokens, self.token_budget, metric="estimated_tokens"
            )

        if (
            memory_projection is not None
            and memory_projection.block
            and not self._memory_projection_committed
            and memory_projection.commit_usage is not None
        ):
            memory_projection.commit_usage()
            self._memory_projection_committed = True

        omitted = 0
        for index, override in overrides.items():
            message = messages[index]
            if isinstance(message, ToolMessage):
                omitted += max(0, len(message.content) - len(str(override)))
            elif isinstance(message, AssistantMessage):
                original = _message_chars(to_provider_message(message))
                replaced = _message_chars(override)
                omitted += max(0, original - replaced)
        return RequestView(
            messages=tuple(rendered),
            char_count=count,
            estimated_token_count=estimated_tokens,
            request_byte_count=request_bytes,
            compacted_results=compacted_results,
            compacted_assistants=compacted_assistants,
            omitted_chars=omitted,
            memory_projection=memory_projection,
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
    def _compact_assistant(
        message: AssistantMessage,
    ) -> Optional[Dict[str, Any]]:
        """Return a bounded provider message for an old assistant turn.

        The canonical message is not mutated: this only builds a detached,
        truncated request-view equivalent.  Tool calls and provider
        continuations are preserved verbatim because they are protocol data.
        """
        base = to_provider_message(message)
        changed = False
        if message.reasoning and len(message.reasoning) > MAX_REASONING_CHARS:
            base["reasoning_content"] = (
                message.reasoning[:MAX_REASONING_CHARS]
                + "\n…[reasoning truncated by context manager]"
            )
            changed = True
        if len(message.text or "") > MAX_ASSISTANT_TEXT_CHARS:
            base["content"] = (message.text or "")[
                :MAX_ASSISTANT_TEXT_CHARS
            ] + "\n…[text truncated by context manager]"
            changed = True
        return base if changed else None

    def _render(
        self,
        messages: List[CanonicalMessage],
        overrides: Dict[int, Any],
        *,
        prefix: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        rendered: List[Dict[str, Any]] = []
        if prefix:
            # Memory is an untrusted, non-instruction reference block at the
            # lowest-priority position before the canonical system prompt.
            rendered.append({"role": "system", "content": prefix})
        latest_attachment_index = max(
            (
                index
                for index, message in enumerate(messages)
                if isinstance(message, UserMessage) and message.attachments
            ),
            default=-1,
        )
        for index, message in enumerate(messages):
            if index in overrides:
                value = overrides[index]
                if isinstance(message, ToolMessage):
                    rendered.append(
                        {
                            "role": "tool",
                            "tool_call_id": message.tool_call_id,
                            "content": str(value),
                        }
                    )
                    continue
                if isinstance(message, AssistantMessage) and isinstance(value, dict):
                    rendered.append(value)
                    continue
            rendered.append(
                to_provider_message(
                    message,
                    include_attachments=index == latest_attachment_index,
                    attachment_loader=self._attachment_loader,
                )
            )
        return rendered
