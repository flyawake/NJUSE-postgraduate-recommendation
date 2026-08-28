"""SQLite conversation repository with explicit schema migration.

This is the single durable fact source for conversations/turns/canonical
history/public events in task_004. It is deliberately dependency-free
(standard library ``sqlite3`` only) and does not start threads or call models.
"""

from __future__ import annotations

import base64
import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..models import CanonicalMessage
from .domain import (
    CanonicalGroupRecord,
    CanonicalGroupState,
    ConversationRecord,
    PublicEventRecord,
    TurnRecord,
    canonical_message_to_payload,
    payload_to_canonical_message,
)

SCHEMA_VERSION = 3
DEFAULT_BUSY_TIMEOUT_MS = 5000

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_meta (
    version INTEGER NOT NULL,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    title_source TEXT NOT NULL DEFAULT 'auto',
    workspace_path TEXT NOT NULL,
    workspace_key TEXT NOT NULL,
    profile_id TEXT,
    state TEXT NOT NULL DEFAULT 'active'
        CHECK (state IN ('active', 'archived', 'deleted')),
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    last_activity_at TEXT NOT NULL,
    archived_at TEXT
);

CREATE TABLE IF NOT EXISTS conversation_events (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    event_seq INTEGER NOT NULL,
    kind TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (conversation_id, event_seq)
);

CREATE TABLE IF NOT EXISTS turns (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL,
    state TEXT NOT NULL
        CHECK (state IN ('pending', 'starting', 'running', 'success', 'error',
                         'interrupted', 'rejected')),
    run_id TEXT UNIQUE,
    user_text TEXT NOT NULL,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    result_json TEXT,
    error_code TEXT,
    idempotency_key TEXT,
    UNIQUE (conversation_id, ordinal)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_turns_one_active
    ON turns(conversation_id)
    WHERE state IN ('pending', 'starting', 'running');

CREATE UNIQUE INDEX IF NOT EXISTS idx_turns_idempotency
    ON turns(conversation_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS canonical_groups (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    turn_id TEXT NOT NULL REFERENCES turns(id) ON DELETE CASCADE,
    group_seq INTEGER NOT NULL,
    kind TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending'
        CHECK (state IN ('pending', 'committed', 'abandoned', 'recovered')),
    created_at TEXT NOT NULL,
    committed_at TEXT,
    UNIQUE (conversation_id, group_seq)
);

CREATE TABLE IF NOT EXISTS canonical_items (
    id TEXT PRIMARY KEY,
    group_id TEXT NOT NULL REFERENCES canonical_groups(id) ON DELETE CASCADE,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    turn_id TEXT NOT NULL REFERENCES turns(id) ON DELETE CASCADE,
    canonical_seq INTEGER NOT NULL,
    role TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (conversation_id, canonical_seq)
);

CREATE INDEX IF NOT EXISTS idx_canonical_items_group
    ON canonical_items(group_id, canonical_seq);

CREATE TABLE IF NOT EXISTS public_events (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    turn_id TEXT NOT NULL REFERENCES turns(id) ON DELETE CASCADE,
    run_id TEXT NOT NULL,
    event_seq INTEGER NOT NULL,
    kind TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (run_id, event_seq)
);

CREATE TABLE IF NOT EXISTS turn_change_sets (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    turn_id TEXT NOT NULL UNIQUE REFERENCES turns(id) ON DELETE CASCADE,
    status TEXT NOT NULL,
    additions INTEGER NOT NULL DEFAULT 0,
    deletions INTEGER NOT NULL DEFAULT 0,
    file_count INTEGER NOT NULL DEFAULT 0,
    coverage TEXT NOT NULL,
    finalized_at TEXT
);

CREATE TABLE IF NOT EXISTS turn_file_changes (
    id TEXT PRIMARY KEY,
    change_set_id TEXT NOT NULL REFERENCES turn_change_sets(id) ON DELETE CASCADE,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    turn_id TEXT NOT NULL REFERENCES turns(id) ON DELETE CASCADE,
    relative_path TEXT NOT NULL,
    old_relative_path TEXT,
    change_type TEXT NOT NULL,
    source TEXT NOT NULL,
    before_blob_id TEXT,
    after_blob_id TEXT,
    before_sha TEXT,
    after_sha TEXT,
    additions INTEGER NOT NULL DEFAULT 0,
    deletions INTEGER NOT NULL DEFAULT 0,
    binary INTEGER NOT NULL DEFAULT 0,
    preview_status TEXT NOT NULL DEFAULT 'available',
    warnings TEXT,
    UNIQUE (change_set_id, relative_path)
);

CREATE TABLE IF NOT EXISTS artifact_blobs (
    sha256 TEXT PRIMARY KEY,
    byte_count INTEGER NOT NULL,
    encoding TEXT NOT NULL,
    storage_path TEXT,
    created_at TEXT NOT NULL,
    reference_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS artifact_refs (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    turn_id TEXT NOT NULL REFERENCES turns(id) ON DELETE CASCADE,
    change_id TEXT,
    side TEXT,
    blob_id TEXT NOT NULL REFERENCES artifact_blobs(sha256) ON DELETE CASCADE,
    UNIQUE (blob_id, change_id, side)
);
"""


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cursor_token(activity: str, conversation_id: str) -> str:
    raw = json.dumps([activity, conversation_id], separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def _parse_cursor(cursor: str) -> Optional[Tuple[str, str]]:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii"))
        data = json.loads(raw.decode("utf-8"))
        if isinstance(data, list) and len(data) == 2:
            return str(data[0]), str(data[1])
    except Exception:
        return None
    return None


class SQLiteConversationRepository:
    """Thread-safe SQLite backend for conversation facts.

    A single connection is used with ``check_same_thread=False`` and all
    access is serialized by an RLock. WAL is enabled for file databases so a
    GUI worker thread can append events while HTTP reads snapshots.
    """

    def __init__(
        self,
        db_path: Path | str,
        *,
        busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
        create_backups: bool = True,
    ) -> None:
        self._db_path = Path(db_path)
        if self._db_path.parent and not self._db_path.parent.exists():
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._busy_timeout_ms = busy_timeout_ms
        self._create_backups = create_backups
        self._lock = threading.RLock()
        self._conn: Optional[sqlite3.Connection] = None

    # ------------------------------------------------------------ lifecycle

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            conn = sqlite3.connect(
                str(self._db_path),
                timeout=max(1, self._busy_timeout_ms / 1000.0),
                check_same_thread=False,
            )
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute(f"PRAGMA busy_timeout={self._busy_timeout_ms}")
            if str(self._db_path) != ":memory:":
                try:
                    conn.execute("PRAGMA journal_mode=WAL")
                except sqlite3.Error:
                    pass
            self._conn = conn
        return self._conn

    def initialize(self) -> None:
        """Create/migrate the schema. Versions only move forward."""
        with self._lock:
            conn = self._connect()
            existing = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_meta'"
            ).fetchone()
            current_version = 0
            if existing is not None:
                row = conn.execute(
                    "SELECT version FROM schema_meta ORDER BY version DESC LIMIT 1"
                ).fetchone()
                current_version = int(row["version"]) if row else 0
            if current_version == 0:
                if current_version == 0 and existing is not None:
                    # An older schema marked in metadata: keep an explicit
                    # backup before any upgrade, never silently rewrite it.
                    if self._create_backups:
                        self._backup_before_upgrade(current_version)
                conn.executescript(_SCHEMA_SQL)
                conn.execute("DELETE FROM schema_meta")
                conn.execute(
                    "INSERT INTO schema_meta(version, applied_at) VALUES (?, ?)",
                    (SCHEMA_VERSION, _utcnow()),
                )
                conn.commit()
            elif current_version < SCHEMA_VERSION:
                if self._create_backups:
                    self._backup_before_upgrade(current_version)
                try:
                    conn.execute("BEGIN IMMEDIATE")
                    if current_version == 1:
                        conn.execute(
                            "ALTER TABLE turns ADD COLUMN idempotency_key TEXT"
                        )
                    if current_version <= 2:
                        conn.execute(
                            """
                            CREATE TABLE IF NOT EXISTS conversation_events (
                                id TEXT PRIMARY KEY,
                                conversation_id TEXT NOT NULL
                                    REFERENCES conversations(id) ON DELETE CASCADE,
                                event_seq INTEGER NOT NULL,
                                kind TEXT NOT NULL,
                                payload_json TEXT NOT NULL,
                                created_at TEXT NOT NULL,
                                UNIQUE (conversation_id, event_seq)
                            )
                            """
                        )
                        for row in conn.execute(
                            "SELECT * FROM conversations ORDER BY created_at, id"
                        ).fetchall():
                            self._append_conversation_event(
                                conn,
                                str(row["id"]),
                                "migration_snapshot",
                                {
                                    "title": str(row["title"]),
                                    "title_source": str(row["title_source"]),
                                    "state": str(row["state"]),
                                    "version": int(row["version"]),
                                    "last_activity_at": str(row["last_activity_at"]),
                                },
                                str(row["created_at"]),
                            )
                        conn.execute(
                            """
                            CREATE UNIQUE INDEX IF NOT EXISTS idx_turns_idempotency
                            ON turns(conversation_id, idempotency_key)
                            WHERE idempotency_key IS NOT NULL
                            """
                        )
                    conn.execute("DELETE FROM schema_meta")
                    conn.execute(
                        "INSERT INTO schema_meta(version, applied_at) VALUES (?, ?)",
                        (SCHEMA_VERSION, _utcnow()),
                    )
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise
            elif current_version > SCHEMA_VERSION:
                raise RuntimeError(
                    f"database schema version {current_version} is newer than "
                    f"supported version {SCHEMA_VERSION}"
                )

    def backup_to(self, destination: Path) -> Path:
        """Create a consistent SQLite backup at ``destination``."""
        with self._lock:
            conn = self._connect()
            dest = Path(destination)
            dest.parent.mkdir(parents=True, exist_ok=True)
            target = sqlite3.connect(str(dest))
            try:
                conn.backup(target)
            finally:
                target.close()
            return dest

    def _backup_before_upgrade(self, from_version: int) -> None:
        if not self._db_path.exists() or str(self._db_path) == ":memory:":
            return
        stamp = _utcnow().replace(":", "-").replace(".", "-")
        backup_dir = self._db_path.parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        self.backup_to(backup_dir / f"state-v{from_version}-{stamp}.db")

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def list_artifact_blob_ids(self) -> set[str]:
        with self._lock:
            rows = (
                self._connect().execute("SELECT sha256 FROM artifact_blobs").fetchall()
            )
        return {str(row["sha256"]) for row in rows}

    @staticmethod
    def _append_conversation_event(
        conn: sqlite3.Connection,
        conversation_id: str,
        kind: str,
        payload: Dict[str, Any],
        created_at: Optional[str] = None,
    ) -> None:
        seq = int(
            conn.execute(
                """
                SELECT COALESCE(MAX(event_seq), 0) + 1 AS value
                FROM conversation_events WHERE conversation_id=?
                """,
                (conversation_id,),
            ).fetchone()["value"]
        )
        conn.execute(
            """
            INSERT INTO conversation_events(
                id, conversation_id, event_seq, kind, payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                uuid.uuid4().hex,
                conversation_id,
                seq,
                kind,
                json.dumps(payload, ensure_ascii=False, sort_keys=True),
                created_at or _utcnow(),
            ),
        )

    # ------------------------------------------------------------ conversations

    def create_conversation(
        self,
        *,
        workspace_path: str,
        workspace_key: str,
        profile_id: Optional[str],
        title: str,
        title_source: str = "auto",
    ) -> ConversationRecord:
        conversation_id = uuid.uuid4().hex
        now = _utcnow()
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT INTO conversations(
                        id, title, title_source, workspace_path, workspace_key,
                        profile_id, state, version, created_at, last_activity_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 'active', 1, ?, ?)
                    """,
                    (
                        conversation_id,
                        title,
                        title_source,
                        workspace_path,
                        workspace_key,
                        profile_id,
                        now,
                        now,
                    ),
                )
                self._append_conversation_event(
                    conn,
                    conversation_id,
                    "created",
                    {
                        "title": title,
                        "title_source": title_source,
                        "state": "active",
                        "version": 1,
                        "last_activity_at": now,
                    },
                    now,
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        record = self.get_conversation(conversation_id)
        assert record is not None
        return record

    def get_conversation(self, conversation_id: str) -> Optional[ConversationRecord]:
        with self._lock:
            conn = self._connect()
            row = conn.execute(
                "SELECT * FROM conversations WHERE id = ? AND state != 'deleted'",
                (conversation_id,),
            ).fetchone()
        return self._row_to_conversation(row) if row else None

    def list_conversations(
        self,
        *,
        archived: Optional[bool] = None,
        query: Optional[str] = None,
        limit: int = 50,
        cursor: Optional[str] = None,
    ) -> Tuple[List[ConversationRecord], Optional[str]]:
        if limit < 1 or limit > 100:
            raise ValueError("limit must be between 1 and 100")
        clauses: List[str] = []
        params: List[Any] = []
        if archived is True:
            clauses.append("state = 'archived'")
        elif archived is False:
            clauses.append("state = 'active'")
        if query:
            clauses.append(
                "(title LIKE ? OR workspace_key LIKE ? OR workspace_path LIKE ?)"
            )
            like = f"%{query}%"
            params.extend([like, like, like])
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        order = "ORDER BY last_activity_at DESC, id DESC"
        limit_clause = "LIMIT ?"
        if cursor:
            parsed_cursor = _parse_cursor(cursor)
            if parsed_cursor is None:
                raise ValueError("invalid_cursor")
            activity, cid = parsed_cursor
            where = (where + " AND " if where else "WHERE ") + (
                "(last_activity_at < ? OR (last_activity_at = ? AND id < ?))"
            )
            params.extend([activity, activity, cid])
        with self._lock:
            conn = self._connect()
            rows = conn.execute(
                f"SELECT * FROM conversations {where} {order} {limit_clause}",
                [*params, limit + 1],
            ).fetchall()
        has_more = len(rows) > limit
        records = [self._row_to_conversation(row) for row in rows[:limit]]
        next_cursor = None
        if has_more and records:
            last = records[-1]
            next_cursor = _cursor_token(last.last_activity_at, last.id)
        return records, next_cursor

    def rename_conversation(
        self, conversation_id: str, *, title: str, expected_version: int
    ) -> ConversationRecord:
        with self._lock:
            conn = self._connect()
            current = conn.execute(
                "SELECT * FROM conversations WHERE id = ? AND state != 'deleted'",
                (conversation_id,),
            ).fetchone()
            if current is None:
                raise KeyError("conversation_not_found")
            if int(current["version"]) != expected_version:
                raise ValueError("version_conflict")
            next_version = int(current["version"]) + 1
            try:
                conn.execute(
                    """
                    UPDATE conversations SET title=?, title_source='manual', version=version+1
                    WHERE id=?
                    """,
                    (title, conversation_id),
                )
                self._append_conversation_event(
                    conn,
                    conversation_id,
                    "renamed",
                    {"title": title, "title_source": "manual", "version": next_version},
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        record = self.get_conversation(conversation_id)
        assert record is not None
        return record

    def set_auto_title(self, conversation_id: str, title: str) -> ConversationRecord:
        with self._lock:
            conn = self._connect()
            try:
                cursor = conn.execute(
                    """
                    UPDATE conversations SET title=?, title_source='auto'
                    WHERE id=? AND title_source='auto'
                    """,
                    (title, conversation_id),
                )
                if cursor.rowcount:
                    self._append_conversation_event(
                        conn,
                        conversation_id,
                        "auto_title",
                        {"title": title, "title_source": "auto"},
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        record = self.get_conversation(conversation_id)
        assert record is not None
        return record

    def set_conversation_state(
        self, conversation_id: str, *, state: str, expected_version: int
    ) -> ConversationRecord:
        with self._lock:
            conn = self._connect()
            current = conn.execute(
                "SELECT * FROM conversations WHERE id = ? AND state != 'deleted'",
                (conversation_id,),
            ).fetchone()
            if current is None:
                raise KeyError("conversation_not_found")
            if int(current["version"]) != expected_version:
                raise ValueError("version_conflict")
            archived_at = _utcnow() if state == "archived" else None
            next_version = int(current["version"]) + 1
            try:
                conn.execute(
                    """
                    UPDATE conversations
                    SET state=?, version=version+1, archived_at=?
                    WHERE id=?
                    """,
                    (state, archived_at, conversation_id),
                )
                self._append_conversation_event(
                    conn,
                    conversation_id,
                    "state_changed",
                    {"state": state, "version": next_version},
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        record = self.get_conversation(conversation_id)
        assert record is not None
        return record

    def delete_conversation(
        self, conversation_id: str, expected_version: int
    ) -> List[str]:
        with self._lock:
            conn = self._connect()
            current = conn.execute(
                "SELECT * FROM conversations WHERE id = ? AND state != 'deleted'",
                (conversation_id,),
            ).fetchone()
            if current is None:
                raise KeyError("conversation_not_found")
            if int(current["version"]) != expected_version:
                raise ValueError("version_conflict")
            candidates = [
                str(row["blob_id"])
                for row in conn.execute(
                    "SELECT DISTINCT blob_id FROM artifact_refs WHERE conversation_id=?",
                    (conversation_id,),
                ).fetchall()
            ]
            conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
            orphaned: List[str] = []
            for blob_id in candidates:
                refs = conn.execute(
                    "SELECT COUNT(*) AS value FROM artifact_refs WHERE blob_id=?",
                    (blob_id,),
                ).fetchone()
                if int(refs["value"]) == 0:
                    conn.execute(
                        "DELETE FROM artifact_blobs WHERE sha256=?", (blob_id,)
                    )
                    orphaned.append(blob_id)
                else:
                    conn.execute(
                        """
                        UPDATE artifact_blobs SET reference_count=? WHERE sha256=?
                        """,
                        (int(refs["value"]), blob_id),
                    )
            conn.commit()
            return orphaned

    # ------------------------------------------------------------ turns

    def create_turn(
        self, conversation_id: str, *, user_text: str, run_id: str
    ) -> TurnRecord:
        turn_id = uuid.uuid4().hex
        now = _utcnow()
        with self._lock:
            conn = self._connect()
            row = conn.execute(
                "SELECT COALESCE(MAX(ordinal), 0) AS max_ord FROM turns WHERE conversation_id=?",
                (conversation_id,),
            ).fetchone()
            ordinal = int(row["max_ord"]) + 1
            conn.execute(
                """
                INSERT INTO turns(
                    id, conversation_id, ordinal, state, run_id, user_text, created_at
                ) VALUES (?, ?, ?, 'pending', ?, ?, ?)
                """,
                (turn_id, conversation_id, ordinal, run_id, user_text, now),
            )
            conn.execute(
                "UPDATE conversations SET last_activity_at=? WHERE id=?",
                (now, conversation_id),
            )
            self._append_conversation_event(
                conn,
                conversation_id,
                "turn_started",
                {"turn_id": turn_id, "ordinal": ordinal, "last_activity_at": now},
                now,
            )
            conn.commit()
        record = self.get_turn(conversation_id, turn_id)
        assert record is not None
        return record

    def get_turn_by_idempotency(
        self, conversation_id: str, idempotency_key: str
    ) -> Optional[TurnRecord]:
        with self._lock:
            row = (
                self._connect()
                .execute(
                    """
                SELECT * FROM turns
                WHERE conversation_id=? AND idempotency_key=?
                """,
                    (conversation_id, idempotency_key),
                )
                .fetchone()
            )
        return self._row_to_turn(row) if row else None

    def create_turn_with_initial_messages(
        self,
        conversation_id: str,
        *,
        user_text: str,
        run_id: str,
        idempotency_key: Optional[str],
        messages: Sequence[CanonicalMessage],
    ) -> Tuple[TurnRecord, bool]:
        """Atomically create a turn and its committed initial canonical group.

        The durable idempotency key is checked again inside ``BEGIN
        IMMEDIATE`` so concurrent HTTP requests and process restarts resolve
        to one turn. No active turn can exist without its initial canonical
        facts after this transaction commits.
        """

        turn_id = uuid.uuid4().hex
        group_id = uuid.uuid4().hex
        now = _utcnow()
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                if idempotency_key:
                    existing = conn.execute(
                        """
                        SELECT * FROM turns
                        WHERE conversation_id=? AND idempotency_key=?
                        """,
                        (conversation_id, idempotency_key),
                    ).fetchone()
                    if existing is not None:
                        conn.commit()
                        return self._row_to_turn(existing), False
                active = conn.execute(
                    """
                    SELECT 1 FROM turns WHERE conversation_id=?
                    AND state IN ('pending', 'starting', 'running')
                    """,
                    (conversation_id,),
                ).fetchone()
                if active is not None:
                    raise ValueError("conversation_busy")
                ordinal = (
                    int(
                        conn.execute(
                            """
                        SELECT COALESCE(MAX(ordinal), 0) AS value
                        FROM turns WHERE conversation_id=?
                        """,
                            (conversation_id,),
                        ).fetchone()["value"]
                    )
                    + 1
                )
                conn.execute(
                    """
                    INSERT INTO turns(
                        id, conversation_id, ordinal, state, run_id,
                        user_text, created_at, started_at, idempotency_key
                    ) VALUES (?, ?, ?, 'starting', ?, ?, ?, ?, ?)
                    """,
                    (
                        turn_id,
                        conversation_id,
                        ordinal,
                        run_id,
                        user_text,
                        now,
                        now,
                        idempotency_key,
                    ),
                )
                group_seq = (
                    int(
                        conn.execute(
                            """
                        SELECT COALESCE(MAX(group_seq), 0) AS value
                        FROM canonical_groups WHERE conversation_id=?
                        """,
                            (conversation_id,),
                        ).fetchone()["value"]
                    )
                    + 1
                )
                conn.execute(
                    """
                    INSERT INTO canonical_groups(
                        id, conversation_id, turn_id, group_seq, kind,
                        state, created_at, committed_at
                    ) VALUES (?, ?, ?, ?, 'turn_input', 'committed', ?, ?)
                    """,
                    (group_id, conversation_id, turn_id, group_seq, now, now),
                )
                next_seq = (
                    int(
                        conn.execute(
                            """
                        SELECT COALESCE(MAX(canonical_seq), 0) AS value
                        FROM canonical_items WHERE conversation_id=?
                        """,
                            (conversation_id,),
                        ).fetchone()["value"]
                    )
                    + 1
                )
                for offset, message in enumerate(messages):
                    payload = canonical_message_to_payload(message)
                    conn.execute(
                        """
                        INSERT INTO canonical_items(
                            id, group_id, conversation_id, turn_id,
                            canonical_seq, role, payload_json, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            uuid.uuid4().hex,
                            group_id,
                            conversation_id,
                            turn_id,
                            next_seq + offset,
                            payload.get("type", ""),
                            json.dumps(payload, ensure_ascii=False, sort_keys=True),
                            now,
                        ),
                    )
                conn.execute(
                    "UPDATE conversations SET last_activity_at=? WHERE id=?",
                    (now, conversation_id),
                )
                self._append_conversation_event(
                    conn,
                    conversation_id,
                    "turn_started",
                    {
                        "turn_id": turn_id,
                        "ordinal": ordinal,
                        "last_activity_at": now,
                    },
                    now,
                )
                row = conn.execute(
                    "SELECT * FROM turns WHERE id=?", (turn_id,)
                ).fetchone()
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        assert row is not None
        return self._row_to_turn(row), True

    def verify_conversation_projection(self, conversation_id: str) -> bool:
        """Validate mutable list fields against the append-only lifecycle log."""

        with self._lock:
            conn = self._connect()
            current = conn.execute(
                "SELECT * FROM conversations WHERE id=?", (conversation_id,)
            ).fetchone()
            rows = conn.execute(
                """
                SELECT kind, payload_json FROM conversation_events
                WHERE conversation_id=? ORDER BY event_seq
                """,
                (conversation_id,),
            ).fetchall()
        if current is None or not rows:
            return False
        projected: Dict[str, Any] = {}
        try:
            for row in rows:
                payload = json.loads(row["payload_json"])
                kind = str(row["kind"])
                if kind in {"created", "migration_snapshot"}:
                    projected.update(payload)
                elif kind in {"renamed", "auto_title"}:
                    projected["title"] = payload["title"]
                    projected["title_source"] = payload["title_source"]
                    if "version" in payload:
                        projected["version"] = payload["version"]
                elif kind == "state_changed":
                    projected["state"] = payload["state"]
                    projected["version"] = payload["version"]
                elif kind == "turn_started":
                    projected["last_activity_at"] = payload["last_activity_at"]
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return False
        return all(
            projected.get(key) == current[key]
            for key in (
                "title",
                "title_source",
                "state",
                "version",
                "last_activity_at",
            )
        )

    def get_turn(self, conversation_id: str, turn_id: str) -> Optional[TurnRecord]:
        with self._lock:
            conn = self._connect()
            row = conn.execute(
                "SELECT * FROM turns WHERE id=? AND conversation_id=?",
                (turn_id, conversation_id),
            ).fetchone()
            return self._row_to_turn(row) if row else None

    def get_active_turn(self, conversation_id: str) -> Optional[TurnRecord]:
        with self._lock:
            conn = self._connect()
            row = conn.execute(
                """
                SELECT * FROM turns WHERE conversation_id=?
                  AND state IN ('pending', 'starting', 'running')
                ORDER BY ordinal DESC LIMIT 1
                """,
                (conversation_id,),
            ).fetchone()
            return self._row_to_turn(row) if row else None

    def get_latest_turn(self, conversation_id: str) -> Optional[TurnRecord]:
        with self._lock:
            row = (
                self._connect()
                .execute(
                    """
                SELECT * FROM turns WHERE conversation_id=?
                ORDER BY ordinal DESC LIMIT 1
                """,
                    (conversation_id,),
                )
                .fetchone()
            )
        return self._row_to_turn(row) if row else None

    def list_turns(
        self,
        conversation_id: str,
        *,
        limit: int = 50,
        cursor: Optional[str] = None,
    ) -> Tuple[List[TurnRecord], Optional[str]]:
        if limit < 1 or limit > 100:
            raise ValueError("invalid_limit")
        clauses = ["conversation_id=?"]
        params: List[Any] = [conversation_id]
        if cursor:
            try:
                payload = json.loads(
                    base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
                )
                ordinal = int(payload)
            except Exception as exc:
                raise ValueError("invalid_cursor") from exc
            clauses.append("ordinal < ?")
            params.append(ordinal)
        with self._lock:
            conn = self._connect()
            rows = conn.execute(
                f"SELECT * FROM turns WHERE {' AND '.join(clauses)} "
                "ORDER BY ordinal DESC LIMIT ?",
                [*params, limit + 1],
            ).fetchall()
        has_more = len(rows) > limit
        records = [self._row_to_turn(row) for row in rows[:limit]]
        next_cursor = None
        if has_more and records:
            next_cursor = base64.urlsafe_b64encode(
                str(records[-1].ordinal).encode("ascii")
            ).decode("ascii")
        return records, next_cursor

    def update_turn_state(
        self,
        conversation_id: str,
        turn_id: str,
        *,
        state: str,
        run_id: Optional[str] = None,
        started_at: Optional[str] = None,
    ) -> TurnRecord:
        now = _utcnow()
        with self._lock:
            conn = self._connect()
            current = conn.execute(
                "SELECT * FROM turns WHERE id=? AND conversation_id=?",
                (turn_id, conversation_id),
            ).fetchone()
            if current is None:
                raise KeyError("turn_not_found")
            conn.execute(
                """
                UPDATE turns SET state=?, started_at=COALESCE(?, started_at),
                    run_id=COALESCE(?, run_id)
                WHERE id=? AND conversation_id=?
                """,
                (state, started_at, run_id, turn_id, conversation_id),
            )
            if state in ("success", "error", "interrupted", "rejected"):
                conn.execute(
                    "UPDATE turns SET finished_at=? WHERE id=?",
                    (now, turn_id),
                )
            conn.commit()
        record = self.get_turn(conversation_id, turn_id)
        assert record is not None
        return record

    def set_turn_terminal(
        self,
        conversation_id: str,
        turn_id: str,
        *,
        state: str,
        result_json: Optional[str],
        error_code: Optional[str] = None,
    ) -> TurnRecord:
        now = _utcnow()
        with self._lock:
            conn = self._connect()
            current = conn.execute(
                "SELECT * FROM turns WHERE id=? AND conversation_id=?",
                (turn_id, conversation_id),
            ).fetchone()
            if current is None:
                raise KeyError("turn_not_found")
            if current["state"] in ("success", "error", "interrupted"):
                raise ValueError("turn_already_terminal")
            cursor = conn.execute(
                """
                UPDATE turns SET state=?, finished_at=?, result_json=?, error_code=?
                WHERE id=? AND conversation_id=?
                  AND state IN ('starting', 'running', 'pending')
                """,
                (state, now, result_json, error_code, turn_id, conversation_id),
            )
            if cursor.rowcount == 0:
                raise ValueError("turn_already_terminal")
            conn.commit()
        record = self.get_turn(conversation_id, turn_id)
        assert record is not None
        return record

    def recover_active_turns(self) -> List[TurnRecord]:
        """Mark every starting/running turn as interrupted after restart."""
        recovered: List[TurnRecord] = []
        with self._lock:
            conn = self._connect()
            rows = conn.execute(
                "SELECT * FROM turns WHERE state IN ('starting', 'running')"
            ).fetchall()
            for row in rows:
                turn_id = str(row["id"])
                conversation_id = str(row["conversation_id"])
                now = _utcnow()
                conn.execute(
                    """
                    UPDATE turns SET state='interrupted', finished_at=?,
                        result_json=?, error_code='PROCESS_RESTARTED'
                    WHERE id=? AND state IN ('starting', 'running')
                    """,
                    (
                        now,
                        json.dumps(
                            {
                                "status": "INTERRUPTED",
                                "stop_reason": "PROCESS_RESTARTED",
                                "verification_status": "NOT_RUN",
                            }
                        ),
                        turn_id,
                    ),
                )
                recovered.append(
                    TurnRecord(
                        id=turn_id,
                        conversation_id=conversation_id,
                        ordinal=int(row["ordinal"]),
                        state="interrupted",
                        run_id=row["run_id"],
                        user_text=str(row["user_text"]),
                        created_at=str(row["created_at"]),
                        started_at=row["started_at"],
                        finished_at=now,
                        result_json=json.dumps(
                            {
                                "status": "INTERRUPTED",
                                "stop_reason": "PROCESS_RESTARTED",
                                "verification_status": "NOT_RUN",
                            }
                        ),
                        error_code="PROCESS_RESTARTED",
                    )
                )
            conn.commit()
        return recovered

    # ------------------------------------------------------------ canonical

    def begin_canonical_group(
        self, conversation_id: str, turn_id: str, *, kind: str
    ) -> Tuple[str, int]:
        group_id = uuid.uuid4().hex
        now = _utcnow()
        with self._lock:
            conn = self._connect()
            row = conn.execute(
                "SELECT COALESCE(MAX(group_seq), 0) AS max_seq FROM canonical_groups WHERE conversation_id=?",
                (conversation_id,),
            ).fetchone()
            group_seq = int(row["max_seq"]) + 1
            conn.execute(
                """
                INSERT INTO canonical_groups(
                    id, conversation_id, turn_id, group_seq, kind, state, created_at
                ) VALUES (?, ?, ?, ?, ?, 'pending', ?)
                """,
                (group_id, conversation_id, turn_id, group_seq, kind, now),
            )
            conn.commit()
        return group_id, group_seq

    def append_canonical_item(
        self,
        conversation_id: str,
        turn_id: str,
        group_id: str,
        canonical_seq: int,
        message: CanonicalMessage,
    ) -> None:
        now = _utcnow()
        payload = canonical_message_to_payload(message)
        with self._lock:
            conn = self._connect()
            conn.execute(
                """
                INSERT INTO canonical_items(
                    id, group_id, conversation_id, turn_id, canonical_seq,
                    role, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    uuid.uuid4().hex,
                    group_id,
                    conversation_id,
                    turn_id,
                    canonical_seq,
                    payload.get("type", ""),
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    now,
                ),
            )
            conn.commit()

    def commit_canonical_group(
        self, group_id: str, *, state: str = CanonicalGroupState.COMMITTED.value
    ) -> None:
        with self._lock:
            conn = self._connect()
            conn.execute(
                """
                UPDATE canonical_groups SET state=?, committed_at=?
                WHERE id=?
                """,
                (state, _utcnow(), group_id),
            )
            conn.commit()

    def get_open_canonical_group(
        self, conversation_id: str, turn_id: str
    ) -> Optional[CanonicalGroupRecord]:
        with self._lock:
            conn = self._connect()
            row = conn.execute(
                """
                SELECT * FROM canonical_groups
                WHERE conversation_id=? AND turn_id=? AND state='pending'
                ORDER BY group_seq DESC LIMIT 1
                """,
                (conversation_id, turn_id),
            ).fetchone()
            return self._row_to_group(row) if row else None

    def get_group_items(self, group_id: str) -> List[Tuple[int, CanonicalMessage]]:
        with self._lock:
            conn = self._connect()
            rows = conn.execute(
                """
                SELECT canonical_seq, payload_json FROM canonical_items
                WHERE group_id=? ORDER BY canonical_seq
                """,
                (group_id,),
            ).fetchall()
        items: List[Tuple[int, CanonicalMessage]] = []
        for row in rows:
            try:
                payload = json.loads(row["payload_json"])
                items.append(
                    (int(row["canonical_seq"]), payload_to_canonical_message(payload))
                )
            except (ValueError, TypeError):
                # fail-closed: a corrupt canonical payload must surface as
                # data_error rather than silently skip history.
                raise ValueError("data_error")
        return items

    def get_canonical_history(self, conversation_id: str) -> List[CanonicalMessage]:
        with self._lock:
            conn = self._connect()
            rows = conn.execute(
                """
                SELECT ci.canonical_seq, ci.payload_json
                FROM canonical_items ci
                JOIN canonical_groups cg ON cg.id = ci.group_id
                WHERE ci.conversation_id=? AND cg.state IN ('committed', 'recovered')
                ORDER BY ci.canonical_seq
                """,
                (conversation_id,),
            ).fetchall()
        messages: List[CanonicalMessage] = []
        for row in rows:
            try:
                payload = json.loads(row["payload_json"])
                messages.append(payload_to_canonical_message(payload))
            except (ValueError, TypeError) as exc:
                raise ValueError("data_error") from exc
        return messages

    def next_canonical_seq(self, conversation_id: str) -> int:
        with self._lock:
            conn = self._connect()
            row = conn.execute(
                "SELECT COALESCE(MAX(canonical_seq), 0) AS max_seq FROM canonical_items WHERE conversation_id=?",
                (conversation_id,),
            ).fetchone()
            return int(row["max_seq"]) + 1

    def recover_pending_groups_for_turn(
        self, conversation_id: str, turn_id: str
    ) -> int:
        """Turn a pending tool group into a recovered, paired group.

        For any tool call without a stored result we append a deterministic
        synthetic result that says the execution outcome is unknown and the
        agent must re-observe the workspace. The group is then committed as
        ``recovered`` so subsequent turns can never send an unpaired call to
        a provider.
        """
        import json as _json

        from ..models import AssistantMessage, ToolMessage

        now = _utcnow()
        recovered_count = 0
        with self._lock:
            conn = self._connect()
            rows = conn.execute(
                """
                SELECT id FROM canonical_groups
                WHERE conversation_id=? AND turn_id=? AND state='pending'
                ORDER BY group_seq
                """,
                (conversation_id, turn_id),
            ).fetchall()
            for row in rows:
                group_id = str(row["id"])
                items = self.get_group_items(group_id)
                calls: dict[str, str] = {}
                received: set[str] = set()
                for _seq, message in items:
                    if isinstance(message, AssistantMessage):
                        for call in message.tool_calls:
                            if call.id:
                                calls[call.id] = call.name
                    elif isinstance(message, ToolMessage) and message.tool_call_id:
                        received.add(message.tool_call_id)
                for call_id, tool_name in calls.items():
                    if call_id in received:
                        continue
                    payload = {
                        "ok": False,
                        "error": {
                            "code": "PROCESS_RESTARTED",
                            "message": (
                                "执行结果未知：进程在上一次工具调用中恢复；"
                                "请重新观察工作区后再继续"
                            ),
                            "retryable": False,
                        },
                    }
                    synthetic = ToolMessage(
                        tool_call_id=call_id,
                        content=_json.dumps(
                            payload, ensure_ascii=False, sort_keys=True
                        ),
                        tool_name=tool_name,
                        ok=False,
                        resource_key=tool_name,
                        is_read_success=False,
                        file_path=None,
                    )
                    seq = self.next_canonical_seq(conversation_id)
                    self.append_canonical_item(
                        conversation_id,
                        turn_id,
                        group_id,
                        seq,
                        synthetic,
                    )
                    recovered_count += 1
                conn.execute(
                    "UPDATE canonical_groups SET state='recovered', committed_at=? WHERE id=?",
                    (now, group_id),
                )
            conn.commit()
        return recovered_count

    def abandon_pending_groups_for_turn(
        self, conversation_id: str, turn_id: str
    ) -> None:
        with self._lock:
            conn = self._connect()
            conn.execute(
                """
                UPDATE canonical_groups SET state='abandoned'
                WHERE conversation_id=? AND turn_id=? AND state='pending'
                """,
                (conversation_id, turn_id),
            )
            conn.commit()

    def abandon_groups_for_rejected_turn(
        self, conversation_id: str, turn_id: str
    ) -> None:
        with self._lock:
            conn = self._connect()
            conn.execute(
                """
                UPDATE canonical_groups SET state='abandoned'
                WHERE conversation_id=? AND turn_id=?
                """,
                (conversation_id, turn_id),
            )
            conn.commit()

    # ------------------------------------------------------------ public events

    def append_public_event(
        self,
        *,
        conversation_id: str,
        turn_id: str,
        run_id: str,
        event_seq: int,
        kind: str,
        payload: Dict[str, Any],
    ) -> PublicEventRecord:
        event_id = uuid.uuid4().hex
        now = _utcnow()
        with self._lock:
            conn = self._connect()
            conn.execute(
                """
                INSERT INTO public_events(
                    id, conversation_id, turn_id, run_id, event_seq,
                    kind, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    conversation_id,
                    turn_id,
                    run_id,
                    event_seq,
                    kind,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    now,
                ),
            )
            conn.commit()
        return PublicEventRecord(
            id=event_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            run_id=run_id,
            event_seq=event_seq,
            kind=kind,
            payload_json="",
            created_at=now,
        )

    def list_public_events(
        self,
        *,
        conversation_id: str,
        turn_id: str,
        after_seq: int = 0,
        limit: int = 500,
    ) -> List[Dict[str, Any]]:
        with self._lock:
            conn = self._connect()
            rows = conn.execute(
                """
                SELECT event_seq, kind, payload_json, created_at FROM public_events
                WHERE conversation_id=? AND turn_id=? AND event_seq>?
                ORDER BY event_seq LIMIT ?
                """,
                (conversation_id, turn_id, after_seq, limit),
            ).fetchall()
        out: List[Dict[str, Any]] = []
        for row in rows:
            try:
                event = json.loads(row["payload_json"])
            except (TypeError, ValueError) as exc:
                raise ValueError("data_error") from exc
            if not isinstance(event, dict) or not isinstance(
                event.get("payload"), dict
            ):
                raise ValueError("data_error")
            payload = dict(event["payload"])
            target = payload.pop("target", None)
            out.append(
                {
                    "id": int(row["event_seq"]),
                    "kind": str(row["kind"]),
                    "step": int(event.get("step", 0)),
                    "phase": str(event.get("phase", "READY")),
                    "target": target if isinstance(target, str) else None,
                    "payload": payload,
                    "created_at": str(row["created_at"]),
                }
            )
        return out

    # ------------------------------------------------------------ change sets

    def save_change_set(
        self,
        *,
        change_set_id: str,
        conversation_id: str,
        turn_id: str,
        status: str,
        additions: int,
        deletions: int,
        file_count: int,
        coverage: str,
        files: Sequence[Dict[str, Any]],
    ) -> List[str]:
        now = _utcnow()
        with self._lock:
            conn = self._connect()
            conn.execute(
                """
                INSERT INTO turn_change_sets(
                    id, conversation_id, turn_id, status, additions, deletions,
                    file_count, coverage, finalized_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(turn_id) DO UPDATE SET
                    status=excluded.status,
                    additions=excluded.additions,
                    deletions=excluded.deletions,
                    file_count=excluded.file_count,
                    coverage=excluded.coverage,
                    finalized_at=excluded.finalized_at
                """,
                (
                    change_set_id,
                    conversation_id,
                    turn_id,
                    status,
                    additions,
                    deletions,
                    file_count,
                    coverage,
                    now,
                ),
            )
            prior_change_rows = conn.execute(
                "SELECT id FROM turn_file_changes WHERE change_set_id=?",
                (change_set_id,),
            ).fetchall()
            prior_change_ids = [str(row["id"]) for row in prior_change_rows]
            previous_change_blobs: List[str] = []
            for prior_change_id in prior_change_ids:
                previous_change_blobs.extend(
                    str(row["blob_id"])
                    for row in conn.execute(
                        "SELECT blob_id FROM artifact_refs WHERE change_id=?",
                        (prior_change_id,),
                    ).fetchall()
                )
                conn.execute(
                    "DELETE FROM artifact_refs WHERE change_id=?", (prior_change_id,)
                )
            conn.execute(
                "DELETE FROM turn_file_changes WHERE change_set_id=?",
                (change_set_id,),
            )
            current_change_blobs: List[str] = []
            for item in files:
                proposed_change_id = uuid.uuid4().hex
                conn.execute(
                    """
                    INSERT INTO turn_file_changes(
                        id, change_set_id, conversation_id, turn_id, relative_path,
                        old_relative_path, change_type, source, before_blob_id,
                        after_blob_id, before_sha, after_sha, additions,
                        deletions, binary, preview_status, warnings
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(change_set_id, relative_path) DO UPDATE SET
                        change_type=excluded.change_type,
                        source=excluded.source,
                        before_blob_id=excluded.before_blob_id,
                        after_blob_id=excluded.after_blob_id,
                        before_sha=excluded.before_sha,
                        after_sha=excluded.after_sha,
                        additions=excluded.additions,
                        deletions=excluded.deletions,
                        binary=excluded.binary,
                        preview_status=excluded.preview_status,
                        warnings=excluded.warnings
                    """,
                    (
                        proposed_change_id,
                        change_set_id,
                        conversation_id,
                        turn_id,
                        item["relative_path"],
                        item.get("old_relative_path"),
                        item["change_type"],
                        item["source"],
                        item.get("before_blob_id"),
                        item.get("after_blob_id"),
                        item.get("before_sha"),
                        item.get("after_sha"),
                        int(item.get("additions", 0)),
                        int(item.get("deletions", 0)),
                        1 if item.get("binary") else 0,
                        item.get("preview_status", "available"),
                        json.dumps(item.get("warnings", []), ensure_ascii=False)
                        if item.get("warnings")
                        else None,
                    ),
                )
                change_row = conn.execute(
                    """
                    SELECT id FROM turn_file_changes
                    WHERE change_set_id=? AND relative_path=?
                    """,
                    (change_set_id, item["relative_path"]),
                ).fetchone()
                change_id = str(change_row["id"])
                current_blobs: List[str] = []
                for side in ("before", "after"):
                    blob_id = item.get(f"{side}_blob_id")
                    if not blob_id:
                        continue
                    byte_count = int(item.get(f"{side}_byte_count", 0))
                    conn.execute(
                        """
                        INSERT INTO artifact_blobs(
                            sha256, byte_count, encoding, storage_path,
                            created_at, reference_count
                        ) VALUES (?, ?, 'cas-v1', NULL, ?, 0)
                        ON CONFLICT(sha256) DO NOTHING
                        """,
                        (blob_id, byte_count, now),
                    )
                    conn.execute(
                        """
                        INSERT INTO artifact_refs(
                            id, conversation_id, turn_id, change_id, side, blob_id
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        ON CONFLICT(blob_id, change_id, side) DO NOTHING
                        """,
                        (
                            uuid.uuid4().hex,
                            conversation_id,
                            turn_id,
                            change_id,
                            side,
                            blob_id,
                        ),
                    )
                    current_blobs.append(str(blob_id))
                current_change_blobs.extend(current_blobs)
            orphaned: List[str] = []
            for blob_id in set(previous_change_blobs + current_change_blobs):
                count = int(
                    conn.execute(
                        """
                        SELECT COUNT(*) AS value FROM artifact_refs
                        WHERE blob_id=?
                        """,
                        (blob_id,),
                    ).fetchone()["value"]
                )
                if count:
                    conn.execute(
                        """
                        UPDATE artifact_blobs SET reference_count=?
                        WHERE sha256=?
                        """,
                        (count, blob_id),
                    )
                else:
                    conn.execute(
                        "DELETE FROM artifact_blobs WHERE sha256=?", (blob_id,)
                    )
                    orphaned.append(blob_id)
            conn.commit()
            return orphaned

    def get_change_set(
        self, conversation_id: str, turn_id: str
    ) -> Optional[Dict[str, Any]]:
        with self._lock:
            conn = self._connect()
            row = conn.execute(
                """
                SELECT * FROM turn_change_sets
                WHERE conversation_id=? AND turn_id=?
                """,
                (conversation_id, turn_id),
            ).fetchone()
            if row is None:
                return None
            files = conn.execute(
                """
                SELECT * FROM turn_file_changes
                WHERE change_set_id=? ORDER BY relative_path
                """,
                (row["id"],),
            ).fetchall()
        result = dict(row)
        result["files"] = []
        for file in files:
            item = dict(file)
            item["binary"] = bool(item["binary"])
            item["warnings"] = json.loads(item["warnings"]) if item["warnings"] else []
            result["files"].append(item)
        return result

    def get_file_change(
        self, conversation_id: str, turn_id: str, change_id: str
    ) -> Optional[Dict[str, Any]]:
        with self._lock:
            conn = self._connect()
            row = conn.execute(
                """
                SELECT tfc.* FROM turn_file_changes tfc
                JOIN turn_change_sets tcs ON tcs.id = tfc.change_set_id
                WHERE tcs.conversation_id=? AND tcs.turn_id=? AND tfc.id=?
                """,
                (conversation_id, turn_id, change_id),
            ).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["binary"] = bool(item["binary"])
        item["warnings"] = json.loads(item["warnings"]) if item["warnings"] else []
        return item

    # ------------------------------------------------------------ helpers

    @staticmethod
    def _row_to_conversation(row: sqlite3.Row) -> ConversationRecord:
        return ConversationRecord(
            id=str(row["id"]),
            title=str(row["title"]),
            title_source=str(row["title_source"]),
            workspace_path=str(row["workspace_path"]),
            workspace_key=str(row["workspace_key"]),
            profile_id=row["profile_id"],
            state=str(row["state"]),
            version=int(row["version"]),
            created_at=str(row["created_at"]),
            last_activity_at=str(row["last_activity_at"]),
            archived_at=row["archived_at"],
        )

    @staticmethod
    def _row_to_turn(row: sqlite3.Row) -> TurnRecord:
        return TurnRecord(
            id=str(row["id"]),
            conversation_id=str(row["conversation_id"]),
            ordinal=int(row["ordinal"]),
            state=str(row["state"]),
            run_id=row["run_id"],
            user_text=str(row["user_text"]),
            created_at=str(row["created_at"]),
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            result_json=row["result_json"],
            error_code=row["error_code"],
        )

    @staticmethod
    def _row_to_group(row: sqlite3.Row) -> CanonicalGroupRecord:
        return CanonicalGroupRecord(
            id=str(row["id"]),
            conversation_id=str(row["conversation_id"]),
            turn_id=str(row["turn_id"]),
            group_seq=int(row["group_seq"]),
            kind=str(row["kind"]),
            state=str(row["state"]),
            created_at=str(row["created_at"]),
            committed_at=row["committed_at"],
        )
