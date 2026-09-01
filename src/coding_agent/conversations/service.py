"""ConversationService: lifecycle orchestration over SQLite + runtime registry.

This service is the task_004 backend composition root for the web layer. It
does not implement AgentLoop/tool semantics; it resolves connections, creates
turns, injects the canonical journal and change collector, owns the work
registry and persists canonical/public facts.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from ..agent import DEFAULT_HARD_MAX_STEPS, AgentLoop
from ..artifacts.store import ArtifactStore
from ..attachments import (
    MAX_ATTACHMENTS_PER_TURN,
    MAX_ATTACHMENTS_TOTAL_BYTES,
    AttachmentStore,
    AttachmentValidationError,
    validate_attachment,
)
from ..changes.collector import ToolChangeCollector
from ..changes.diff import MAX_DIFF_LINES, MAX_FILE_BYTES, build_diff
from ..checkpoints import (
    CheckpointError,
    WorkspaceCheckpointRestorer,
    summarize_actions,
)
from ..completion import CompletionPolicy
from ..config import (
    DEFAULT_CHAR_BUDGET,
    DEFAULT_MAX_STEPS,
    ResolvedModelConnection,
    resolve_connection,
)
from ..context import CanonicalHistory, ContextManager
from ..credentials import CredentialError, CredentialService
from ..errors import ConfigError
from ..memory.extractor import MemoryCandidateExtractor
from ..memory.service import MemoryService, MemoryServiceError
from ..model_client import ModelClient, ModelClientFactory
from ..models import (
    AgentEvent,
    AttachmentRef,
    RunResult,
    RunStatus,
    SystemMessage,
    UserMessage,
)
from ..planning import PlanLedger, PlanSnapshot
from ..prompt import SYSTEM_PROMPT
from ..provider_config import ProfileError, ProfileStore, ProviderProfile, default_home
from ..public_redaction import redact_public_run_result
from ..streaming import ModelRequestOptions
from ..tools import build_default_tools
from ..tools.approval import PermissionBroker
from ..tools.executor import ToolExecutor
from ..tools.observation import FileObservationTracker
from ..tools.paths import Workspace, resolve_inside
from ..tools.policy import InteractiveWorkspaceToolPolicy
from .domain import (
    ConversationRecord,
    ConversationState,
    TurnRecord,
    TurnState,
    title_from_user_text,
)
from .inbox import InboxPort
from .journal import CanonicalJournal
from .runtime import RuntimeRegistry, RuntimeRegistryError
from .store import SQLiteConversationRepository

logger = logging.getLogger("coding_agent.conversations.service")


class ConversationServiceError(Exception):
    def __init__(self, code: str, message: str, *, field: Optional[str] = None):
        super().__init__(message)
        self.code = code
        self.field = field


def _canonical_workspace_key(path: Path) -> str:
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        resolved = path.absolute()
    return os.path.normcase(str(resolved))


def _memory_cursor_signature(
    scope_type: Optional[str],
    scope_key: Optional[str],
    status: Optional[str],
    query: Optional[str],
) -> str:
    payload = json.dumps(
        [scope_type, scope_key, status, query or ""],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _encode_memory_cursor(offset: int, snapshot: str, signature: str) -> str:
    raw = json.dumps([offset, snapshot, signature], separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def _decode_memory_cursor(cursor: str, signature: str) -> Tuple[int, str]:
    try:
        data = json.loads(base64.urlsafe_b64decode(cursor.encode("ascii")))
        if (
            not isinstance(data, list)
            or len(data) != 3
            or not isinstance(data[0], int)
            or data[0] < 0
            or not isinstance(data[1], str)
            or data[2] != signature
        ):
            raise ValueError
        return data[0], data[1]
    except Exception as exc:
        raise ConversationServiceError(
            "invalid_cursor", "memory 分页游标无效", field="cursor"
        ) from exc


class _PersistEventSink:
    """Buffered public-event sink for stream delta checkpointing.

    The worker's AgentLoop may emit thousands of small text/reasoning deltas.
    Instead of one SQLite commit per chunk, this sink coalesces events in
    memory and flushes a bounded batch at a fixed event/character threshold,
    always flushing the terminal ``run_finished`` event immediately.
    """

    _MAX_BUFFERED_EVENTS = 100
    _MAX_BUFFERED_CHARS = 16_384

    def __init__(
        self,
        repository: SQLiteConversationRepository,
        conversation_id: str,
        turn_id: str,
        run_id: str,
    ) -> None:
        self._repo = repository
        self._cid = conversation_id
        self._tid = turn_id
        self._run_id = run_id
        self._seq = 0
        self._pending: List[Dict[str, Any]] = []
        self._delta_entries: Dict[Tuple[Any, ...], Dict[str, Any]] = {}
        self._chars = 0
        self._checkpoints: Dict[Tuple[int, str], List[str]] = {}
        self._checkpoint_seqs: Dict[Tuple[int, str], int] = {}
        self._last_flush = time.monotonic()
        self._flush_interval = 0.25

    def emit(self, event: AgentEvent) -> None:
        self._seq += 1
        payload = event.to_dict()
        delta = event.payload.get("delta")
        attempt = event.payload.get("attempt")
        is_delta = event.type in {
            "assistant_text_delta",
            "reasoning_delta",
            "reasoning_summary_delta",
        } and isinstance(delta, str)
        delta_key = (
            event.type,
            attempt,
            event.step,
            event.payload.get("summary_index"),
            event.payload.get("visibility"),
        )
        current = self._delta_entries.get(delta_key) if is_delta else None
        if current is not None:
            current["event_seq"] = self._seq
            current["payload"]["sequence"] = event.sequence
            current["delta_parts"].append(delta)
        else:
            entry: Dict[str, Any] = {
                "conversation_id": self._cid,
                "turn_id": self._tid,
                "run_id": self._run_id,
                "event_seq": self._seq,
                "kind": event.type,
                "payload": payload,
                "attempt": attempt,
                "step": event.step,
            }
            if is_delta:
                entry["delta_parts"] = [delta]
                self._delta_entries[delta_key] = entry
            else:
                # A lifecycle/tool event is an ordering barrier. Deltas after
                # it must not merge backward across the state transition.
                self._delta_entries = {}
            self._pending.append(entry)
        self._chars += _event_size(event.type, payload)
        self._update_checkpoint(event)
        if (
            event.type in {"plan_updated", "run_finished"}
            or len(self._pending) >= self._MAX_BUFFERED_EVENTS
            or self._chars >= self._MAX_BUFFERED_CHARS
            or time.monotonic() - self._last_flush >= self._flush_interval
        ):
            self.flush()

    def _update_checkpoint(self, event: AgentEvent) -> None:
        attempt = event.payload.get("attempt")
        if not isinstance(attempt, int):
            return
        delta = event.payload.get("delta")
        if not isinstance(delta, str) or not delta:
            return
        if event.type == "assistant_text_delta":
            channel = "text"
        elif event.type == "reasoning_delta":
            channel = "reasoning"
        elif event.type == "reasoning_summary_delta":
            channel = "summary"
        else:
            return
        key = (attempt, channel)
        self._checkpoints.setdefault(key, []).append(delta)
        self._checkpoint_seqs[key] = self._seq

    def flush(self) -> None:
        if not self._pending and not self._checkpoints:
            return
        pending = self._pending
        for entry in pending:
            parts = entry.pop("delta_parts", None)
            entry.pop("attempt", None)
            entry.pop("step", None)
            if parts is not None:
                entry["payload"]["payload"]["delta"] = "".join(parts)
        checkpoint_entries = [
            {
                "conversation_id": self._cid,
                "turn_id": self._tid,
                "run_id": self._run_id,
                "attempt": attempt,
                "channel": channel,
                "text": "".join(parts),
                "event_seq": self._checkpoint_seqs[(attempt, channel)],
            }
            for (attempt, channel), parts in self._checkpoints.items()
        ]
        self._pending = []
        self._delta_entries = {}
        self._checkpoints = {}
        self._checkpoint_seqs = {}
        self._chars = 0
        self._last_flush = time.monotonic()
        if pending:
            self._repo.append_public_events_batch(pending)
        if checkpoint_entries:
            self._repo.upsert_stream_checkpoints_batch(checkpoint_entries)


def _event_size(kind: str, payload: Dict[str, Any]) -> int:
    return (
        16
        + len(kind)
        + sum(len(str(key)) + len(str(value)) for key, value in payload.items())
    )


class ConversationService:
    def __init__(
        self,
        *,
        home: Optional[Path] = None,
        env: Optional[Mapping[str, str]] = None,
        max_workers: int = 2,
        client_factory: Callable[
            [ResolvedModelConnection], ModelClient
        ] = ModelClientFactory.create,
        loop_builder: Optional[Callable[..., AgentLoop]] = None,
        repository: Optional[SQLiteConversationRepository] = None,
    ) -> None:
        resolved_home = Path(home) if home is not None else default_home()
        resolved_home.mkdir(parents=True, exist_ok=True)
        self._home = resolved_home
        self._env: Dict[str, str] = dict(env) if env is not None else dict(os.environ)
        self.profile_store = ProfileStore(resolved_home)
        self.credentials = CredentialService(resolved_home, self._env)
        self._client_factory = client_factory
        self._loop_builder = loop_builder
        self._repository = repository or SQLiteConversationRepository(
            resolved_home / "state.db"
        )
        self._repository.initialize()
        self.permissions = PermissionBroker()
        self._memory = MemoryService(self._repository)
        self._recover_active_turns = self._repository.recover_active_turns()
        for turn in self._recover_active_turns:
            self._repository.recover_pending_groups_for_turn(
                turn.conversation_id, turn.id
            )
        self._repository.recover_claimed_steers()
        self.runtime = RuntimeRegistry(max_workers=max_workers)
        self._artifact_store = ArtifactStore(resolved_home / "artifacts")
        self._attachment_store = AttachmentStore(resolved_home / "attachments")
        self._restore_locks: Dict[str, threading.Lock] = {}
        self._restore_locks_lock = threading.RLock()
        self._recover_restore_operations()
        # A crash can occur after an atomic CAS rename but before the DB ref
        # transaction, or after DB GC but before physical unlink. Startup
        # reconciliation is idempotent and never removes a referenced blob.
        referenced_blobs = self._repository.list_artifact_blob_ids()
        for blob_id in self._artifact_store.list_digests() - referenced_blobs:
            self._artifact_store.delete(blob_id)
        referenced_attachments = self._repository.list_attachment_blob_ids()
        for blob_id in self._attachment_store.list_digests() - referenced_attachments:
            self._attachment_store.delete(blob_id)
        self._shutdown_lock = threading.RLock()
        self._queue_consumer_locks: Dict[str, threading.Lock] = {}
        self._queue_consumer_locks_lock = threading.RLock()

    # ------------------------------------------------------------ read APIs

    def list_conversations(
        self,
        *,
        archived: Optional[bool] = False,
        query: Optional[str] = None,
        limit: int = 50,
        cursor: Optional[str] = None,
    ) -> Dict[str, Any]:
        try:
            records, next_cursor = self._repository.list_conversations(
                archived=archived, query=query, limit=limit, cursor=cursor
            )
        except ValueError as exc:
            code = "invalid_cursor" if str(exc) == "invalid_cursor" else "invalid_limit"
            raise ConversationServiceError(code, "会话分页参数无效") from exc
        return {
            "items": [
                {
                    **self._conversation_to_dict(record),
                    "latest_turn": (
                        self._turn_to_dict(latest)
                        if (latest := self._repository.get_latest_turn(record.id))
                        else None
                    ),
                }
                for record in records
            ],
            "next_cursor": next_cursor,
        }

    def create_conversation(
        self,
        *,
        workspace_path: str,
        profile_id: Optional[str],
        title: Optional[str] = None,
    ) -> Dict[str, Any]:
        try:
            path = Path(workspace_path).expanduser().resolve(strict=True)
            if not path.is_dir():
                raise ConversationServiceError(
                    "invalid_workspace", "工作区不存在或不是目录", field="workspace"
                )
        except (OSError, RuntimeError) as exc:
            raise ConversationServiceError(
                "invalid_workspace", "工作区不存在或不可访问", field="workspace"
            ) from exc
        workspace_key = _canonical_workspace_key(path)
        record = self._repository.create_conversation(
            workspace_path=str(path),
            workspace_key=workspace_key,
            profile_id=profile_id,
            title=(title or "新会话").strip() or "新会话",
            title_source="manual" if title and title.strip() else "auto",
        )
        return self._conversation_to_dict(record)

    def get_conversation(self, conversation_id: str) -> Dict[str, Any]:
        record = self._require_conversation(conversation_id)
        return self._conversation_to_dict(record)

    # ------------------------------------------------------------ memory

    def _validated_memory_scope(
        self, scope_type: str, scope_key: str
    ) -> Tuple[str, str]:
        if scope_type == "global":
            if scope_key != "global":
                raise ConversationServiceError(
                    "invalid_scope_key", "global 作用域的 scope_key 必须为 global"
                )
            return scope_type, "global"
        if scope_type == "conversation":
            self._require_conversation(scope_key)
            return scope_type, scope_key
        if scope_type == "workspace":
            try:
                path = Path(scope_key).expanduser().resolve(strict=True)
                if not path.is_dir():
                    raise OSError("not_directory")
            except (OSError, RuntimeError, ValueError) as exc:
                raise ConversationServiceError(
                    "invalid_scope_key",
                    "workspace 作用域必须指向现有工作区目录",
                    field="scope_key",
                ) from exc
            return scope_type, _canonical_workspace_key(path)
        raise ConversationServiceError(
            "invalid_scope", "scope_type 必须是 global/workspace/conversation"
        )

    def list_memories(
        self,
        *,
        scope_type: Optional[str] = None,
        scope_key: Optional[str] = None,
        status: Optional[str] = None,
        query: Optional[str] = None,
        limit: int = 100,
        cursor: Optional[str] = None,
    ) -> Dict[str, Any]:
        if status and status not in {
            "candidate",
            "confirmed",
            "superseded",
            "rejected",
            "deleted",
        }:
            raise ConversationServiceError(
                "invalid_status", "无效的 memory status", field="status"
            )
        if scope_type and scope_key:
            scope_type, scope_key = self._validated_memory_scope(scope_type, scope_key)
        page_limit = max(1, min(limit, 100))
        signature = _memory_cursor_signature(scope_type, scope_key, status, query)
        offset, snapshot = (
            _decode_memory_cursor(cursor, signature) if cursor else (0, "")
        )
        fetch_limit = 1_000
        if query:
            items = self._memory.search(
                query,
                scope_type=scope_type,
                scope_key=scope_key,
                limit=fetch_limit,
                statuses=[status] if status else None,
            )
        else:
            items = self._memory.list(
                scope_type=scope_type,
                scope_key=scope_key,
                status=status,
                limit=fetch_limit,
            )
        if not snapshot:
            snapshot = max(
                (str(item.get("updated_at", "")) for item in items), default=""
            )
        else:
            items = [
                item for item in items if str(item.get("updated_at", "")) <= snapshot
            ]
        page_items = items[offset : offset + page_limit]
        next_offset = offset + len(page_items)
        next_cursor = (
            _encode_memory_cursor(next_offset, snapshot, signature)
            if next_offset < len(items)
            else None
        )
        return {"items": page_items, "next_cursor": next_cursor}

    def create_memory(
        self,
        *,
        scope_type: str,
        scope_key: str,
        kind: str,
        content: str,
        title: Optional[str] = None,
        source_conversation_id: Optional[str] = None,
        source_turn_id: Optional[str] = None,
        source_excerpt: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        try:
            scope_type, scope_key = self._validated_memory_scope(scope_type, scope_key)
            if source_turn_id and not source_conversation_id:
                raise ConversationServiceError(
                    "invalid_source",
                    "source_turn_id 必须与 source_conversation_id 一起提供",
                    field="source_turn_id",
                )
            if source_conversation_id:
                self._require_conversation(source_conversation_id)
                if source_turn_id:
                    self._require_turn(source_conversation_id, source_turn_id)
            return self._memory.create_confirmed_memory(
                scope_type=scope_type,
                scope_key=scope_key,
                kind=kind,
                content=content,
                title=title,
                source_conversation_id=source_conversation_id,
                source_turn_id=source_turn_id,
                source_excerpt=source_excerpt,
                idempotency_key=idempotency_key,
            )
        except MemoryServiceError as exc:
            raise ConversationServiceError(exc.code, str(exc), field=exc.field) from exc
        except ValueError as exc:
            if str(exc) == "idempotency_conflict":
                raise ConversationServiceError(
                    "idempotency_conflict", "幂等键已用于其他 memory 操作"
                ) from exc
            raise

    def get_memory(self, memory_id: str) -> Dict[str, Any]:
        try:
            row = self._memory.get(memory_id)
            if row is None:
                raise MemoryServiceError("memory_not_found", "记忆不存在")
            return row
        except MemoryServiceError as exc:
            raise ConversationServiceError(exc.code, str(exc), field=exc.field) from exc

    def edit_memory(
        self,
        memory_id: str,
        *,
        content: str,
        kind: Optional[str] = None,
        title: Optional[str] = None,
        expected_version: int,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        try:
            return self._memory.edit(
                memory_id,
                content=content,
                kind=kind,
                title=title,
                expected_version=expected_version,
                idempotency_key=idempotency_key,
            )
        except MemoryServiceError as exc:
            raise ConversationServiceError(exc.code, str(exc), field=exc.field) from exc
        except ValueError as exc:
            if str(exc) == "idempotency_conflict":
                raise ConversationServiceError(
                    "idempotency_conflict", "幂等键已用于其他 memory 操作"
                ) from exc
            if str(exc) == "version_conflict":
                raise ConversationServiceError(
                    "version_conflict", "记忆已被其他端修改", field="version"
                ) from exc
            raise ConversationServiceError("memory_not_found", "记忆不存在") from exc

    def delete_memory(
        self,
        memory_id: str,
        *,
        expected_version: int,
        idempotency_key: Optional[str] = None,
    ) -> None:
        try:
            self._memory.delete(
                memory_id,
                expected_version=expected_version,
                idempotency_key=idempotency_key,
            )
        except MemoryServiceError as exc:
            raise ConversationServiceError(exc.code, str(exc), field=exc.field) from exc
        except ValueError as exc:
            if str(exc) == "idempotency_conflict":
                raise ConversationServiceError(
                    "idempotency_conflict", "幂等键已用于其他 memory 操作"
                ) from exc
            if str(exc) == "version_conflict":
                raise ConversationServiceError(
                    "version_conflict", "记忆已被其他端修改", field="version"
                ) from exc
            raise ConversationServiceError("memory_not_found", "记忆不存在") from exc

    def approve_memory(
        self,
        memory_id: str,
        *,
        expected_version: int,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        try:
            return self._memory.approve(
                memory_id,
                expected_version=expected_version,
                idempotency_key=idempotency_key,
            )
        except MemoryServiceError as exc:
            raise ConversationServiceError(exc.code, str(exc), field=exc.field) from exc
        except ValueError as exc:
            if str(exc) == "idempotency_conflict":
                raise ConversationServiceError(
                    "idempotency_conflict", "幂等键已用于其他 memory 操作"
                ) from exc
            if str(exc) == "version_conflict":
                raise ConversationServiceError(
                    "version_conflict", "记忆已被其他端修改", field="version"
                ) from exc
            raise ConversationServiceError("memory_not_found", "记忆不存在") from exc

    def reject_memory(
        self,
        memory_id: str,
        *,
        expected_version: int,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        try:
            return self._memory.reject(
                memory_id,
                expected_version=expected_version,
                idempotency_key=idempotency_key,
            )
        except MemoryServiceError as exc:
            raise ConversationServiceError(exc.code, str(exc), field=exc.field) from exc
        except ValueError as exc:
            if str(exc) == "idempotency_conflict":
                raise ConversationServiceError(
                    "idempotency_conflict", "幂等键已用于其他 memory 操作"
                ) from exc
            if str(exc) == "version_conflict":
                raise ConversationServiceError(
                    "version_conflict", "记忆已被其他端修改", field="version"
                ) from exc
            raise ConversationServiceError("memory_not_found", "记忆不存在") from exc

    def reset_memories(
        self,
        *,
        scope_type: str,
        scope_key: str,
        idempotency_key: Optional[str] = None,
        expected_scope_version: Optional[int] = None,
    ) -> Dict[str, Any]:
        try:
            scope_type, scope_key = self._validated_memory_scope(scope_type, scope_key)
            deleted = self._memory.reset_scope(
                scope_type,
                scope_key,
                idempotency_key=idempotency_key,
                expected_scope_version=expected_scope_version,
            )
        except MemoryServiceError as exc:
            raise ConversationServiceError(exc.code, str(exc), field=exc.field) from exc
        except ValueError as exc:
            if str(exc) == "idempotency_conflict":
                raise ConversationServiceError(
                    "idempotency_conflict", "幂等键已用于其他 memory 操作"
                ) from exc
            if str(exc) == "version_conflict":
                raise ConversationServiceError(
                    "version_conflict",
                    "记忆作用域已被其他端修改",
                    field="expected_scope_version",
                ) from exc
            raise
        return {"scope_type": scope_type, "scope_key": scope_key, "deleted": deleted}

    def turn_memory_usage(self, turn_id: str) -> List[Dict[str, Any]]:
        return self._memory.turn_memory_usage(turn_id)

    def memory_settings(
        self,
        *,
        scope_type: Optional[str] = None,
        scope_key: Optional[str] = None,
        enabled: Optional[bool] = None,
        candidate_enabled: Optional[bool] = None,
    ) -> Dict[str, Any]:
        if candidate_enabled is not None:
            return self._memory.set_candidate_enabled(candidate_enabled)
        if enabled is None:
            if scope_type and scope_key:
                scope_type, scope_key = self._validated_memory_scope(
                    scope_type, scope_key
                )
            return {
                "enabled": self._memory.is_memory_enabled(
                    conversation_id=scope_key if scope_type == "conversation" else None,
                    workspace_key=scope_key if scope_type == "workspace" else None,
                ),
                "candidate_enabled": self._memory.is_candidate_enabled(),
                "scope_version": self._memory.scope_version(
                    scope_type or "global", scope_key or "global"
                ),
            }
        try:
            resolved_type, resolved_key = self._validated_memory_scope(
                scope_type or "global", scope_key or "global"
            )
            result = self._memory.set_memory_enabled(
                scope_type=resolved_type,
                scope_key=resolved_key,
                enabled=enabled,
            )
        except MemoryServiceError as exc:
            raise ConversationServiceError(exc.code, str(exc), field=exc.field) from exc
        result["candidate_enabled"] = self._memory.is_candidate_enabled()
        result["scope_version"] = self._memory.scope_version(
            resolved_type, resolved_key
        )
        return result

    def rename_conversation(
        self, conversation_id: str, *, title: str, expected_version: int
    ) -> Dict[str, Any]:
        cleaned = (title or "").strip()
        if not cleaned:
            raise ConversationServiceError(
                "invalid_title", "标题不能为空", field="title"
            )
        try:
            record = self._repository.rename_conversation(
                conversation_id, title=cleaned, expected_version=expected_version
            )
        except KeyError as exc:
            raise ConversationServiceError(
                "conversation_not_found", "会话不存在"
            ) from exc
        except ValueError as exc:
            raise ConversationServiceError(
                "version_conflict", "会话已被其他端修改", field="version"
            ) from exc
        return self._conversation_to_dict(record)

    def set_conversation_reasoning_effort(
        self, conversation_id: str, reasoning_effort: Optional[str]
    ) -> Dict[str, Any]:
        if reasoning_effort not in (None, "", "low", "medium", "high", "max"):
            raise ConversationServiceError(
                "invalid_reasoning_effort",
                "思考强度必须是 low/medium/high/max、默认或空",
                field="reasoning_effort",
            )
        try:
            record = self._repository.set_conversation_reasoning_effort(
                conversation_id, reasoning_effort
            )
        except KeyError as exc:
            raise ConversationServiceError(
                "conversation_not_found", "会话不存在"
            ) from exc
        return self._conversation_to_dict(record)

    def set_conversation_command_policy(
        self, conversation_id: str, command_policy: str
    ) -> Dict[str, Any]:
        if command_policy not in ("ask", "allow", "deny"):
            raise ConversationServiceError(
                "invalid_command_policy",
                "命令权限必须是 ask/allow/deny",
                field="command_policy",
            )
        try:
            record = self._repository.set_conversation_command_policy(
                conversation_id, command_policy
            )
        except KeyError as exc:
            raise ConversationServiceError(
                "conversation_not_found", "会话不存在"
            ) from exc
        if command_policy != "ask":
            self.permissions.resolve_conversation(
                conversation_id, allow=command_policy == "allow"
            )
        return self._conversation_to_dict(record)

    def archive_conversation(
        self, conversation_id: str, *, expected_version: int
    ) -> Dict[str, Any]:
        return self._set_state(
            conversation_id, ConversationState.ARCHIVED.value, expected_version
        )

    def unarchive_conversation(
        self, conversation_id: str, *, expected_version: int
    ) -> Dict[str, Any]:
        return self._set_state(
            conversation_id, ConversationState.ACTIVE.value, expected_version
        )

    def delete_conversation(
        self, conversation_id: str, *, expected_version: int
    ) -> None:
        self._require_conversation(conversation_id)
        if self.runtime.is_active(conversation_id):
            raise ConversationServiceError(
                "conversation_busy", "会话正在运行，不能删除"
            )
        try:
            attachment_blobs = self._repository.list_conversation_attachment_blob_ids(
                conversation_id
            )
            orphaned = self._repository.delete_conversation(
                conversation_id, expected_version
            )
            for blob_id in orphaned:
                self._artifact_store.delete(blob_id)
            for blob_id in attachment_blobs:
                if not self._repository.attachment_blob_is_referenced(blob_id):
                    self._attachment_store.delete(blob_id)
        except KeyError as exc:
            raise ConversationServiceError(
                "conversation_not_found", "会话不存在"
            ) from exc
        except ValueError as exc:
            if str(exc) == "checkpoint_restore_busy":
                raise ConversationServiceError(
                    "checkpoint_restore_busy", "检查点恢复期间不能删除会话"
                ) from exc
            raise ConversationServiceError(
                "version_conflict", "会话已被其他端修改", field="version"
            ) from exc

    # ------------------------------------------------------- attachments

    def create_attachment(
        self,
        conversation_id: str,
        *,
        filename: str,
        media_type: Optional[str],
        data: bytes,
    ) -> Dict[str, Any]:
        conversation = self._require_conversation(conversation_id)
        if conversation.state != ConversationState.ACTIVE.value:
            raise ConversationServiceError(
                "conversation_archived", "已归档会话不能上传附件"
            )
        try:
            validated = validate_attachment(filename, media_type, data)
        except AttachmentValidationError as exc:
            raise ConversationServiceError(exc.code, exc.message, field="file") from exc
        blob_id = self._attachment_store.put(data)
        try:
            ref = self._repository.create_attachment(
                conversation_id,
                blob_id=blob_id,
                filename=validated.filename,
                media_type=validated.media_type,
                kind=validated.kind,
                size_bytes=len(data),
            )
        except Exception:
            if not self._repository.attachment_blob_is_referenced(blob_id):
                self._attachment_store.delete(blob_id)
            raise
        return self._attachment_to_dict(ref)

    def delete_attachment(self, conversation_id: str, attachment_id: str) -> None:
        self._require_conversation(conversation_id)
        blob_id = self._repository.delete_pending_attachment(
            conversation_id, attachment_id
        )
        if blob_id is None:
            raise ConversationServiceError(
                "attachment_not_found", "附件不存在或已随 turn 发送"
            )
        if blob_id:
            self._attachment_store.delete(blob_id)

    def read_attachment(
        self, conversation_id: str, attachment_id: str
    ) -> Tuple[Dict[str, Any], bytes]:
        self._require_conversation(conversation_id)
        ref = self._repository.get_attachment(conversation_id, attachment_id)
        if ref is None:
            raise ConversationServiceError("attachment_not_found", "附件不存在")
        try:
            data = self._attachment_store.read(ref.sha256)
        except (FileNotFoundError, OSError, ValueError) as exc:
            raise ConversationServiceError(
                "attachment_unavailable", "附件内容不可用"
            ) from exc
        return self._attachment_to_dict(ref), data

    # ------------------------------------------------------------ turns

    def list_turns(
        self,
        conversation_id: str,
        *,
        limit: int = 50,
        cursor: Optional[str] = None,
    ) -> Dict[str, Any]:
        self._require_conversation(conversation_id)
        try:
            records, next_cursor = self._repository.list_turns(
                conversation_id, limit=limit, cursor=cursor
            )
        except ValueError as exc:
            code = "invalid_cursor" if str(exc) == "invalid_cursor" else "invalid_limit"
            raise ConversationServiceError(code, "turn 分页参数无效") from exc
        return {
            "items": [self._turn_to_dict(r) for r in records],
            "next_cursor": next_cursor,
        }

    def get_turn(self, conversation_id: str, turn_id: str) -> Dict[str, Any]:
        record = self._require_turn(conversation_id, turn_id)
        return self._turn_to_dict(record)

    def preview_checkpoint_restore(
        self, conversation_id: str, turn_id: str
    ) -> Dict[str, Any]:
        """Return a compare-before-write restore plan without changing state."""

        conversation = self._require_conversation(conversation_id)
        if conversation.state != ConversationState.ACTIVE.value:
            raise ConversationServiceError(
                "conversation_archived", "已归档会话不能恢复检查点"
            )
        try:
            target, future_turns = self._repository.list_active_turns_after(
                conversation_id, turn_id
            )
        except KeyError as exc:
            raise ConversationServiceError(
                "checkpoint_not_on_active_timeline", "检查点不在当前对话时间线"
            ) from exc
        blockers: List[Dict[str, Any]] = []
        if not target.is_terminal:
            blockers.append(
                {
                    "code": "checkpoint_turn_not_terminal",
                    "message": "只能恢复到已完成的对话轮次",
                }
            )
        if not future_turns:
            blockers.append(
                {
                    "code": "checkpoint_is_current",
                    "message": "当前已经位于该检查点",
                }
            )
        if self.runtime.is_active(conversation_id) or self._repository.get_active_turn(
            conversation_id
        ):
            blockers.append(
                {
                    "code": "conversation_busy",
                    "message": "会话运行中，不能恢复检查点",
                }
            )
        if self._repository.has_pending_inbox_items(conversation_id):
            blockers.append(
                {
                    "code": "checkpoint_inbox_not_empty",
                    "message": "请先处理或删除待发送消息",
                }
            )
        if self._repository.has_other_workspace_activity_after(
            conversation.workspace_key, conversation_id, target.finished_at
        ):
            blockers.append(
                {
                    "code": "checkpoint_workspace_diverged",
                    "message": "同一工作区已被其他会话继续修改",
                }
            )
        if self._repository.has_unfinished_restore_for_workspace(
            conversation.workspace_key
        ):
            blockers.append(
                {
                    "code": "checkpoint_recovery_required",
                    "message": "工作区存在尚未完成恢复的检查点操作",
                }
            )

        change_sets: List[Dict[str, Any]] = []
        for future in future_turns:
            if future.state == TurnState.REJECTED.value:
                continue
            change_set = self._repository.get_change_set(conversation_id, future.id)
            if change_set is None:
                blockers.append(
                    {
                        "code": "checkpoint_change_set_missing",
                        "message": "后续轮次没有完整的文件变更记录",
                        "turn_id": future.id,
                    }
                )
                continue
            change_sets.append(change_set)

        workspace = Path(conversation.workspace_path)
        if not workspace.is_dir():
            blockers.append(
                {
                    "code": "invalid_workspace",
                    "message": "会话工作区已不可用",
                }
            )
            plan = {
                "version": 1,
                "target_turn_id": turn_id,
                "future_turn_ids": [item.id for item in future_turns],
                "steps": [],
                "affected_files": [],
                "blockers": [],
            }
        else:
            try:
                restorer = WorkspaceCheckpointRestorer(workspace, self._artifact_store)
                plan = restorer.build_plan(
                    change_sets,
                    target_turn_id=turn_id,
                    future_turn_ids=[item.id for item in future_turns],
                )
            except (OSError, RuntimeError):
                blockers.append(
                    {
                        "code": "invalid_workspace",
                        "message": "会话工作区无法安全解析",
                    }
                )
                plan = {
                    "version": 1,
                    "target_turn_id": turn_id,
                    "future_turn_ids": [item.id for item in future_turns],
                    "steps": [],
                    "affected_files": [],
                    "blockers": [],
                }
        blockers.extend(plan.get("blockers", []))
        plan["blockers"] = blockers
        counts = summarize_actions(plan)
        return {
            "conversation_id": conversation_id,
            "target_turn_id": turn_id,
            "target_ordinal": target.ordinal,
            "future_turn_count": len(future_turns),
            "file_count": len(plan.get("affected_files", [])),
            "create_count": counts["create"],
            "modify_count": counts["modify"],
            "delete_count": counts["delete"],
            "restorable": not blockers,
            "coverage": "complete" if not blockers else "blocked",
            "affected_files": list(plan.get("affected_files", [])),
            "blockers": blockers,
            "warnings": ["checkpoint_restores_captured_workspace_files_only"],
            "_plan": plan,
        }

    def restore_checkpoint(
        self,
        conversation_id: str,
        turn_id: str,
        *,
        idempotency_key: str,
        confirm: bool,
    ) -> Dict[str, Any]:
        if not confirm:
            raise ConversationServiceError(
                "confirmation_required", "恢复检查点需要显式确认", field="confirm"
            )
        if not idempotency_key or len(idempotency_key) > 128:
            raise ConversationServiceError(
                "invalid_request", "恢复请求缺少有效的幂等键", field="idempotency_key"
            )
        conversation = self._require_conversation(conversation_id)
        with self._restore_locks_lock:
            restore_lock = self._restore_locks.setdefault(
                conversation_id, threading.Lock()
            )
        if not restore_lock.acquire(blocking=False):
            raise ConversationServiceError(
                "checkpoint_restore_busy", "该会话已有恢复操作正在进行"
            )
        owner_id = f"restore:{conversation_id}:{uuid.uuid4().hex}"
        lease_acquired = False
        try:
            prior = self._repository.get_restore_operation_by_idempotency(
                conversation_id, idempotency_key
            )
            if prior is not None:
                if str(prior["target_turn_id"]) != turn_id:
                    raise ConversationServiceError(
                        "idempotency_conflict",
                        "该幂等键已用于另一个检查点恢复请求",
                        field="idempotency_key",
                    )
                if prior["state"] == "completed":
                    return self._restore_result(prior)
                if prior["state"] in {"prepared", "applying"}:
                    raise ConversationServiceError(
                        "checkpoint_restore_busy",
                        "此前的同一恢复请求仍在处理中",
                    )
                if prior["state"] in {"rolled_back", "recovery_required"}:
                    raise ConversationServiceError(
                        "checkpoint_restore_failed",
                        "此前的同一恢复请求未完成，请使用新的请求重试",
                    )
            owner = self.runtime.acquire_workspace_lease(
                conversation.workspace_key, owner_id
            )
            if owner is not None:
                raise ConversationServiceError(
                    "workspace_busy", "同一工作区已有其他操作运行", field="workspace"
                )
            lease_acquired = True
            preview = self.preview_checkpoint_restore(conversation_id, turn_id)
            if not preview["restorable"]:
                first = preview["blockers"][0]
                raise ConversationServiceError(
                    str(first.get("code", "checkpoint_not_restorable")),
                    str(first.get("message", "该检查点当前无法安全恢复")),
                    field=first.get("path"),
                )
            plan = dict(preview.pop("_plan"))
            try:
                operation, created = self._repository.create_restore_operation(
                    operation_id=uuid.uuid4().hex,
                    conversation_id=conversation_id,
                    target_turn_id=turn_id,
                    workspace_key=conversation.workspace_key,
                    plan=plan,
                    idempotency_key=idempotency_key,
                )
            except ValueError as exc:
                code = str(exc)
                if code == "idempotency_conflict":
                    raise ConversationServiceError(
                        code,
                        "该幂等键已用于另一个检查点恢复请求",
                        field="idempotency_key",
                    ) from exc
                if code == "checkpoint_restore_busy":
                    raise ConversationServiceError(
                        code, "同一工作区已有检查点恢复操作"
                    ) from exc
                raise
            if not created:
                if operation["state"] == "completed":
                    return self._restore_result(operation)
                if operation["state"] in {"rolled_back", "recovery_required"}:
                    raise ConversationServiceError(
                        "checkpoint_restore_failed",
                        "此前的同一恢复请求未完成，请使用新的请求重试",
                    )
            operation_id = str(operation["id"])
            self._repository.update_restore_operation(
                operation_id, state="applying", applied_steps=0
            )
            restorer = WorkspaceCheckpointRestorer(
                Path(conversation.workspace_path), self._artifact_store
            )
            try:
                restored_files = restorer.apply(
                    plan,
                    on_progress=lambda count: self._repository.update_restore_operation(
                        operation_id, applied_steps=count
                    ),
                )
                restorer.verify_applied(plan)
                completed = self._repository.complete_restore_operation(
                    operation_id, restored_file_count=restored_files
                )
                return self._restore_result(completed)
            except Exception as exc:
                logger.exception("checkpoint restore failed: %s", operation_id)
                error_code = (
                    exc.code
                    if isinstance(exc, CheckpointError)
                    else "checkpoint_timeline_changed"
                    if isinstance(exc, ValueError)
                    and str(exc) == "checkpoint_timeline_changed"
                    else "checkpoint_restore_failed"
                )
                try:
                    restorer.rollback(plan)
                    self._repository.update_restore_operation(
                        operation_id,
                        state="rolled_back",
                        error_code=error_code,
                    )
                except Exception:
                    logger.exception(
                        "checkpoint restore rollback requires recovery: %s",
                        operation_id,
                    )
                    self._repository.update_restore_operation(
                        operation_id,
                        state="recovery_required",
                        error_code="checkpoint_recovery_required",
                    )
                    raise ConversationServiceError(
                        "checkpoint_recovery_required",
                        "恢复中断且自动回滚未完成；请停止修改工作区并重启应用",
                    ) from exc
                raise ConversationServiceError(
                    error_code, "检查点恢复失败，工作区已自动回滚"
                ) from exc
        finally:
            if lease_acquired:
                self.runtime.release_workspace_lease(
                    conversation.workspace_key, owner_id
                )
            restore_lock.release()

    def start_turn(
        self,
        conversation_id: str,
        *,
        user_text: str,
        idempotency_key: Optional[str] = None,
        profile_id: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
        inbox_item_id: Optional[str] = None,
        attachment_ids: Sequence[str] = (),
    ) -> Dict[str, Any]:
        record = self._require_conversation(conversation_id)
        if record.state != ConversationState.ACTIVE.value:
            raise ConversationServiceError(
                "conversation_archived", "已归档会话不能开启新 turn"
            )
        if idempotency_key:
            stored_turn = self._repository.get_turn_by_idempotency(
                conversation_id, idempotency_key
            )
            if stored_turn is not None:
                return self._turn_to_dict(stored_turn)
        if self._repository.has_unfinished_restore_for_workspace(record.workspace_key):
            raise ConversationServiceError(
                "checkpoint_recovery_required",
                "工作区存在尚未安全结束的检查点恢复；请重启应用完成恢复",
            )
        if self._repository.get_active_turn(conversation_id) is not None:
            raise ConversationServiceError(
                "conversation_busy", "该会话已有正在运行的 turn"
            )
        if len(attachment_ids) > MAX_ATTACHMENTS_PER_TURN:
            raise ConversationServiceError(
                "too_many_attachments", "每轮最多发送 4 个附件", field="attachment_ids"
            )
        attachments = self._repository.get_pending_attachments(
            conversation_id, attachment_ids
        )
        if len(attachments) != len(attachment_ids) or len(set(attachment_ids)) != len(
            attachment_ids
        ):
            raise ConversationServiceError(
                "attachment_unavailable",
                "附件不存在、属于其他会话或已被发送",
                field="attachment_ids",
            )
        if sum(item.size_bytes for item in attachments) > MAX_ATTACHMENTS_TOTAL_BYTES:
            raise ConversationServiceError(
                "attachments_too_large",
                "本轮附件总大小不能超过 20 MiB",
                field="attachment_ids",
            )
        if (not user_text or not user_text.strip()) and not attachments:
            raise ConversationServiceError(
                "invalid_task", "任务内容不能为空", field="task"
            )
        connection, profile = self._resolve_connection(profile_id or record.profile_id)
        workspace = Path(record.workspace_path)
        if not workspace.is_dir():
            raise ConversationServiceError(
                "invalid_workspace", "会话工作区已不可用", field="workspace"
            )
        workspace_key = _canonical_workspace_key(workspace)
        run_id = uuid.uuid4().hex
        existing_history = self._repository.get_canonical_history(conversation_id)
        initial_messages = []
        if not existing_history:
            initial_messages.append(SystemMessage(SYSTEM_PROMPT))
        initial_messages.append(
            UserMessage(
                user_text.strip(), source="user", attachments=tuple(attachments)
            )
        )
        try:
            turn, created = self._repository.create_turn_with_initial_messages(
                conversation_id,
                user_text=user_text.strip(),
                run_id=run_id,
                idempotency_key=idempotency_key,
                messages=initial_messages,
                inbox_item_id=inbox_item_id,
                attachment_ids=tuple(attachment_ids),
            )
        except ValueError as exc:
            if str(exc) == "conversation_busy":
                raise ConversationServiceError(
                    "conversation_busy", "该会话已有正在运行的 turn"
                ) from exc
            if str(exc) == "inbox_item_not_queued":
                raise ConversationServiceError(
                    "inbox_item_not_queued", "队列项已被其他操作更新"
                ) from exc
            if str(exc) in {"attachment_duplicate", "attachment_unavailable"}:
                raise ConversationServiceError(
                    "attachment_unavailable", "附件已被其他请求使用"
                ) from exc
            raise
        if not created:
            return self._turn_to_dict(turn)
        # Deterministic default title from the first user message.
        if record.title_source == "auto" and turn.ordinal == 1:
            self._repository.set_auto_title(
                conversation_id, title_from_user_text(turn.user_text)
            )
        cancel_event = threading.Event()
        history = CanonicalHistory()
        for message in self._repository.get_canonical_history(conversation_id):
            history.append(message)
        journal = CanonicalJournal(self._repository, conversation_id, turn.id)
        collector = ToolChangeCollector(workspace, self._artifact_store)
        sink = _PersistEventSink(self._repository, conversation_id, turn.id, run_id)
        try:
            loop = self._build_loop(
                connection=connection,
                workspace=workspace,
                task=turn.user_text,
                run_id=run_id,
                conversation_id=conversation_id,
                turn_id=turn.id,
                cancel_event=cancel_event,
                sink=sink,
                journal=journal,
                collector=collector,
                history=history,
                request_options=ModelRequestOptions(
                    reasoning_mode=profile.reasoning_mode if profile else "auto",
                    reasoning_effort=(
                        reasoning_effort
                        if reasoning_effort is not None
                        else (profile.reasoning_effort if profile else None)
                    ),
                ),
            )
        except Exception as exc:
            self._repository.update_turn_state(
                conversation_id, turn.id, state=TurnState.REJECTED.value
            )
            self._repository.abandon_groups_for_rejected_turn(conversation_id, turn.id)
            if inbox_item_id is not None:
                self._repository.block_inbox_item(
                    conversation_id, inbox_item_id, "turn_rejected"
                )
            logger.warning(
                "turn %s rejected during loop build: %s", turn.id, type(exc).__name__
            )
            raise ConversationServiceError(
                "turn_rejected", "本轮启动失败，不能创建运行"
            ) from exc
        try:
            self.runtime.submit(
                conversation_id,
                workspace_key,
                turn_id=turn.id,
                run_id=run_id,
                target=lambda: self._run_worker(
                    loop,
                    conversation_id,
                    turn.id,
                    run_id,
                    journal,
                    collector,
                    turn.user_text,
                    history,
                    connection,
                    workspace_key,
                ),
                cancel_event=cancel_event,
                on_finish=lambda: self._after_turn_finished(conversation_id),
            )
        except RuntimeRegistryError as exc:
            self._repository.update_turn_state(
                conversation_id, turn.id, state=TurnState.REJECTED.value
            )
            self._repository.abandon_groups_for_rejected_turn(conversation_id, turn.id)
            if inbox_item_id is not None:
                self._repository.block_inbox_item(
                    conversation_id, inbox_item_id, exc.code
                )
            raise ConversationServiceError(
                exc.code,
                str(exc),
                field="workspace" if exc.code == "workspace_busy" else None,
            ) from exc
        return self._turn_to_dict(self._require_turn(conversation_id, turn.id))

    def cancel_turn(self, conversation_id: str, turn_id: str) -> Dict[str, Any]:
        self._require_turn(conversation_id, turn_id)
        active = self._repository.get_active_turn(conversation_id)
        if active is None or active.id != turn_id:
            raise ConversationServiceError("turn_not_active", "该 turn 当前未运行")
        self.permissions.cancel_turn(conversation_id, turn_id)
        self.runtime.cancel(conversation_id)
        return self._turn_to_dict(self._require_turn(conversation_id, turn_id))

    def list_permission_requests(
        self, conversation_id: str, turn_id: str
    ) -> List[Dict[str, Any]]:
        self._require_turn(conversation_id, turn_id)
        return self.permissions.list_pending(conversation_id, turn_id)

    def resolve_permission_request(
        self,
        conversation_id: str,
        turn_id: str,
        request_id: str,
        *,
        decision: str,
    ) -> Dict[str, Any]:
        self._require_turn(conversation_id, turn_id)
        if decision not in {"allow", "deny"}:
            raise ConversationServiceError(
                "invalid_permission_decision", "decision 必须是 allow/deny"
            )
        resolved = self.permissions.resolve(
            conversation_id,
            turn_id,
            request_id,
            allow=decision == "allow",
        )
        if resolved is None:
            raise ConversationServiceError(
                "permission_request_not_pending", "权限申请不存在或已经处理"
            )
        return resolved

    # ------------------------------------------------------------ events/changes

    def get_events(
        self,
        conversation_id: str,
        turn_id: str,
        *,
        after_seq: int = 0,
        limit: int = 500,
    ) -> List[Dict[str, Any]]:
        self._require_turn(conversation_id, turn_id)
        return self._repository.list_public_events(
            conversation_id=conversation_id,
            turn_id=turn_id,
            after_seq=after_seq,
            limit=limit,
        )

    def get_stream_snapshot(
        self, conversation_id: str, turn_id: str
    ) -> List[Dict[str, Any]]:
        self._require_turn(conversation_id, turn_id)
        return self._repository.get_stream_checkpoints(conversation_id, turn_id)

    def get_change_set(
        self, conversation_id: str, turn_id: str
    ) -> Optional[Dict[str, Any]]:
        self._require_turn(conversation_id, turn_id)
        return self._repository.get_change_set(conversation_id, turn_id)

    def get_file_change(
        self, conversation_id: str, turn_id: str, change_id: str
    ) -> Optional[Dict[str, Any]]:
        self._require_turn(conversation_id, turn_id)
        return self._repository.get_file_change(conversation_id, turn_id, change_id)

    def get_file_preview(
        self,
        conversation_id: str,
        turn_id: str,
        change_id: str,
        *,
        mode: str,
    ) -> Dict[str, Any]:
        if mode not in {"diff", "before", "after", "current"}:
            raise ConversationServiceError("invalid_preview_mode", "预览模式无效")
        conversation = self._require_conversation(conversation_id)
        self._require_turn(conversation_id, turn_id)
        change = self._repository.get_file_change(conversation_id, turn_id, change_id)
        if change is None:
            raise ConversationServiceError("artifact_not_found", "文件变更不存在")
        base = {
            "change_id": change_id,
            "relative_path": change["relative_path"],
            "change_type": change["change_type"],
            "mode": mode,
            "lines": [],
            "additions": int(change.get("additions", 0)),
            "deletions": int(change.get("deletions", 0)),
            "truncated": False,
            "binary": bool(change.get("binary")),
            "before_sha": change.get("before_sha"),
            "after_sha": change.get("after_sha"),
            "current_sha": None,
            "diverged": False,
            "error": None,
        }
        if change.get("preview_status") != "available":
            base["error"] = {
                "code": change.get("preview_status", "preview_unavailable"),
                "message": "该文件超出预算、为二进制或捕获不完整，无法安全预览",
            }
            return base

        try:
            before = (
                self._artifact_store.read_text(change["before_blob_id"])
                if change.get("before_blob_id")
                else None
            )
            after = (
                self._artifact_store.read_text(change["after_blob_id"])
                if change.get("after_blob_id")
                else None
            )
        except Exception:
            base["error"] = {
                "code": "artifact_corrupt",
                "message": "快照读取失败或完整性校验不通过",
            }
            return base

        current_text: Optional[str] = None
        current_exists = False
        try:
            target = resolve_inside(
                Path(conversation.workspace_path),
                change["relative_path"],
                must_exist=False,
            )
            current_exists = target.is_file()
            if current_exists:
                if target.stat().st_size > MAX_FILE_BYTES:
                    raise ValueError("current_too_large")
                current_data = target.read_bytes()
                import hashlib

                base["current_sha"] = hashlib.sha256(current_data).hexdigest()
                current_text = current_data.decode("utf-8")
        except (OSError, UnicodeDecodeError, ValueError):
            if mode == "current":
                base["error"] = {
                    "code": "current_preview_unavailable",
                    "message": "当前文件不可安全预览",
                }
                return base
        expected_after = change.get("after_sha")
        base["diverged"] = current_exists != (expected_after is not None) or (
            current_exists and base["current_sha"] != expected_after
        )

        if mode == "diff":
            result = build_diff(before, after)
            base.update(
                lines=result.lines,
                additions=result.additions,
                deletions=result.deletions,
                truncated=result.truncated,
            )
            return base
        selected = {"before": before, "after": after, "current": current_text}[mode]
        lines = (selected or "").splitlines()
        base["lines"] = lines[:MAX_DIFF_LINES]
        base["truncated"] = len(lines) > MAX_DIFF_LINES
        return base

    # ------------------------------------------------------------ inbox

    def get_inbox(self, conversation_id: str) -> Dict[str, Any]:
        self._require_conversation(conversation_id)
        return self._repository.get_inbox_snapshot(conversation_id)

    def enqueue_inbox(
        self,
        conversation_id: str,
        *,
        content: str,
        mode: str,
        idempotency_key: Optional[str] = None,
        profile_id: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
    ) -> Dict[str, Any]:
        record = self._require_conversation(conversation_id)
        if record.state != ConversationState.ACTIVE.value:
            raise ConversationServiceError(
                "conversation_archived", "已归档会话不能新增加入队列"
            )
        if not content or not content.strip():
            raise ConversationServiceError(
                "invalid_task", "消息内容不能为空", field="content"
            )
        if mode not in ("queue", "steer"):
            raise ConversationServiceError("invalid_mode", "mode 必须是 queue/steer")
        active = self._repository.get_active_turn(conversation_id)
        bound_turn_id = active.id if active else None
        if mode == "steer" and bound_turn_id is None:
            raise ConversationServiceError(
                "turn_not_steerable",
                "当前没有可插入的 active turn，消息已作为普通 Queue 处理",
            )
        try:
            self._repository.enqueue_inbox_item(
                conversation_id,
                content=content,
                requested_mode=mode,
                idempotency_key=idempotency_key,
                bound_turn_id=bound_turn_id,
                profile_id=profile_id,
                reasoning_effort=reasoning_effort,
            )
        except ValueError as exc:
            if str(exc) == "turn_not_steerable":
                self._repository.enqueue_inbox_item(
                    conversation_id,
                    content=content,
                    requested_mode="queue",
                    idempotency_key=idempotency_key,
                    profile_id=profile_id,
                    reasoning_effort=reasoning_effort,
                )
            else:
                raise
        return self._repository.get_inbox_snapshot(conversation_id)

    def edit_inbox(
        self,
        conversation_id: str,
        item_id: str,
        *,
        content: Optional[str] = None,
        mode: Optional[str] = None,
        expected_version: int,
    ) -> Dict[str, Any]:
        try:
            self._repository.edit_inbox_item(
                conversation_id,
                item_id,
                content=content,
                requested_mode=mode,
                expected_version=expected_version,
            )
        except KeyError as exc:
            raise ConversationServiceError("item_not_found", "队列项不存在") from exc
        except ValueError as exc:
            code = (
                "item_not_editable"
                if str(exc) == "item_not_editable"
                else "version_conflict"
            )
            raise ConversationServiceError(code, str(exc)) from exc
        return self._repository.get_inbox_snapshot(conversation_id)

    def remove_inbox(
        self,
        conversation_id: str,
        item_id: str,
        *,
        expected_version: int,
    ) -> Dict[str, Any]:
        try:
            self._repository.remove_inbox_item(
                conversation_id, item_id, expected_version=expected_version
            )
        except KeyError as exc:
            raise ConversationServiceError("item_not_found", "队列项不存在") from exc
        except ValueError as exc:
            code = (
                "item_not_editable"
                if str(exc) == "item_not_editable"
                else "version_conflict"
            )
            raise ConversationServiceError(code, str(exc)) from exc
        return self._repository.get_inbox_snapshot(conversation_id)

    def reorder_inbox(
        self,
        conversation_id: str,
        *,
        ordered_ids: Sequence[str],
        expected_queue_version: int,
    ) -> Dict[str, Any]:
        try:
            self._repository.reorder_inbox_items(
                conversation_id,
                ordered_ids,
                expected_queue_version=expected_queue_version,
            )
        except ValueError as exc:
            code = (
                "version_conflict"
                if str(exc) == "version_conflict"
                else "invalid_reorder"
            )
            raise ConversationServiceError(code, str(exc)) from exc
        return self._repository.get_inbox_snapshot(conversation_id)

    def steer_inbox(
        self,
        conversation_id: str,
        item_id: str,
        *,
        expected_version: int,
    ) -> Dict[str, Any]:
        active = self._repository.get_active_turn(conversation_id)
        if active is None:
            raise ConversationServiceError(
                "turn_not_steerable", "当前没有可插入的 active turn"
            )
        try:
            self._repository.request_steer(
                conversation_id,
                item_id,
                expected_version=expected_version,
            )
        except KeyError as exc:
            raise ConversationServiceError("item_not_found", "队列项不存在") from exc
        except ValueError as exc:
            code = (
                "turn_not_steerable"
                if str(exc) == "turn_not_steerable"
                else "version_conflict"
                if str(exc) == "version_conflict"
                else "item_not_steerable"
            )
            raise ConversationServiceError(code, str(exc)) from exc
        return self._repository.get_inbox_snapshot(conversation_id)

    def retry_inbox(
        self,
        conversation_id: str,
        item_id: str,
        *,
        expected_version: int,
    ) -> Dict[str, Any]:
        try:
            self._repository.retry_inbox_item(
                conversation_id, item_id, expected_version=expected_version
            )
        except KeyError as exc:
            raise ConversationServiceError("item_not_found", "队列项不存在") from exc
        except ValueError as exc:
            code = (
                "version_conflict"
                if str(exc) == "version_conflict"
                else "item_not_blocked"
            )
            raise ConversationServiceError(code, str(exc)) from exc
        self._start_next_from_queue(conversation_id)
        return self._repository.get_inbox_snapshot(conversation_id)

    # ------------------------------------------------------------ shutdown

    def shutdown(self, timeout: float = 5.0) -> None:
        self.permissions.cancel_all()
        self.runtime.shutdown(timeout=timeout)
        recovered = self._repository.recover_active_turns()
        for turn in recovered:
            self._repository.recover_pending_groups_for_turn(
                turn.conversation_id, turn.id
            )

    # ------------------------------------------------------------ internals

    def _after_turn_finished(self, conversation_id: str) -> None:
        """Called after a worker is removed from the runtime registry.

        This is the single queue consumer entry: pending steers are demoted to
        Queue, and at most one queued item is claimed to create the next turn.
        """
        with self._queue_consumer_locks_lock:
            lock = self._queue_consumer_locks.setdefault(
                conversation_id, threading.Lock()
            )
        if not lock.acquire(blocking=False):
            return
        try:
            self._repository.demote_all_steer_pending(conversation_id)
            self._start_next_from_queue(conversation_id)
        except Exception:
            logger.exception(
                "queue consumer failed for conversation %s", conversation_id
            )
        finally:
            lock.release()

    def _start_next_from_queue(self, conversation_id: str) -> None:
        # A mutation can remove the preliminary head before this consumer's
        # transaction starts.  Re-read in a loop instead of recursing, while
        # still starting no more than one actual turn per invocation.
        while True:
            if self.runtime.is_active(conversation_id):
                return
            record = self._repository.get_conversation(conversation_id)
            if record is None or record.state != ConversationState.ACTIVE.value:
                return
            queued = self._repository.list_queued_items(conversation_id)
            if not queued:
                return
            item = queued[0]
            try:
                self.start_turn(
                    conversation_id,
                    user_text=item["content"],
                    # A queue item is at-most-once for a particular durable
                    # version.  Explicit retry advances that version and is a
                    # new, auditable delivery attempt rather than returning a
                    # previously rejected turn.
                    idempotency_key=f"inbox:{item['id']}:{item['version']}",
                    profile_id=item.get("profile_id"),
                    reasoning_effort=item.get("reasoning_effort"),
                    inbox_item_id=item["id"],
                )
                return
            except ConversationServiceError as exc:
                if exc.code == "conversation_busy":
                    return
                if exc.code == "inbox_item_not_queued":
                    continue
                logger.warning(
                    "queue item %s blocked during auto-start: %s",
                    item["id"],
                    exc.code,
                )
                self._repository.block_inbox_item(conversation_id, item["id"], exc.code)
                return

    def _set_state(
        self, conversation_id: str, state: str, expected_version: int
    ) -> Dict[str, Any]:
        self._require_conversation(conversation_id)
        if self.runtime.is_active(conversation_id):
            raise ConversationServiceError(
                "conversation_busy", "会话正在运行，不能归档/删除"
            )
        try:
            updated = self._repository.set_conversation_state(
                conversation_id, state=state, expected_version=expected_version
            )
        except KeyError as exc:
            raise ConversationServiceError(
                "conversation_not_found", "会话不存在"
            ) from exc
        except ValueError as exc:
            if str(exc) == "checkpoint_restore_busy":
                raise ConversationServiceError(
                    "checkpoint_restore_busy", "检查点恢复期间不能归档或取消归档"
                ) from exc
            raise ConversationServiceError(
                "version_conflict", "会话已被其他端修改", field="version"
            ) from exc
        return self._conversation_to_dict(updated)

    def _run_worker(
        self,
        loop: AgentLoop,
        conversation_id: str,
        turn_id: str,
        run_id: str,
        journal: CanonicalJournal,
        collector: ToolChangeCollector,
        task: str,
        history: CanonicalHistory,
        connection: ResolvedModelConnection,
        workspace_key: str,
    ) -> None:
        self._repository.update_turn_state(
            conversation_id, turn_id, state=TurnState.RUNNING.value
        )
        try:
            result: RunResult = loop.run_turn(
                task, history=history, task_already_in_history=True
            )
        except Exception:
            result = None
        persisted = self._repository.get_turn(conversation_id, turn_id)
        if persisted is None or not persisted.is_active:
            journal.close()
            return
        if result is not None:
            self._finish_turn(
                conversation_id, turn_id, run_id, result, journal, collector
            )
            threading.Thread(
                target=self._maybe_extract_memory_candidates,
                kwargs={
                    "connection": connection,
                    "conversation_id": conversation_id,
                    "turn_id": turn_id,
                    "workspace_key": workspace_key,
                    "task": task,
                    "result": result,
                },
                name=f"memory-candidate-{turn_id[:8]}",
                daemon=True,
            ).start()
        else:
            self._repository.update_turn_state(
                conversation_id, turn_id, state=TurnState.INTERRUPTED.value
            )
            journal.close()

    def _maybe_extract_memory_candidates(
        self,
        *,
        connection: ResolvedModelConnection,
        conversation_id: str,
        turn_id: str,
        workspace_key: str,
        task: str,
        result: RunResult,
    ) -> None:
        """Optional P1 candidate extraction after a successful terminal turn.

        It is deliberately best-effort and isolated: a model failure, timeout,
        invalid JSON or bad proposal never changes the main turn's result.
        """
        if not self._memory.is_candidate_enabled():
            return
        if not self._memory.is_memory_enabled(
            conversation_id=conversation_id, workspace_key=workspace_key
        ):
            return
        final_text = result.final_text or ""
        if not final_text.strip():
            return
        if not self._memory.extraction_input_is_safe(task, final_text):
            return
        try:
            existing = self._memory.search(
                task,
                scope_type="workspace",
                scope_key=workspace_key,
                limit=8,
            )
            extractor = MemoryCandidateExtractor(self._client_factory(connection))
            proposals = extractor.extract(
                user_text=task,
                assistant_text=final_text,
                existing_memories=existing,
            )
            if proposals:
                self._memory.ingest_candidate_proposals(
                    proposals,
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                    workspace_key=workspace_key,
                )
                logger.info(
                    "memory candidates proposed for turn %s: %d",
                    turn_id,
                    len(proposals),
                )
        except Exception:
            logger.exception("memory candidate extraction failed for turn %s", turn_id)

    def _finish_turn(
        self,
        conversation_id: str,
        turn_id: str,
        run_id: str,
        result: RunResult,
        journal: CanonicalJournal,
        collector: ToolChangeCollector,
    ) -> None:
        status = (
            TurnState.SUCCESS.value
            if result.status is RunStatus.SUCCESS
            else (
                TurnState.INTERRUPTED.value
                if result.status is RunStatus.INTERRUPTED
                else TurnState.ERROR.value
            )
        )
        payload = result.to_dict()
        try:
            change_set = collector.finalize(
                self._repository,
                conversation_id=conversation_id,
                turn_id=turn_id,
                status="final",
            )
            confirmed_paths = {
                str(item["relative_path"]) for item in change_set.get("files", [])
            }
            expected_paths = {str(path) for path in result.mutated_paths}
            missing = sorted(expected_paths - confirmed_paths)
            if missing:
                payload.setdefault("details", {})["change_set_mismatch"] = {
                    "missing_paths": missing,
                    "coverage": change_set.get("coverage"),
                }
                logger.error(
                    "turn %s change set missed mutated paths: %s", turn_id, missing
                )
            self._repository.set_turn_terminal(
                conversation_id,
                turn_id,
                state=status,
                result_json=json.dumps(payload, ensure_ascii=False, sort_keys=True),
                error_code=result.stop_reason.value,
            )
        except Exception:
            logger.exception("failed to persist terminal turn %s", turn_id)
            try:
                self._repository.set_turn_terminal(
                    conversation_id,
                    turn_id,
                    state=TurnState.ERROR.value,
                    result_json=json.dumps(
                        {"status": "ERROR", "stop_reason": "INTERNAL_ERROR"}
                    ),
                    error_code="PERSIST_FAILED",
                )
            except Exception:
                pass
        finally:
            journal.close()
        if result.plan_state:
            try:
                # Idempotent fallback for a failure in AgentLoop's terminal
                # plan callback. Keep it isolated from the already-persisted
                # turn result so optional plan metadata cannot rewrite a
                # successful turn as PERSIST_FAILED.
                self._repository.finish_turn_plan(
                    conversation_id, turn_id, state=result.plan_state
                )
            except Exception:
                logger.exception("failed to finalize plan for turn %s", turn_id)

    def _resolve_connection(
        self, profile_id: Optional[str]
    ) -> Tuple[ResolvedModelConnection, Optional[ProviderProfile]]:
        try:
            config = self.profile_store.load()
            profiles = {p.id: p for p in config.profiles.values()}
            selected_id = profile_id or config.active_profile
            selected = profiles.get(selected_id) if selected_id else None
            connection = resolve_connection(
                profiles=profiles,
                active_profile=config.active_profile,
                explicit_profile=profile_id,
                env=self._env,
                credential_resolver=self.credentials.resolve,
            )
            return connection, selected
        except (ProfileError, ConfigError) as exc:
            field = getattr(exc, "field", None)
            code = getattr(exc, "code", "invalid_config")
            raise ConversationServiceError(code, str(exc), field=field) from exc
        except CredentialError as exc:
            raise ConversationServiceError(exc.code, str(exc), field=exc.field) from exc

    def _make_memory_provider(
        self, conversation_id: str, turn_id: str, workspace: Path, task: str
    ):
        workspace_key = _canonical_workspace_key(workspace)

        def provide():
            # ContextManager caches the result, so this runs at most once per
            # turn; MemoryService also persists memory_usage at that moment.
            return self._memory.project_for_turn(
                conversation_id=conversation_id,
                turn_id=turn_id,
                workspace_key=workspace_key,
                user_text=task,
            )

        return provide

    def _build_loop(
        self,
        *,
        connection: ResolvedModelConnection,
        workspace: Path,
        task: str,
        run_id: str,
        conversation_id: str,
        turn_id: str,
        cancel_event: threading.Event,
        sink,
        journal: CanonicalJournal,
        collector: ToolChangeCollector,
        history: CanonicalHistory,
        request_options: Optional[ModelRequestOptions] = None,
    ) -> AgentLoop:
        options = request_options or ModelRequestOptions()
        inbox_port = InboxPort(self._repository, conversation_id, turn_id)
        if self._loop_builder is not None:
            return self._loop_builder(
                connection=connection,
                workspace=workspace,
                task=task,
                run_id=run_id,
                conversation_id=conversation_id,
                turn_id=turn_id,
                inbox_port=inbox_port,
                cancel_event=cancel_event,
                sink=sink,
                journal=journal,
                collector=collector,
                history=history,
                request_options=options,
            )
        tracker = FileObservationTracker()
        plan_ledger = PlanLedger(
            persist=lambda snapshot, expected: self._persist_plan_snapshot(
                conversation_id,
                turn_id,
                snapshot,
                expected,
            ),
            finish=lambda state: self._repository.finish_turn_plan(
                conversation_id, turn_id, state=state
            ),
        )
        registry = build_default_tools(
            Workspace(workspace), tracker, cancel_event.is_set, plan_ledger
        )
        executor = ToolExecutor(
            registry,
            InteractiveWorkspaceToolPolicy(
                self.permissions,
                conversation_id=conversation_id,
                turn_id=turn_id,
                is_cancelled=cancel_event.is_set,
                command_policy=lambda: self._conversation_command_policy(
                    conversation_id
                ),
            ),
            cancel_event.is_set,
            observer=collector,
        )
        return AgentLoop(
            model_client=self._client_factory(connection),
            tool_registry=registry,
            tool_executor=executor,
            context_manager=ContextManager(
                DEFAULT_CHAR_BUDGET,
                memory_provider=self._make_memory_provider(
                    conversation_id, turn_id, workspace, task
                ),
                attachment_loader=lambda ref: self._attachment_store.read(ref.sha256),
                context_window_tokens=connection.context_window_tokens,
            ),
            completion_policy=CompletionPolicy(),
            run_id=run_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            inbox_port=inbox_port,
            max_steps=DEFAULT_MAX_STEPS,
            hard_max_steps=DEFAULT_HARD_MAX_STEPS,
            is_cancelled=cancel_event.is_set,
            event_sink=sink,
            journal=journal,
            plan_ledger=plan_ledger,
            request_options=options,
        )

    def _persist_plan_snapshot(
        self,
        conversation_id: str,
        turn_id: str,
        snapshot: PlanSnapshot,
        expected_revision: int,
    ) -> None:
        self._repository.save_turn_plan(
            conversation_id,
            turn_id,
            revision=snapshot.revision,
            state=snapshot.state,
            explanation=snapshot.explanation,
            steps=[item.to_dict() for item in snapshot.steps],
            expected_revision=expected_revision,
        )

    def _run_worker_with_history(
        self, loop: AgentLoop, task: str, history: CanonicalHistory
    ):
        # Kept as a convenience seam for custom loop builders.
        return loop.run_turn(task, history=history)

    def _recover_restore_operations(self) -> None:
        """Roll back file mutations whose timeline transaction never committed."""

        for operation in self._repository.list_unfinished_restore_operations():
            operation_id = str(operation["id"])
            if operation["state"] == "prepared":
                self._repository.update_restore_operation(
                    operation_id,
                    state="rolled_back",
                    error_code="checkpoint_process_restarted",
                )
                continue
            conversation = self._repository.get_conversation(
                str(operation["conversation_id"])
            )
            if conversation is None:
                continue
            try:
                workspace = Path(conversation.workspace_path)
                restorer = WorkspaceCheckpointRestorer(workspace, self._artifact_store)
                restorer.rollback(dict(operation["plan"]))
                self._repository.update_restore_operation(
                    operation_id,
                    state="rolled_back",
                    error_code="checkpoint_process_restarted",
                )
                logger.warning(
                    "rolled back interrupted checkpoint restore %s", operation_id
                )
            except Exception:
                logger.exception(
                    "checkpoint restore requires manual recovery: %s", operation_id
                )
                self._repository.update_restore_operation(
                    operation_id,
                    state="recovery_required",
                    error_code="checkpoint_recovery_required",
                )

    @staticmethod
    def _restore_result(operation: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "operation_id": str(operation["id"]),
            "conversation_id": str(operation["conversation_id"]),
            "target_turn_id": str(operation["target_turn_id"]),
            "state": str(operation["state"]),
            "superseded_turn_count": int(operation.get("superseded_turn_count", 0)),
            "restored_file_count": int(operation.get("restored_file_count", 0)),
            "completed_at": operation.get("completed_at"),
        }

    def _require_conversation(self, conversation_id: str) -> ConversationRecord:
        record = self._repository.get_conversation(conversation_id)
        if record is None:
            raise ConversationServiceError("conversation_not_found", "会话不存在")
        return record

    def _conversation_command_policy(self, conversation_id: str) -> str:
        record = self._repository.get_conversation(conversation_id)
        return record.command_policy if record is not None else "deny"

    def _require_turn(self, conversation_id: str, turn_id: str) -> TurnRecord:
        record = self._repository.get_turn(conversation_id, turn_id)
        if record is None:
            raise ConversationServiceError("turn_not_found", "turn 不存在")
        return record

    @staticmethod
    def _conversation_to_dict(record: ConversationRecord) -> Dict[str, Any]:
        return {
            "id": record.id,
            "title": record.title,
            "title_source": record.title_source,
            "workspace_path": record.workspace_path,
            "workspace_key": record.workspace_key,
            "profile_id": record.profile_id,
            "reasoning_effort": record.reasoning_effort,
            "command_policy": record.command_policy,
            "state": record.state,
            "version": record.version,
            "created_at": record.created_at,
            "last_activity_at": record.last_activity_at,
            "archived_at": record.archived_at,
        }

    def _turn_to_dict(self, record: TurnRecord) -> Dict[str, Any]:
        result = None
        if record.result_json:
            try:
                result = redact_public_run_result(json.loads(record.result_json))
            except (TypeError, ValueError):
                result = None
        try:
            plan = self._repository.get_turn_plan(record.conversation_id, record.id)
        except ValueError:
            # Preserve conversation availability if a manually modified or
            # damaged optional plan row cannot be decoded. The immutable DB
            # row remains available for diagnosis; no malformed content is
            # exposed through the public DTO.
            logger.error("corrupt plan snapshot for turn %s", record.id)
            plan = None
        return {
            "id": record.id,
            "conversation_id": record.conversation_id,
            "ordinal": record.ordinal,
            "state": record.state,
            "run_id": record.run_id,
            "user_text": record.user_text,
            "created_at": record.created_at,
            "started_at": record.started_at,
            "finished_at": record.finished_at,
            "result": result,
            "error_code": record.error_code,
            "timeline_state": record.timeline_state,
            "active": record.is_active,
            "plan": plan,
            "attachments": [
                self._attachment_to_dict(item)
                for item in self._repository.list_turn_attachments(
                    record.conversation_id, record.id
                )
            ],
        }

    @staticmethod
    def _attachment_to_dict(ref: AttachmentRef) -> Dict[str, Any]:
        return {
            "id": ref.id,
            "filename": ref.filename,
            "media_type": ref.media_type,
            "kind": ref.kind,
            "size_bytes": ref.size_bytes,
        }
