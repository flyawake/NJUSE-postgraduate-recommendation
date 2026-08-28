"""ConversationService: lifecycle orchestration over SQLite + runtime registry.

This service is the task_004 backend composition root for the web layer. It
does not implement AgentLoop/tool semantics; it resolves connections, creates
turns, injects the canonical journal and change collector, owns the work
registry and persists canonical/public facts.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from ..agent import AgentLoop
from ..artifacts.store import ArtifactStore
from ..changes.collector import ToolChangeCollector
from ..changes.diff import MAX_DIFF_LINES, MAX_FILE_BYTES, build_diff
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
from ..model_client import ModelClient, ModelClientFactory
from ..models import AgentEvent, RunResult, RunStatus, SystemMessage, UserMessage
from ..prompt import SYSTEM_PROMPT
from ..provider_config import ProfileError, ProfileStore, ProviderProfile, default_home
from ..tools import build_default_tools
from ..tools.executor import ToolExecutor
from ..tools.observation import FileObservationTracker
from ..tools.paths import Workspace, resolve_inside
from ..tools.policy import WorkspaceToolPolicy
from .domain import (
    ConversationRecord,
    ConversationState,
    TurnRecord,
    TurnState,
    title_from_user_text,
)
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
        return str(path.resolve(strict=True))
    except (OSError, RuntimeError, ValueError):
        return str(path.absolute())


class _PersistEventSink:
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

    def emit(self, event: AgentEvent) -> None:
        self._seq += 1
        self._repo.append_public_event(
            conversation_id=self._cid,
            turn_id=self._tid,
            run_id=self._run_id,
            event_seq=self._seq,
            kind=event.type,
            payload=event.to_dict(),
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
        self._recover_active_turns = self._repository.recover_active_turns()
        for turn in self._recover_active_turns:
            self._repository.recover_pending_groups_for_turn(
                turn.conversation_id, turn.id
            )
        self.runtime = RuntimeRegistry(max_workers=max_workers)
        self._artifact_store = ArtifactStore(resolved_home / "artifacts")
        # A crash can occur after an atomic CAS rename but before the DB ref
        # transaction, or after DB GC but before physical unlink. Startup
        # reconciliation is idempotent and never removes a referenced blob.
        referenced_blobs = self._repository.list_artifact_blob_ids()
        for blob_id in self._artifact_store.list_digests() - referenced_blobs:
            self._artifact_store.delete(blob_id)
        self._shutdown_lock = threading.RLock()

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
            orphaned = self._repository.delete_conversation(
                conversation_id, expected_version
            )
            for blob_id in orphaned:
                self._artifact_store.delete(blob_id)
        except KeyError as exc:
            raise ConversationServiceError(
                "conversation_not_found", "会话不存在"
            ) from exc
        except ValueError as exc:
            raise ConversationServiceError(
                "version_conflict", "会话已被其他端修改", field="version"
            ) from exc

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

    def start_turn(
        self,
        conversation_id: str,
        *,
        user_text: str,
        idempotency_key: Optional[str] = None,
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
        if self._repository.get_active_turn(conversation_id) is not None:
            raise ConversationServiceError(
                "conversation_busy", "该会话已有正在运行的 turn"
            )
        if not user_text or not user_text.strip():
            raise ConversationServiceError(
                "invalid_task", "任务内容不能为空", field="task"
            )
        connection, _profile = self._resolve_connection(record.profile_id)
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
        initial_messages.append(UserMessage(user_text.strip(), source="user"))
        try:
            turn, created = self._repository.create_turn_with_initial_messages(
                conversation_id,
                user_text=user_text.strip(),
                run_id=run_id,
                idempotency_key=idempotency_key,
                messages=initial_messages,
            )
        except ValueError as exc:
            if str(exc) == "conversation_busy":
                raise ConversationServiceError(
                    "conversation_busy", "该会话已有正在运行的 turn"
                ) from exc
            raise
        if not created:
            return self._turn_to_dict(turn)
        # Deterministic default title from the first user message.
        if record.title_source == "auto" and turn.ordinal == 1:
            self._repository.set_auto_title(
                conversation_id, title_from_user_text(user_text)
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
                task=user_text,
                run_id=run_id,
                cancel_event=cancel_event,
                sink=sink,
                journal=journal,
                collector=collector,
                history=history,
            )
        except Exception as exc:
            self._repository.update_turn_state(
                conversation_id, turn.id, state=TurnState.REJECTED.value
            )
            self._repository.abandon_groups_for_rejected_turn(conversation_id, turn.id)
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
                    user_text.strip(),
                    history,
                ),
                cancel_event=cancel_event,
            )
        except RuntimeRegistryError as exc:
            self._repository.update_turn_state(
                conversation_id, turn.id, state=TurnState.REJECTED.value
            )
            self._repository.abandon_groups_for_rejected_turn(conversation_id, turn.id)
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
        self.runtime.cancel(conversation_id)
        return self._turn_to_dict(self._require_turn(conversation_id, turn_id))

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

    # ------------------------------------------------------------ shutdown

    def shutdown(self, timeout: float = 5.0) -> None:
        self.runtime.shutdown(timeout=timeout)
        recovered = self._repository.recover_active_turns()
        for turn in recovered:
            self._repository.recover_pending_groups_for_turn(
                turn.conversation_id, turn.id
            )

    # ------------------------------------------------------------ internals

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
        else:
            self._repository.update_turn_state(
                conversation_id, turn_id, state=TurnState.INTERRUPTED.value
            )
            journal.close()

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

    def _resolve_connection(
        self, profile_id: Optional[str]
    ) -> Tuple[ResolvedModelConnection, Optional[ProviderProfile]]:
        try:
            config = self.profile_store.load()
            profiles = {p.id: p for p in config.profiles.values()}
            selected = profiles.get(profile_id) if profile_id else None
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

    def _build_loop(
        self,
        *,
        connection: ResolvedModelConnection,
        workspace: Path,
        task: str,
        run_id: str,
        cancel_event: threading.Event,
        sink,
        journal: CanonicalJournal,
        collector: ToolChangeCollector,
        history: CanonicalHistory,
    ) -> AgentLoop:
        if self._loop_builder is not None:
            return self._loop_builder(
                connection=connection,
                workspace=workspace,
                task=task,
                run_id=run_id,
                cancel_event=cancel_event,
                sink=sink,
                journal=journal,
                collector=collector,
                history=history,
            )
        tracker = FileObservationTracker()
        registry = build_default_tools(
            Workspace(workspace), tracker, cancel_event.is_set
        )
        executor = ToolExecutor(
            registry,
            WorkspaceToolPolicy(),
            cancel_event.is_set,
            observer=collector,
        )
        return AgentLoop(
            model_client=self._client_factory(connection),
            tool_registry=registry,
            tool_executor=executor,
            context_manager=ContextManager(DEFAULT_CHAR_BUDGET),
            completion_policy=CompletionPolicy(),
            run_id=run_id,
            max_steps=DEFAULT_MAX_STEPS,
            is_cancelled=cancel_event.is_set,
            event_sink=sink,
            journal=journal,
        )

    def _run_worker_with_history(
        self, loop: AgentLoop, task: str, history: CanonicalHistory
    ):
        # Kept as a convenience seam for custom loop builders.
        return loop.run_turn(task, history=history)

    def _require_conversation(self, conversation_id: str) -> ConversationRecord:
        record = self._repository.get_conversation(conversation_id)
        if record is None:
            raise ConversationServiceError("conversation_not_found", "会话不存在")
        return record

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
            "state": record.state,
            "version": record.version,
            "created_at": record.created_at,
            "last_activity_at": record.last_activity_at,
            "archived_at": record.archived_at,
        }

    @staticmethod
    def _turn_to_dict(record: TurnRecord) -> Dict[str, Any]:
        result = None
        if record.result_json:
            try:
                result = json.loads(record.result_json)
            except (TypeError, ValueError):
                result = None
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
            "active": record.is_active,
        }
