"""Stable domain types for Conversation/Turn/Run and canonical history.

The domain is deliberately provider-neutral and contains no SDK or web DTO
types. It is shared by the SQLite repository, ConversationService, runtime
registry and canonical journal.

State machine:
    conversation: active -> archived -> active; delete is a hard 404 after.
    turn: pending -> starting -> running -> success | error | interrupted
          or rejected before starting.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional

from ..models import CanonicalMessage


class ConversationState(str, Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"
    DELETED = "deleted"


class TurnState(str, Enum):
    PENDING = "pending"
    STARTING = "starting"
    RUNNING = "running"
    SUCCESS = "success"
    ERROR = "error"
    INTERRUPTED = "interrupted"
    REJECTED = "rejected"


class CanonicalGroupState(str, Enum):
    PENDING = "pending"
    COMMITTED = "committed"
    ABANDONED = "abandoned"
    RECOVERED = "recovered"


class ChangeCoverage(str, Enum):
    COMPLETE = "complete"
    CONFIRMED_ONLY = "confirmed_only"
    INCOMPLETE = "incomplete"


class ChangeType(str, Enum):
    CREATED = "created"
    MODIFIED = "modified"
    DELETED = "deleted"
    RENAMED = "renamed"


class ChangeSource(str, Enum):
    TOOL_CONFIRMED = "tool_confirmed"
    COMMAND_DETECTED = "command_detected"
    EXTERNAL_UNKNOWN = "external_unknown"


@dataclass(frozen=True)
class ConversationRecord:
    id: str
    title: str
    title_source: str
    workspace_path: str
    workspace_key: str
    profile_id: Optional[str]
    reasoning_effort: Optional[str]
    state: str
    version: int
    created_at: str
    last_activity_at: str
    archived_at: Optional[str] = None

    @property
    def is_active(self) -> bool:
        return self.state == ConversationState.ACTIVE.value


@dataclass(frozen=True)
class TurnRecord:
    id: str
    conversation_id: str
    ordinal: int
    state: str
    run_id: Optional[str]
    user_text: str
    created_at: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    result_json: Optional[str] = None
    error_code: Optional[str] = None

    @property
    def is_active(self) -> bool:
        return self.state in {
            TurnState.PENDING.value,
            TurnState.STARTING.value,
            TurnState.RUNNING.value,
        }

    @property
    def is_terminal(self) -> bool:
        return not self.is_active and self.state != TurnState.REJECTED.value


@dataclass(frozen=True)
class CanonicalGroupRecord:
    id: str
    conversation_id: str
    turn_id: str
    group_seq: int
    kind: str
    state: str
    created_at: str
    committed_at: Optional[str] = None


@dataclass(frozen=True)
class PublicEventRecord:
    id: str
    conversation_id: str
    turn_id: str
    run_id: str
    event_seq: int
    kind: str
    payload_json: str
    created_at: str


@dataclass(frozen=True)
class TurnResult:
    """Parsed terminal data stored on a TurnRecord."""

    status: str
    stop_reason: str
    final_text: Optional[str] = None
    verification_status: Optional[str] = None
    step_count: int = 0
    provider_attempt_count: int = 0
    tool_call_count: int = 0
    mutated_paths: tuple[str, ...] = ()
    details: Dict[str, Any] = field(default_factory=dict)


def canonical_message_to_payload(message: CanonicalMessage) -> Dict[str, Any]:
    """Serialize a canonical message to a typed JSON payload.

    The payload shape is private to this project and versioned by the schema
    migration; reading it back uses :func:`payload_to_canonical_message`.
    """
    from ..models import AssistantMessage, SystemMessage, ToolMessage, UserMessage

    if isinstance(message, SystemMessage):
        return {"type": "system", "content": message.content}
    if isinstance(message, UserMessage):
        return {
            "type": "user",
            "content": message.content,
            "source": message.source,
            "attachments": [
                {
                    "id": item.id,
                    "filename": item.filename,
                    "media_type": item.media_type,
                    "kind": item.kind,
                    "size_bytes": item.size_bytes,
                    "sha256": item.sha256,
                }
                for item in message.attachments
            ],
        }
    if isinstance(message, AssistantMessage):
        return {
            "type": "assistant",
            "text": message.text,
            "reasoning": message.reasoning,
            "continuations": [
                {
                    "wire_api": item.wire_api,
                    "item_id": item.item_id,
                    "encrypted_content": item.encrypted_content,
                    "summary": list(item.summary),
                }
                for item in message.continuations
            ],
            "tool_calls": [
                {
                    "id": call.id,
                    "name": call.name,
                    "arguments_raw": call.arguments_raw,
                }
                for call in message.tool_calls
            ],
        }
    if isinstance(message, ToolMessage):
        return {
            "type": "tool",
            "tool_call_id": message.tool_call_id,
            "content": message.content,
            "tool_name": message.tool_name,
            "ok": message.ok,
            "resource_key": message.resource_key,
            "is_read_success": message.is_read_success,
            "file_path": message.file_path,
        }
    raise TypeError(f"unknown canonical message type: {type(message)!r}")


def payload_to_canonical_message(payload: Dict[str, Any]) -> CanonicalMessage:
    """Deserialize an internal canonical payload with fail-closed validation."""

    from ..models import (
        AssistantMessage,
        AttachmentRef,
        ProviderContinuation,
        SystemMessage,
        ToolCall,
        ToolMessage,
        UserMessage,
    )

    message_type = payload.get("type")
    if message_type == "system":
        content = payload.get("content")
        if not isinstance(content, str):
            raise ValueError("system payload missing content")
        return SystemMessage(content=content)
    if message_type == "user":
        content = payload.get("content")
        source = payload.get("source", "user")
        if not isinstance(content, str) or not isinstance(source, str):
            raise ValueError("user payload missing content/source")
        raw_attachments = payload.get("attachments", [])
        if not isinstance(raw_attachments, list):
            raise ValueError("user attachments invalid")
        attachments = []
        for item in raw_attachments:
            if not isinstance(item, dict):
                raise ValueError("user attachment item invalid")
            try:
                attachment = AttachmentRef(
                    id=str(item["id"]),
                    filename=str(item["filename"]),
                    media_type=str(item["media_type"]),
                    kind=str(item["kind"]),
                    size_bytes=int(item["size_bytes"]),
                    sha256=str(item["sha256"]),
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError("user attachment item invalid") from exc
            if (
                not attachment.id
                or not attachment.filename
                or attachment.kind not in {"image", "file"}
                or attachment.size_bytes < 0
                or len(attachment.sha256) != 64
            ):
                raise ValueError("user attachment item invalid")
            attachments.append(attachment)
        return UserMessage(
            content=content, source=source, attachments=tuple(attachments)
        )
    if message_type == "assistant":
        text = payload.get("text", "")
        raw_calls = payload.get("tool_calls", [])
        if not isinstance(text, str) or not isinstance(raw_calls, list):
            raise ValueError("assistant payload invalid")
        calls = tuple(
            ToolCall(
                id=str(item["id"]),
                name=str(item["name"]),
                arguments_raw=str(item["arguments_raw"]),
            )
            for item in raw_calls
        )
        reasoning = payload.get("reasoning")
        raw_continuations = payload.get("continuations", [])
        if not isinstance(raw_continuations, list):
            raise ValueError("assistant continuations invalid")
        continuation_items = []
        for item in raw_continuations:
            if not isinstance(item, dict):
                raise ValueError("assistant continuation item invalid")
            wire_api = item.get("wire_api")
            item_id = item.get("item_id")
            encrypted = item.get("encrypted_content")
            summary = item.get("summary", [])
            if (
                not isinstance(wire_api, str)
                or not isinstance(item_id, str)
                or not item_id
                or not isinstance(encrypted, str)
                or not encrypted
                or not isinstance(summary, list)
                or not all(isinstance(text, str) for text in summary)
            ):
                raise ValueError("assistant continuation item invalid")
            continuation_items.append(
                ProviderContinuation(
                    wire_api=wire_api,
                    item_id=item_id,
                    encrypted_content=encrypted,
                    summary=tuple(summary),
                )
            )
        continuations = tuple(continuation_items)
        return AssistantMessage(
            text=text,
            tool_calls=calls,
            reasoning=reasoning if isinstance(reasoning, str) else None,
            continuations=continuations,
        )
    if message_type == "tool":
        return ToolMessage(
            tool_call_id=str(payload["tool_call_id"]),
            content=str(payload["content"]),
            tool_name=str(payload["tool_name"]),
            ok=bool(payload["ok"]),
            resource_key=str(payload.get("resource_key", "")),
            is_read_success=bool(payload.get("is_read_success", False)),
            file_path=(
                str(payload["file_path"])
                if payload.get("file_path") is not None
                else None
            ),
        )
    raise ValueError(f"unknown canonical payload type: {message_type!r}")


def parse_turn_result(result_json: Optional[str]) -> Optional[TurnResult]:
    if not result_json:
        return None
    import json as _json

    try:
        data = _json.loads(result_json)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return TurnResult(
        status=str(data.get("status", "")),
        stop_reason=str(data.get("stop_reason", "")),
        final_text=data.get("final_text"),
        verification_status=data.get("verification_status"),
        step_count=int(data.get("step_count", 0)),
        provider_attempt_count=int(data.get("provider_attempt_count", 0)),
        tool_call_count=int(data.get("tool_call_count", 0)),
        mutated_paths=tuple(data.get("mutated_paths", ())),
        details=dict(data.get("details", {}) or {}),
    )


def title_from_user_text(text: str, limit: int = 40) -> str:
    """Deterministic default title from the first user message.

    Per task_004: strip whitespace, collapse newlines, truncate by Unicode
    code point, fall back to '新会话'.
    """
    normalized = " ".join(" ".join(text.split()).split())
    if not normalized:
        return "新会话"
    return normalized[:limit]
