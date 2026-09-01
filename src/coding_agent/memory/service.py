"""MemoryService: stable facade over SQLite facts + hybrid lexical search.

The service is the only API used by ContextManager/projection, the web layer
and future candidate extraction. It keeps write-side facts separate from the
read-side active confirmed projection, applies the secret policy before any
persistence, and records usage in a secret-free audit table.
"""

from __future__ import annotations

import hashlib
import html
import json
import time
import unicodedata
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..conversations.store import SQLiteConversationRepository
from .analyzer import (
    normalized_hash,
    terms_for_query,
    tokenize,
)
from .models import (
    ACTIVE_MEMORY_STATUSES,
    MEMORY_DEFAULT_TOP_K,
    MEMORY_MAX_SINGLE_PROJECTION_CHARS,
    MEMORY_MAX_TITLE_CHARS,
    MEMORY_MAX_TOTAL_PROJECTION_CHARS,
    MemoryConfirmation,
    MemoryEntry,
    MemoryKind,
    MemoryProjection,
    MemoryScope,
    MemoryStatus,
)
from .policy import MemoryPolicy, MemoryPolicyError

_last_timestamp: float = 0.0


def _utcnow() -> str:
    global _last_timestamp
    now = time.time()
    if now <= _last_timestamp:
        now = _last_timestamp + 0.000001
    _last_timestamp = now
    return datetime.fromtimestamp(now, timezone.utc).isoformat(timespec="microseconds")


def _new_id() -> str:
    return uuid.uuid4().hex


def _clean_content(content: str) -> str:
    """Normalize Unicode without destroying user-visible case or line structure."""
    return unicodedata.normalize("NFKC", content).strip()


class MemoryServiceError(Exception):
    def __init__(self, code: str, message: str, *, field: Optional[str] = None):
        super().__init__(message)
        self.code = code
        self.field = field


def _scope_priority(scope_type: str) -> int:
    return {
        MemoryScope.CONVERSATION.value: 3,
        MemoryScope.WORKSPACE.value: 2,
        MemoryScope.GLOBAL.value: 1,
    }.get(scope_type, 0)


class MemoryService:
    def __init__(
        self,
        repository: SQLiteConversationRepository,
        policy: Optional[MemoryPolicy] = None,
    ) -> None:
        self._repo = repository
        self._policy = policy or MemoryPolicy()
        self._repo.ensure_memory_index()

    def _check_content(
        self, content: str, *, source_excerpt: Optional[str] = None
    ) -> None:
        try:
            self._policy.check(content, source_excerpt=source_excerpt)
        except MemoryPolicyError as exc:
            raise MemoryServiceError(exc.code, str(exc)) from exc

    def _check_title(self, title: Optional[str]) -> None:
        if not title or not title.strip():
            return
        if len(title.strip()) > MEMORY_MAX_TITLE_CHARS:
            raise MemoryServiceError(
                "memory_title_too_long",
                f"记忆标题超过 {MEMORY_MAX_TITLE_CHARS} 字符上限",
            )
        self._check_content(title.strip())

    # ------------------------------------------------------------ lifecycle

    def create_confirmed_memory(
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
        confirmation: str = MemoryConfirmation.EXPLICIT_UI.value,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        self._validate_scope(scope_type, scope_key)
        self._validate_kind(kind)
        if confirmation not in {
            MemoryConfirmation.EXPLICIT_UI.value,
            MemoryConfirmation.EXPLICIT_COMMAND.value,
            MemoryConfirmation.USER_APPROVED.value,
        }:
            raise MemoryServiceError(
                "invalid_confirmation", "confirmed 记忆必须来自显式用户确认"
            )
        self._check_content(content, source_excerpt=source_excerpt)
        self._check_title(title)
        clean_content = _clean_content(content)
        if not clean_content:
            raise MemoryServiceError("memory_content_empty", "记忆内容不能为空")
        data = self._entry_dict(
            scope_type=scope_type,
            scope_key=scope_key,
            kind=kind,
            content=clean_content,
            title=(title or "").strip() or None,
            status=MemoryStatus.CONFIRMED.value,
            confirmation=confirmation,
            source_conversation_id=source_conversation_id,
            source_turn_id=source_turn_id,
            source_excerpt=(source_excerpt or "").strip()[:500] or None,
        )
        return self._repo.create_memory_entry(
            data,
            event_kind="confirmed",
            event_payload={"confirmation": confirmation},
            idempotency_key=idempotency_key,
        )

    def create_candidate(
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
        self._validate_scope(scope_type, scope_key)
        self._validate_kind(kind)
        self._check_content(content, source_excerpt=source_excerpt)
        self._check_title(title)
        data = self._entry_dict(
            scope_type=scope_type,
            scope_key=scope_key,
            kind=kind,
            content=_clean_content(content),
            title=(title or "").strip() or None,
            status=MemoryStatus.CANDIDATE.value,
            confirmation=MemoryConfirmation.IMPORTED.value,
            source_conversation_id=source_conversation_id,
            source_turn_id=source_turn_id,
            source_excerpt=(source_excerpt or "").strip()[:500] or None,
        )
        if self._repo.memory_rejected_hash_exists(
            data["normalized_hash"], scope_type, scope_key
        ):
            raise MemoryServiceError(
                "memory_candidate_rejected",
                "相同内容的候选此前已被拒绝",
            )
        return self._repo.create_memory_entry(
            data,
            event_kind="proposed",
            event_payload={"confirmation": "imported"},
            idempotency_key=idempotency_key,
        )

    def get(self, entry_id: str) -> Optional[Dict[str, Any]]:
        return self._repo.get_memory_entry(entry_id)

    def list(
        self,
        *,
        scope_type: Optional[str] = None,
        scope_key: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        return self._repo.list_memory_entries(
            scope_type=scope_type,
            scope_key=scope_key,
            statuses=[status] if status else None,
            limit=limit,
        )

    def approve(
        self,
        entry_id: str,
        *,
        expected_version: int,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        replayed = self._idempotent_entry(idempotency_key, "approved")
        if replayed is not None:
            return replayed
        row = self._require(entry_id)
        if row["status"] != MemoryStatus.CANDIDATE.value:
            raise MemoryServiceError("memory_not_candidate", "只有候选记忆可以批准")
        self._check_content(
            str(row.get("content", "")), source_excerpt=row.get("source_excerpt")
        )
        updated = self._repo.update_memory_status(
            entry_id,
            status=MemoryStatus.CONFIRMED.value,
            confirmation=MemoryConfirmation.USER_APPROVED.value,
            expected_version=expected_version,
            event_kind="approved",
            idempotency_key=idempotency_key,
        )
        return updated

    def reject(
        self,
        entry_id: str,
        *,
        expected_version: int,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        replayed = self._idempotent_entry(idempotency_key, "rejected")
        if replayed is not None:
            return replayed
        row = self._require(entry_id)
        if row["status"] != MemoryStatus.CANDIDATE.value:
            raise MemoryServiceError("memory_not_candidate", "只有候选记忆可以拒绝")
        updated = self._repo.update_memory_status(
            entry_id,
            status=MemoryStatus.REJECTED.value,
            confirmation=row["confirmation"],
            expected_version=expected_version,
            event_kind="rejected",
            idempotency_key=idempotency_key,
        )
        return updated

    def edit(
        self,
        entry_id: str,
        *,
        content: str,
        kind: Optional[str] = None,
        title: Optional[str] = None,
        expected_version: int,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        replayed = self._idempotent_entry(idempotency_key, "edited")
        if replayed is not None:
            return replayed
        current = self._require(entry_id)
        if current["status"] not in {
            MemoryStatus.CONFIRMED.value,
            MemoryStatus.CANDIDATE.value,
        }:
            raise MemoryServiceError("memory_not_editable", "该记忆当前不可编辑")
        self._check_content(content, source_excerpt=current.get("source_excerpt"))
        self._check_title(title if title is not None else current.get("title"))
        clean = _clean_content(content)
        if not clean:
            raise MemoryServiceError("memory_content_empty", "记忆内容不能为空")
        new_kind = kind or current["kind"]
        self._validate_kind(new_kind)
        new_title = (title or "").strip() if title is not None else current.get("title")
        data = self._entry_dict(
            scope_type=current["scope_type"],
            scope_key=current["scope_key"],
            kind=new_kind,
            content=clean,
            title=new_title or None,
            status=(
                MemoryStatus.CONFIRMED.value
                if current["status"] == MemoryStatus.CONFIRMED.value
                else current["status"]
            ),
            confirmation=current["confirmation"],
            source_conversation_id=current.get("source_conversation_id"),
            source_turn_id=current.get("source_turn_id"),
            source_excerpt=current.get("source_excerpt"),
        )
        # Edit is versioned: the old row becomes superseded and the new row is
        # the current active version. History remains explainable.
        created = self._repo.create_memory_revision(
            data,
            supersede_entry_id=entry_id,
            expected_version=expected_version,
            event_payload={"supersedes_id": entry_id},
            idempotency_key=idempotency_key,
        )
        return created

    def delete(
        self,
        entry_id: str,
        *,
        expected_version: int,
        idempotency_key: Optional[str] = None,
    ) -> None:
        self._repo.delete_memory_entry(
            entry_id,
            expected_version=expected_version,
            idempotency_key=idempotency_key,
        )

    def reset_scope(
        self,
        scope_type: str,
        scope_key: str,
        *,
        idempotency_key: Optional[str] = None,
        expected_scope_version: Optional[int] = None,
    ) -> int:
        self._validate_scope(scope_type, scope_key)
        return self._repo.reset_memory_scope(
            scope_type,
            scope_key,
            idempotency_key=idempotency_key,
            expected_scope_version=expected_scope_version,
        )

    def scope_version(self, scope_type: str, scope_key: str) -> int:
        self._validate_scope(scope_type, scope_key)
        return self._repo.get_memory_scope_version(scope_type, scope_key)

    # ------------------------------------------------------------ retrieval

    def search(
        self,
        query: str,
        *,
        scope_type: Optional[str] = None,
        scope_key: Optional[str] = None,
        limit: int = MEMORY_DEFAULT_TOP_K,
        include_candidates: bool = False,
        statuses: Optional[Sequence[str]] = None,
    ) -> List[Dict[str, Any]]:
        requested_statuses = list(statuses or ())
        if not requested_statuses:
            requested_statuses = (
                list(ACTIVE_MEMORY_STATUSES)
                if not include_candidates
                else [MemoryStatus.CONFIRMED.value, MemoryStatus.CANDIDATE.value]
            )
        valid_statuses = {item.value for item in MemoryStatus}
        if any(status not in valid_statuses for status in requested_statuses):
            raise MemoryServiceError("invalid_status", "无效的 memory status")
        scopes = [scope_type] if scope_type else None
        scope_keys = [scope_key] if scope_key else None
        term_set = set(terms_for_query(query))
        if set(requested_statuses) == {MemoryStatus.CONFIRMED.value}:
            candidate_ids = self._repo.search_memory_ids(
                query,
                scope_types=scopes,
                scope_keys=scope_keys,
                statuses=requested_statuses,
                limit=max(64, limit * 8),
            )
            rows = self._repo.get_memory_entries_by_ids(candidate_ids)
        else:
            rows = self._repo.list_memory_entries(
                scope_type=scope_type,
                scope_key=scope_key,
                statuses=requested_statuses,
                limit=min(1_000, max(64, limit * 8)),
            )
            if term_set:
                rows = [
                    row
                    for row in rows
                    if term_set & set(tokenize(str(row.get("content", ""))))
                ]
        ranked = self._rank_rows(rows, term_set)
        return ranked[:limit]

    def project_for_turn(
        self,
        *,
        conversation_id: str,
        turn_id: str,
        workspace_key: str,
        user_text: str,
    ) -> Optional[MemoryProjection]:
        """Retrieve once per turn, record usage, and build the injection block."""
        if not self._memory_enabled_for(conversation_id, workspace_key):
            return None
        scope_pairs = [
            (MemoryScope.GLOBAL.value, "global"),
            (MemoryScope.WORKSPACE.value, workspace_key),
            (MemoryScope.CONVERSATION.value, conversation_id),
        ]
        candidate_ids = self._repo.search_memory_ids(
            user_text,
            scope_pairs=scope_pairs,
            statuses=list(ACTIVE_MEMORY_STATUSES),
            limit=max(64, MEMORY_DEFAULT_TOP_K * 8),
        )
        raw_rows = self._repo.get_memory_entries_by_ids(candidate_ids)
        # A checkpoint restore removes later turns from the active dialogue
        # lineage.  Memories sourced from those superseded turns must not leak
        # "future" facts back into the restored conversation.  Shared memories
        # from other conversations remain independent, user-controlled facts.
        raw_rows = [
            row
            for row in raw_rows
            if not (
                row.get("source_conversation_id") == conversation_id
                and row.get("source_turn_id")
                and not self._repo.is_turn_on_active_timeline(
                    conversation_id, str(row["source_turn_id"])
                )
            )
        ]
        ranked = self._rank_rows(raw_rows, set(terms_for_query(user_text)))[
            :MEMORY_DEFAULT_TOP_K
        ]
        entries = [MemoryEntry(**row) for row in ranked]
        return self._build_projection(
            entries=entries,
            turn_id=turn_id,
            workspace_key=workspace_key,
            conversation_id=conversation_id,
        )

    def turn_memory_usage(self, turn_id: str) -> List[Dict[str, Any]]:
        return self._repo.list_memory_usage(turn_id)

    def verify_index(self) -> bool:
        return self._repo.verify_memory_index()

    def extraction_input_is_safe(self, *texts: str) -> bool:
        try:
            for text in texts:
                self._policy.check_extraction_text(text)
        except MemoryPolicyError:
            return False
        return True

    def rebuild_index(self) -> None:
        self._repo.rebuild_memory_index()

    # ------------------------------------------------------------ switches

    def is_memory_enabled(
        self,
        *,
        conversation_id: Optional[str] = None,
        workspace_key: Optional[str] = None,
    ) -> bool:
        return self._memory_enabled_for(conversation_id, workspace_key)

    def set_memory_enabled(
        self,
        *,
        scope_type: str,
        scope_key: str,
        enabled: bool,
    ) -> Dict[str, Any]:
        self._validate_scope(scope_type, scope_key)
        key = f"memory_enabled:{scope_type}:{scope_key}"
        self._repo.set_memory_meta(key, "1" if enabled else "0")
        return {"scope_type": scope_type, "scope_key": scope_key, "enabled": enabled}

    def is_candidate_enabled(self) -> bool:
        value = self._repo.get_memory_meta("memory_candidate_enabled:global")
        return value == "1"

    def set_candidate_enabled(self, enabled: bool) -> Dict[str, Any]:
        self._repo.set_memory_meta(
            "memory_candidate_enabled:global", "1" if enabled else "0"
        )
        return {"candidate_enabled": enabled}

    def ingest_candidate_proposals(
        self,
        proposals: Sequence[Dict[str, Any]],
        *,
        conversation_id: str,
        turn_id: str,
        workspace_key: str,
    ) -> List[Dict[str, Any]]:
        """Persist validated proposals as candidates; failures are skipped."""
        if not self.is_candidate_enabled() or not self.is_memory_enabled(
            conversation_id=conversation_id, workspace_key=workspace_key
        ):
            return []
        created: List[Dict[str, Any]] = []
        for proposal in proposals:
            scope_type = str(proposal.get("scope_type", MemoryScope.WORKSPACE.value))
            if scope_type == MemoryScope.GLOBAL.value:
                scope_key = "global"
            elif scope_type == MemoryScope.CONVERSATION.value:
                scope_key = conversation_id
            else:
                scope_type = MemoryScope.WORKSPACE.value
                scope_key = workspace_key
            try:
                row = self.create_candidate(
                    scope_type=scope_type,
                    scope_key=scope_key,
                    kind=str(proposal.get("kind", MemoryKind.FACT.value)),
                    content=str(proposal.get("content", "")),
                    title=proposal.get("title"),
                    source_conversation_id=conversation_id,
                    source_turn_id=turn_id,
                    idempotency_key=(
                        f"candidate:{turn_id}:{scope_type}:"
                        f"{normalized_hash(str(proposal.get('content', '')))}"
                    ),
                )
                if all(item["id"] != row["id"] for item in created):
                    created.append(row)
            except MemoryServiceError:
                # A single bad proposal must not fail the whole extraction pass.
                continue
        return created

    # ------------------------------------------------------------ internals

    def _memory_enabled_for(
        self, conversation_id: Optional[str], workspace_key: Optional[str]
    ) -> bool:
        global_value = self._repo.get_memory_meta("memory_enabled:global:global")
        if global_value == "0":
            return False
        enabled = True
        if workspace_key:
            workspace_value = self._repo.get_memory_meta(
                f"memory_enabled:workspace:{workspace_key}"
            )
            if workspace_value is not None:
                enabled = workspace_value == "1"
        if conversation_id:
            conversation_value = self._repo.get_memory_meta(
                f"memory_enabled:conversation:{conversation_id}"
            )
            if conversation_value is not None:
                enabled = conversation_value == "1"
        return enabled

    def _build_projection(
        self,
        *,
        entries: Sequence[MemoryEntry],
        turn_id: str,
        workspace_key: str,
        conversation_id: str,
    ) -> MemoryProjection:
        chosen: List[MemoryEntry] = []
        omitted = 0
        seen_hashes: set[str] = set()
        rendered_lines: List[str] = []
        wrapper_chars = len(
            '<memory_context trust="untrusted_reference" injected_count="999">\n'
            "</memory_context>"
        )
        for entry in entries:
            if entry.normalized_hash and entry.normalized_hash in seen_hashes:
                omitted += 1
                continue
            if len(entry.content) > MEMORY_MAX_SINGLE_PROJECTION_CHARS:
                omitted += 1
                continue
            line = self._render_entry(entry)
            projected_chars = wrapper_chars + sum(map(len, rendered_lines))
            projected_chars += len(rendered_lines) + len(line)
            if projected_chars > MEMORY_MAX_TOTAL_PROJECTION_CHARS:
                omitted += 1
                continue
            chosen.append(entry)
            rendered_lines.append(line)
            if entry.normalized_hash:
                seen_hashes.add(entry.normalized_hash)
        snapshot_hash = hashlib.sha256(
            json.dumps(
                [(e.id, e.version, e.normalized_hash) for e in chosen],
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        block = self._render_block(chosen)

        def commit_usage() -> None:
            self._repo.record_memory_projection_usage(
                turn_id=turn_id,
                entries=[entry.to_dict() for entry in chosen],
                reason="active_confirmed_projection",
                snapshot_hash=snapshot_hash,
            )

        return MemoryProjection(
            block=block,
            entries=tuple(chosen),
            snapshot_hash=snapshot_hash,
            reason="active_confirmed_projection",
            omitted_count=omitted,
            commit_usage=commit_usage if chosen else None,
        )

    @staticmethod
    def _render_block(entries: Sequence[MemoryEntry]) -> str:
        if not entries:
            return ""
        lines = [
            '<memory_context trust="untrusted_reference" '
            f'injected_count="{len(entries)}">'
        ]
        for entry in entries:
            lines.append(MemoryService._render_entry(entry))
        lines.append("</memory_context>")
        return "\n".join(lines)

    @staticmethod
    def _render_entry(entry: MemoryEntry) -> str:
        source = "manual"
        if entry.source_conversation_id and entry.source_turn_id:
            source = (
                f"conversation/{entry.source_conversation_id}/"
                f"turn/{entry.source_turn_id}"
            )
        elif entry.source_conversation_id:
            source = f"conversation/{entry.source_conversation_id}"
        return (
            f'  <memory id="{html.escape(entry.id)}" '
            f'scope="{html.escape(entry.scope_type)}" '
            f'kind="{html.escape(entry.kind)}" '
            f'source="{html.escape(source)}">'
            f"{html.escape(entry.content)}</memory>"
        )

    @staticmethod
    def _rank_rows(
        rows: Sequence[Dict[str, Any]], query_terms: set[str]
    ) -> List[Dict[str, Any]]:
        def key(row: Dict[str, Any]) -> Tuple[Any, ...]:
            content_terms = set(tokenize(str(row.get("content", ""))))
            if query_terms:
                hit_ratio = len(query_terms & content_terms) / max(1, len(query_terms))
            else:
                hit_ratio = 0.0
            confirmation_priority = {
                MemoryConfirmation.EXPLICIT_UI.value: 3,
                MemoryConfirmation.EXPLICIT_COMMAND.value: 3,
                MemoryConfirmation.USER_APPROVED.value: 2,
                MemoryConfirmation.IMPORTED.value: 1,
            }.get(str(row.get("confirmation", "")), 0)
            try:
                updated = datetime.fromisoformat(
                    str(row.get("updated_at", "")).replace("Z", "+00:00")
                ).timestamp()
            except (TypeError, ValueError):
                updated = 0.0
            return (
                -_scope_priority(str(row.get("scope_type", ""))),
                -hit_ratio,
                -confirmation_priority,
                -int(row.get("version", 0)),
                -updated,
                str(row.get("id", "")),
            )

        return sorted(rows, key=key)

    @staticmethod
    def _validate_scope(scope_type: str, scope_key: str) -> None:
        try:
            MemoryScope(scope_type)
        except ValueError as exc:
            raise MemoryServiceError(
                "invalid_scope", "scope_type 必须是 global/workspace/conversation"
            ) from exc
        if not scope_key or not str(scope_key).strip():
            raise MemoryServiceError("invalid_scope_key", "scope_key 不能为空")
        if scope_type == MemoryScope.GLOBAL.value and scope_key != "global":
            raise MemoryServiceError(
                "invalid_scope_key", "global 作用域的 scope_key 必须为 global"
            )

    @staticmethod
    def _validate_kind(kind: str) -> None:
        try:
            MemoryKind(kind)
        except ValueError as exc:
            raise MemoryServiceError(
                "invalid_kind", "kind 必须是 preference/fact/decision/procedure"
            ) from exc

    def _require(self, entry_id: str) -> Dict[str, Any]:
        row = self._repo.get_memory_entry(entry_id)
        if row is None:
            raise MemoryServiceError("memory_not_found", "记忆不存在")
        return row

    def _idempotent_entry(
        self, idempotency_key: Optional[str], operation: str
    ) -> Optional[Dict[str, Any]]:
        result = self._repo.get_memory_idempotency_result(idempotency_key, operation)
        if result is None:
            return None
        target_id = str(result.get("target_id") or "")
        row = self._repo.get_memory_entry(target_id)
        if row is None:
            raise MemoryServiceError(
                "idempotency_target_deleted",
                "幂等操作的原结果已被删除，不能重建正文",
            )
        return row

    @staticmethod
    def _entry_dict(
        *,
        scope_type: str,
        scope_key: str,
        kind: str,
        content: str,
        title: Optional[str],
        status: str,
        confirmation: str,
        source_conversation_id: Optional[str],
        source_turn_id: Optional[str],
        source_excerpt: Optional[str],
    ) -> Dict[str, Any]:
        now = _utcnow()
        return {
            "id": _new_id(),
            "scope_type": scope_type,
            "scope_key": scope_key,
            "kind": kind,
            "title": title,
            "content": content,
            "status": status,
            "confirmation": confirmation,
            "source_conversation_id": source_conversation_id,
            "source_turn_id": source_turn_id,
            "source_excerpt": source_excerpt,
            "supersedes_id": None,
            "version": 1,
            "normalized_hash": normalized_hash(content),
            "created_at": now,
            "updated_at": now,
            "last_used_at": None,
            "use_count": 0,
        }
