"""SQLite conversation repository with explicit schema migration.

This is the single durable fact source for conversations/turns/canonical
history/public events in task_004. It is deliberately dependency-free
(standard library ``sqlite3`` only) and does not start threads or call models.
"""

from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..memory.analyzer import (
    format_query_terms,
    searchable_text,
    terms_for_query,
    tokenize,
)
from ..models import CanonicalMessage, UserMessage
from .domain import (
    CanonicalGroupRecord,
    CanonicalGroupState,
    ConversationRecord,
    PublicEventRecord,
    TurnRecord,
    canonical_message_to_payload,
    payload_to_canonical_message,
)

SCHEMA_VERSION = 13
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

CREATE TABLE IF NOT EXISTS stream_checkpoints (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    turn_id TEXT NOT NULL REFERENCES turns(id) ON DELETE CASCADE,
    run_id TEXT NOT NULL,
    attempt INTEGER NOT NULL,
    channel TEXT NOT NULL,
    text TEXT NOT NULL,
    char_count INTEGER NOT NULL,
    event_seq INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    UNIQUE (turn_id, attempt, channel)
);

CREATE TABLE IF NOT EXISTS inbox_meta (
    conversation_id TEXT PRIMARY KEY
        REFERENCES conversations(id) ON DELETE CASCADE,
    queue_version INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS inbox_items (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    content TEXT NOT NULL,
    requested_mode TEXT NOT NULL CHECK (requested_mode IN ('queue', 'steer')),
    state TEXT NOT NULL CHECK (
        state IN ('queued', 'steer_pending', 'claimed', 'delivered',
                  'blocked', 'removed')
    ),
    position INTEGER NOT NULL,
    bound_turn_id TEXT,
    claimed_turn_id TEXT UNIQUE,
    idempotency_key TEXT,
    profile_id TEXT,
    reasoning_effort TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    last_error_code TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    claimed_at TEXT,
    delivered_at TEXT,
    UNIQUE (conversation_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_inbox_queue
    ON inbox_items(conversation_id, state, position, id);

CREATE TABLE IF NOT EXISTS inbox_events (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    item_id TEXT,
    event_seq INTEGER NOT NULL,
    kind TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (conversation_id, event_seq)
);

CREATE TRIGGER IF NOT EXISTS enforce_inbox_state_transition
BEFORE UPDATE OF state ON inbox_items
WHEN OLD.state <> NEW.state AND NOT (
    (OLD.state = 'queued' AND NEW.state IN ('steer_pending', 'claimed', 'delivered', 'blocked', 'removed'))
    OR (OLD.state = 'steer_pending' AND NEW.state IN ('queued', 'claimed', 'delivered', 'blocked', 'removed'))
    OR (OLD.state = 'claimed' AND NEW.state IN ('queued', 'delivered', 'blocked'))
    OR (OLD.state = 'delivered' AND NEW.state IN ('blocked'))
    OR (OLD.state = 'blocked' AND NEW.state IN ('queued', 'steer_pending', 'removed'))
)
BEGIN
    SELECT RAISE(ABORT, 'invalid_inbox_state_transition');
END;

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

_MEMORY_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS memory_entries (
        id TEXT PRIMARY KEY,
        scope_type TEXT NOT NULL CHECK (scope_type IN ('global', 'workspace', 'conversation')),
        scope_key TEXT NOT NULL,
        kind TEXT NOT NULL CHECK (kind IN ('preference', 'fact', 'decision', 'procedure')),
        title TEXT,
        content TEXT NOT NULL,
        status TEXT NOT NULL CHECK (
            status IN ('candidate', 'confirmed', 'superseded', 'rejected', 'deleted')
        ),
        confirmation TEXT NOT NULL CHECK (
            confirmation IN ('explicit_ui', 'explicit_command', 'user_approved', 'imported')
        ),
        source_conversation_id TEXT,
        source_turn_id TEXT,
        source_excerpt TEXT,
        supersedes_id TEXT,
        version INTEGER NOT NULL DEFAULT 1,
        normalized_hash TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        last_used_at TEXT,
        use_count INTEGER NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_memory_scope_status
        ON memory_entries(scope_type, scope_key, status, updated_at)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_memory_status_updated
        ON memory_entries(status, updated_at)
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_single_successor
        ON memory_entries(supersedes_id)
        WHERE supersedes_id IS NOT NULL
    """,
    """
    CREATE TABLE IF NOT EXISTS memory_sources (
        id TEXT PRIMARY KEY,
        entry_id TEXT NOT NULL REFERENCES memory_entries(id) ON DELETE CASCADE,
        conversation_id TEXT,
        turn_id TEXT,
        excerpt TEXT,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_memory_sources_entry
        ON memory_sources(entry_id)
    """,
    """
    CREATE TABLE IF NOT EXISTS memory_terms (
        entry_id TEXT NOT NULL REFERENCES memory_entries(id) ON DELETE CASCADE,
        term TEXT NOT NULL,
        PRIMARY KEY (entry_id, term)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_memory_terms_term
        ON memory_terms(term)
    """,
    """
    CREATE TABLE IF NOT EXISTS memory_usage (
        turn_id TEXT NOT NULL,
        entry_id TEXT NOT NULL,
        rank INTEGER NOT NULL,
        reason TEXT NOT NULL,
        snapshot_hash TEXT NOT NULL,
        used_at TEXT NOT NULL,
        scope_type TEXT,
        scope_key TEXT,
        kind TEXT,
        title TEXT,
        source_conversation_id TEXT,
        source_turn_id TEXT,
        PRIMARY KEY (turn_id, entry_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_memory_usage_turn
        ON memory_usage(turn_id)
    """,
    """
    CREATE TABLE IF NOT EXISTS memory_events (
        id TEXT PRIMARY KEY,
        entry_id TEXT,
        kind TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS memory_meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS memory_idempotency (
        idempotency_key TEXT PRIMARY KEY,
        operation TEXT NOT NULL,
        target_id TEXT,
        result_version INTEGER,
        result_count INTEGER,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS memory_scope_versions (
        scope_type TEXT NOT NULL,
        scope_key TEXT NOT NULL,
        version INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (scope_type, scope_key)
    )
    """,
    """
    CREATE TRIGGER IF NOT EXISTS enforce_memory_status_transition
    BEFORE UPDATE OF status ON memory_entries
    WHEN OLD.status <> NEW.status AND NOT (
        (OLD.status = 'candidate' AND NEW.status IN ('confirmed', 'rejected', 'superseded'))
        OR (OLD.status = 'confirmed' AND NEW.status = 'superseded')
    )
    BEGIN
        SELECT RAISE(ABORT, 'invalid_memory_status_transition');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS enforce_memory_insert_invariants
    BEFORE INSERT ON memory_entries
    WHEN (NEW.scope_type = 'global' AND NEW.scope_key <> 'global')
      OR (NEW.status = 'candidate' AND NEW.confirmation <> 'imported')
      OR (NEW.status = 'confirmed' AND NEW.confirmation = 'imported')
    BEGIN
        SELECT RAISE(ABORT, 'invalid_memory_invariant');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS enforce_memory_update_invariants
    BEFORE UPDATE OF scope_type, scope_key, status, confirmation ON memory_entries
    WHEN (NEW.scope_type = 'global' AND NEW.scope_key <> 'global')
      OR (NEW.status = 'candidate' AND NEW.confirmation <> 'imported')
      OR (NEW.status = 'confirmed' AND NEW.confirmation = 'imported')
      OR OLD.scope_type <> NEW.scope_type
      OR OLD.scope_key <> NEW.scope_key
    BEGIN
        SELECT RAISE(ABORT, 'invalid_memory_invariant');
    END
    """,
)


_last_timestamp: float = 0.0


def _utcnow() -> str:
    """Monotonic UTC timestamp string.

    The database uses ``last_activity_at`` as part of its opaque cursor and
    assumes inserted rows sort after existing rows. Free-running Windows
    timestamps can repeat within a microsecond under rapid inserts, so we
    nudge the clock forward by 1us whenever the previous value is not strictly
    larger.
    """
    global _last_timestamp
    now = time.time()
    if now <= _last_timestamp:
        now = _last_timestamp + 0.000001
    _last_timestamp = now
    return datetime.fromtimestamp(now, timezone.utc).isoformat(timespec="microseconds")


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


def _create_memory_tables(conn: sqlite3.Connection) -> None:
    """Create the task_007 memory tables and attempt the optional FTS5 index."""
    for statement in _MEMORY_SCHEMA_STATEMENTS:
        conn.execute(statement)
    usage_columns = {
        str(row["name"]) for row in conn.execute("PRAGMA table_info(memory_usage)")
    }
    for column in (
        "scope_type",
        "scope_key",
        "kind",
        "title",
        "source_conversation_id",
        "source_turn_id",
    ):
        if column not in usage_columns:
            conn.execute(f"ALTER TABLE memory_usage ADD COLUMN {column} TEXT")
    try:
        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts "
            "USING fts5(search_text, entry_id UNINDEXED)"
        )
        conn.execute(
            "INSERT INTO memory_meta(key, value) VALUES ('index_backend', 'fts5') "
            "ON CONFLICT(key) DO UPDATE SET value='fts5'"
        )
    except sqlite3.OperationalError:
        conn.execute(
            "INSERT INTO memory_meta(key, value) VALUES ('index_backend', 'terms') "
            "ON CONFLICT(key) DO UPDATE SET value='terms'"
        )


def _memory_backend(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        "SELECT value FROM memory_meta WHERE key='index_backend'"
    ).fetchone()
    return str(row["value"]) if row else "terms"


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
                _create_memory_tables(conn)
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
                    if current_version <= 3:
                        conn.execute(
                            """
                            CREATE TABLE IF NOT EXISTS stream_checkpoints (
                                id TEXT PRIMARY KEY,
                                conversation_id TEXT NOT NULL
                                    REFERENCES conversations(id) ON DELETE CASCADE,
                                turn_id TEXT NOT NULL
                                    REFERENCES turns(id) ON DELETE CASCADE,
                                run_id TEXT NOT NULL,
                                attempt INTEGER NOT NULL,
                                channel TEXT NOT NULL,
                                text TEXT NOT NULL,
                                char_count INTEGER NOT NULL,
                                event_seq INTEGER NOT NULL DEFAULT 0,
                                updated_at TEXT NOT NULL,
                                UNIQUE (turn_id, attempt, channel)
                            )
                            """
                        )
                    if current_version <= 4:
                        columns = {
                            str(row["name"])
                            for row in conn.execute(
                                "PRAGMA table_info(stream_checkpoints)"
                            ).fetchall()
                        }
                        if "event_seq" not in columns:
                            conn.execute(
                                "ALTER TABLE stream_checkpoints "
                                "ADD COLUMN event_seq INTEGER NOT NULL DEFAULT 0"
                            )
                    if current_version <= 5:
                        conn.execute(
                            """
                            CREATE TABLE IF NOT EXISTS inbox_meta (
                                conversation_id TEXT PRIMARY KEY
                                    REFERENCES conversations(id) ON DELETE CASCADE,
                                queue_version INTEGER NOT NULL DEFAULT 1
                            )
                            """
                        )
                        conn.execute(
                            """
                            CREATE TABLE IF NOT EXISTS inbox_items (
                                id TEXT PRIMARY KEY,
                                conversation_id TEXT NOT NULL
                                    REFERENCES conversations(id) ON DELETE CASCADE,
                                content TEXT NOT NULL,
                                requested_mode TEXT NOT NULL
                                    CHECK (requested_mode IN ('queue', 'steer')),
                                state TEXT NOT NULL CHECK (
                                    state IN ('queued', 'steer_pending', 'claimed',
                                              'delivered', 'blocked', 'removed')
                                ),
                                position INTEGER NOT NULL,
                                bound_turn_id TEXT,
                                claimed_turn_id TEXT UNIQUE,
                                idempotency_key TEXT,
                                version INTEGER NOT NULL DEFAULT 1,
                                last_error_code TEXT,
                                created_at TEXT NOT NULL,
                                updated_at TEXT NOT NULL,
                                claimed_at TEXT,
                                delivered_at TEXT,
                                UNIQUE (conversation_id, idempotency_key)
                            )
                            """
                        )
                        conn.execute(
                            """
                            CREATE INDEX IF NOT EXISTS idx_inbox_queue
                                ON inbox_items(conversation_id, state, position, id)
                            """
                        )
                        conn.execute(
                            """
                            CREATE TABLE IF NOT EXISTS inbox_events (
                                id TEXT PRIMARY KEY,
                                conversation_id TEXT NOT NULL
                                    REFERENCES conversations(id) ON DELETE CASCADE,
                                item_id TEXT,
                                event_seq INTEGER NOT NULL,
                                kind TEXT NOT NULL,
                                payload_json TEXT NOT NULL,
                                created_at TEXT NOT NULL,
                                UNIQUE (conversation_id, event_seq)
                            )
                            """
                        )
                        has_conversations = conn.execute(
                            "SELECT name FROM sqlite_master WHERE type='table' AND name='conversations'"
                        ).fetchone()
                        if has_conversations is not None:
                            for row in conn.execute(
                                "SELECT id FROM conversations"
                            ).fetchall():
                                conn.execute(
                                    "INSERT OR IGNORE INTO inbox_meta(conversation_id)"
                                    " VALUES (?)",
                                    (str(row["id"]),),
                                )
                    if current_version <= 6:
                        columns = {
                            str(row["name"])
                            for row in conn.execute(
                                "PRAGMA table_info(inbox_items)"
                            ).fetchall()
                        }
                        if "reasoning_effort" not in columns:
                            conn.execute(
                                "ALTER TABLE inbox_items "
                                "ADD COLUMN reasoning_effort TEXT"
                            )
                    if current_version <= 7:
                        conn.execute(
                            """
                            CREATE TRIGGER IF NOT EXISTS enforce_inbox_state_transition
                            BEFORE UPDATE OF state ON inbox_items
                            WHEN OLD.state <> NEW.state AND NOT (
                                (OLD.state = 'queued' AND NEW.state IN ('steer_pending', 'claimed', 'delivered', 'blocked', 'removed'))
                                OR (OLD.state = 'steer_pending' AND NEW.state IN ('queued', 'claimed', 'delivered', 'blocked', 'removed'))
                                OR (OLD.state = 'claimed' AND NEW.state IN ('queued', 'delivered', 'blocked'))
                                OR (OLD.state = 'delivered' AND NEW.state IN ('blocked'))
                                OR (OLD.state = 'blocked' AND NEW.state IN ('queued', 'steer_pending', 'removed'))
                            )
                            BEGIN
                                SELECT RAISE(ABORT, 'invalid_inbox_state_transition');
                            END
                            """
                        )
                    if current_version <= 8:
                        columns = {
                            str(row["name"])
                            for row in conn.execute(
                                "PRAGMA table_info(inbox_items)"
                            ).fetchall()
                        }
                        if "profile_id" not in columns:
                            conn.execute(
                                "ALTER TABLE inbox_items ADD COLUMN profile_id TEXT"
                            )
                    if current_version <= 12:
                        _create_memory_tables(conn)
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
                conn.execute(
                    "INSERT OR IGNORE INTO inbox_meta(conversation_id) VALUES (?)",
                    (conversation_id,),
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
        inbox_item_id: Optional[str] = None,
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
                inbox_item: Optional[sqlite3.Row] = None
                effective_user_text = user_text
                effective_messages = messages
                if inbox_item_id is not None:
                    inbox_item = conn.execute(
                        """
                        SELECT * FROM inbox_items
                        WHERE id=? AND conversation_id=? AND state='queued'
                        """,
                        (inbox_item_id, conversation_id),
                    ).fetchone()
                    if inbox_item is None:
                        raise ValueError("inbox_item_not_queued")
                    # The queue row is the transaction's authority.  An edit
                    # committed after the consumer's preliminary read must be
                    # the text that becomes the turn opener.
                    effective_user_text = str(inbox_item["content"])
                    effective_messages = tuple(
                        UserMessage(effective_user_text, source="user")
                        if isinstance(message, UserMessage) and message.source == "user"
                        else message
                        for message in messages
                    )
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
                        effective_user_text,
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
                for offset, message in enumerate(effective_messages):
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
                if inbox_item is not None:
                    claim_cursor = conn.execute(
                        """
                        UPDATE inbox_items SET state='delivered', claimed_turn_id=?,
                            claimed_at=?, delivered_at=?, updated_at=?,
                            version=version+1
                        WHERE id=? AND conversation_id=? AND state='queued'
                        """,
                        (
                            turn_id,
                            now,
                            now,
                            now,
                            inbox_item_id,
                            conversation_id,
                        ),
                    )
                    if claim_cursor.rowcount != 1:
                        raise ValueError("inbox_item_not_queued")
                    self._append_inbox_event(
                        conn,
                        conversation_id,
                        inbox_item_id,
                        "claimed",
                        {"turn_id": turn_id},
                    )
                    self._append_inbox_event(
                        conn,
                        conversation_id,
                        inbox_item_id,
                        "delivered",
                        {"source": "queue"},
                    )
                    self._bump_inbox_version(conn, conversation_id)
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

    def append_public_events_batch(
        self,
        entries: Sequence[Dict[str, Any]],
    ) -> None:
        """Insert a batch of public events in one transaction.

        This is the task_005 stream checkpoint path: many small deltas are
        coalesced in memory and written with a single commit instead of one
        fsync per token.
        """
        if not entries:
            return
        now = _utcnow()
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                for entry in entries:
                    conn.execute(
                        """
                        INSERT INTO public_events(
                            id, conversation_id, turn_id, run_id, event_seq,
                            kind, payload_json, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            uuid.uuid4().hex,
                            entry["conversation_id"],
                            entry["turn_id"],
                            entry["run_id"],
                            entry["event_seq"],
                            entry["kind"],
                            json.dumps(
                                entry["payload"],
                                ensure_ascii=False,
                                sort_keys=True,
                            ),
                            now,
                        ),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

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

    def upsert_stream_checkpoints_batch(
        self,
        entries: Sequence[Dict[str, Any]],
    ) -> None:
        """Persist coalesced partial stream text checkpoint rows.

        Entries contain only the newly buffered fragment. SQLite appends it
        once per batch, avoiding Python's repeated cumulative-string copies.
        """
        if not entries:
            return
        now = _utcnow()
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                for entry in entries:
                    conn.execute(
                        """
                        INSERT INTO stream_checkpoints(
                            id, conversation_id, turn_id, run_id, attempt,
                            channel, text, char_count, event_seq, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(turn_id, attempt, channel) DO UPDATE SET
                            run_id=excluded.run_id,
                            text=CASE
                                WHEN excluded.event_seq > stream_checkpoints.event_seq
                                THEN stream_checkpoints.text || excluded.text
                                ELSE stream_checkpoints.text
                            END,
                            char_count=CASE
                                WHEN excluded.event_seq > stream_checkpoints.event_seq
                                THEN stream_checkpoints.char_count + excluded.char_count
                                ELSE stream_checkpoints.char_count
                            END,
                            event_seq=MAX(
                                stream_checkpoints.event_seq, excluded.event_seq
                            ),
                            updated_at=excluded.updated_at
                        """,
                        (
                            uuid.uuid4().hex,
                            entry["conversation_id"],
                            entry["turn_id"],
                            entry["run_id"],
                            int(entry["attempt"]),
                            str(entry["channel"]),
                            str(entry["text"]),
                            len(str(entry["text"])),
                            int(entry["event_seq"]),
                            now,
                        ),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def get_stream_checkpoints(
        self, conversation_id: str, turn_id: str
    ) -> List[Dict[str, Any]]:
        with self._lock:
            conn = self._connect()
            rows = conn.execute(
                """
                SELECT run_id, attempt, channel, text, char_count, event_seq, updated_at
                FROM stream_checkpoints
                WHERE conversation_id=? AND turn_id=?
                ORDER BY attempt, channel
                """,
                (conversation_id, turn_id),
            ).fetchall()
        return [dict(row) for row in rows]

    # ------------------------------------------------------------ inbox

    def get_inbox_snapshot(self, conversation_id: str) -> Dict[str, Any]:
        self._ensure_inbox_meta(conversation_id)
        with self._lock:
            conn = self._connect()
            version_row = conn.execute(
                "SELECT queue_version FROM inbox_meta WHERE conversation_id=?",
                (conversation_id,),
            ).fetchone()
            rows = conn.execute(
                """
                SELECT * FROM inbox_items
                WHERE conversation_id=? AND state != 'removed'
                ORDER BY position, id
                """,
                (conversation_id,),
            ).fetchall()
            events = conn.execute(
                """
                SELECT event_seq, kind FROM inbox_events
                WHERE conversation_id=? ORDER BY event_seq DESC LIMIT 50
                """,
                (conversation_id,),
            ).fetchall()
        return {
            "queue_version": int(version_row["queue_version"]) if version_row else 1,
            "items": [self._row_to_inbox_item(row) for row in rows],
            "recent_events": [
                {"seq": int(row["event_seq"]), "kind": str(row["kind"])}
                for row in reversed(events)
            ],
        }

    def enqueue_inbox_item(
        self,
        conversation_id: str,
        *,
        content: str,
        requested_mode: str,
        idempotency_key: Optional[str] = None,
        bound_turn_id: Optional[str] = None,
        profile_id: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
    ) -> Dict[str, Any]:
        item_id = uuid.uuid4().hex
        now = _utcnow()
        state = "steer_pending" if requested_mode == "steer" else "queued"
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                if idempotency_key:
                    existing = conn.execute(
                        """
                        SELECT * FROM inbox_items
                        WHERE conversation_id=? AND idempotency_key=?
                        """,
                        (conversation_id, idempotency_key),
                    ).fetchone()
                    if existing is not None:
                        conn.commit()
                        return self._row_to_inbox_item(existing)
                if requested_mode == "steer" and not bound_turn_id:
                    raise ValueError("turn_not_steerable")
                if requested_mode == "steer" and state == "steer_pending":
                    state = "steer_pending"
                position = int(
                    conn.execute(
                        """
                        SELECT COALESCE(MAX(position), 0) + 1 AS value
                        FROM inbox_items WHERE conversation_id=? AND state != 'removed'
                        """,
                        (conversation_id,),
                    ).fetchone()["value"]
                )
                conn.execute(
                    """
                    INSERT INTO inbox_items(
                        id, conversation_id, content, requested_mode, state,
                        position, bound_turn_id, idempotency_key, profile_id,
                        reasoning_effort, version, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                    """,
                    (
                        item_id,
                        conversation_id,
                        content,
                        requested_mode,
                        state,
                        position,
                        bound_turn_id,
                        idempotency_key,
                        profile_id,
                        reasoning_effort,
                        now,
                        now,
                    ),
                )
                self._bump_inbox_version(conn, conversation_id)
                self._append_inbox_event(
                    conn,
                    conversation_id,
                    item_id,
                    "enqueued",
                    {"mode": requested_mode, "state": state},
                )
                item = conn.execute(
                    "SELECT * FROM inbox_items WHERE id=?", (item_id,)
                ).fetchone()
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return self._row_to_inbox_item(item)

    def edit_inbox_item(
        self,
        conversation_id: str,
        item_id: str,
        *,
        content: Optional[str] = None,
        requested_mode: Optional[str] = None,
        expected_version: int,
    ) -> Dict[str, Any]:
        now = _utcnow()
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT * FROM inbox_items WHERE id=? AND conversation_id=?",
                    (item_id, conversation_id),
                ).fetchone()
                if row is None or row["state"] == "removed":
                    raise KeyError("item_not_found")
                if row["state"] not in ("queued", "steer_pending", "blocked"):
                    raise ValueError("item_not_editable")
                if int(row["version"]) != expected_version:
                    raise ValueError("version_conflict")
                new_content = content if content is not None else row["content"]
                new_mode = (
                    requested_mode
                    if requested_mode is not None
                    else row["requested_mode"]
                )
                conn.execute(
                    """
                    UPDATE inbox_items SET content=?, requested_mode=?,
                        version=version+1, updated_at=?
                    WHERE id=? AND conversation_id=? AND version=?
                    """,
                    (
                        new_content,
                        new_mode,
                        now,
                        item_id,
                        conversation_id,
                        expected_version,
                    ),
                )
                if conn.total_changes == 0:
                    raise ValueError("version_conflict")
                self._append_inbox_event(
                    conn,
                    conversation_id,
                    item_id,
                    "edited",
                    {"version": expected_version + 1},
                )
                self._bump_inbox_version(conn, conversation_id)
                item = conn.execute(
                    "SELECT * FROM inbox_items WHERE id=?", (item_id,)
                ).fetchone()
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return self._row_to_inbox_item(item)

    def remove_inbox_item(
        self, conversation_id: str, item_id: str, expected_version: int
    ) -> None:
        now = _utcnow()
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT * FROM inbox_items WHERE id=? AND conversation_id=?",
                    (item_id, conversation_id),
                ).fetchone()
                if row is None or row["state"] == "removed":
                    raise KeyError("item_not_found")
                if row["state"] not in ("queued", "steer_pending", "blocked"):
                    raise ValueError("item_not_editable")
                if int(row["version"]) != expected_version:
                    raise ValueError("version_conflict")
                conn.execute(
                    """
                    UPDATE inbox_items SET state='removed', content='',
                        version=version+1, updated_at=?
                    WHERE id=? AND version=?
                    """,
                    (now, item_id, expected_version),
                )
                self._append_inbox_event(
                    conn,
                    conversation_id,
                    item_id,
                    "removed",
                    {"version": expected_version + 1},
                )
                self._bump_inbox_version(conn, conversation_id)
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def reorder_inbox_items(
        self,
        conversation_id: str,
        ordered_ids: Sequence[str],
        expected_queue_version: int,
    ) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                meta = conn.execute(
                    "SELECT queue_version FROM inbox_meta WHERE conversation_id=?",
                    (conversation_id,),
                ).fetchone()
                if meta is None or int(meta["queue_version"]) != expected_queue_version:
                    raise ValueError("version_conflict")
                active = conn.execute(
                    """
                    SELECT id FROM inbox_items
                    WHERE conversation_id=? AND state IN ('queued','steer_pending','blocked')
                    """,
                    (conversation_id,),
                ).fetchall()
                active_ids = {str(row["id"]) for row in active}
                if set(ordered_ids) != active_ids:
                    raise ValueError("invalid_reorder")
                for position, item_id in enumerate(ordered_ids, start=1):
                    conn.execute(
                        """
                        UPDATE inbox_items SET position=?, updated_at=?
                        WHERE id=? AND conversation_id=?
                        """,
                        (position, _utcnow(), item_id, conversation_id),
                    )
                self._append_inbox_event(
                    conn,
                    conversation_id,
                    None,
                    "reordered",
                    {"ordered_ids": list(ordered_ids)},
                )
                self._bump_inbox_version(conn, conversation_id)
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def request_steer(
        self, conversation_id: str, item_id: str, expected_version: int
    ) -> Dict[str, Any]:
        now = _utcnow()
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT * FROM inbox_items WHERE id=? AND conversation_id=?",
                    (item_id, conversation_id),
                ).fetchone()
                if row is None or row["state"] == "removed":
                    raise KeyError("item_not_found")
                if row["state"] not in ("queued", "blocked"):
                    raise ValueError("item_not_steerable")
                if int(row["version"]) != expected_version:
                    raise ValueError("version_conflict")
                active = conn.execute(
                    """
                    SELECT id FROM turns WHERE conversation_id=?
                    AND state IN ('starting','running') ORDER BY ordinal DESC LIMIT 1
                    """,
                    (conversation_id,),
                ).fetchone()
                if active is None:
                    raise ValueError("turn_not_steerable")
                conn.execute(
                    """
                    UPDATE inbox_items SET state='steer_pending',
                        requested_mode='steer', bound_turn_id=?,
                        version=version+1, updated_at=?
                    WHERE id=? AND version=?
                    """,
                    (str(active["id"]), now, item_id, expected_version),
                )
                self._append_inbox_event(
                    conn,
                    conversation_id,
                    item_id,
                    "steer_requested",
                    {"turn_id": str(active["id"])},
                )
                self._bump_inbox_version(conn, conversation_id)
                item = conn.execute(
                    "SELECT * FROM inbox_items WHERE id=?", (item_id,)
                ).fetchone()
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return self._row_to_inbox_item(item)

    def retry_inbox_item(
        self, conversation_id: str, item_id: str, expected_version: int
    ) -> Dict[str, Any]:
        now = _utcnow()
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT * FROM inbox_items WHERE id=? AND conversation_id=?",
                    (item_id, conversation_id),
                ).fetchone()
                if row is None or row["state"] == "removed":
                    raise KeyError("item_not_found")
                if row["state"] != "blocked":
                    raise ValueError("item_not_blocked")
                if int(row["version"]) != expected_version:
                    raise ValueError("version_conflict")
                conn.execute(
                    """
                    UPDATE inbox_items SET state='queued', last_error_code=NULL,
                        requested_mode='queue', version=version+1, updated_at=?
                    WHERE id=? AND version=?
                    """,
                    (now, item_id, expected_version),
                )
                self._append_inbox_event(conn, conversation_id, item_id, "retried", {})
                self._bump_inbox_version(conn, conversation_id)
                item = conn.execute(
                    "SELECT * FROM inbox_items WHERE id=?", (item_id,)
                ).fetchone()
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return self._row_to_inbox_item(item)

    def list_queued_items(self, conversation_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            rows = (
                self._connect()
                .execute(
                    """
                    SELECT * FROM inbox_items
                    WHERE conversation_id=? AND state='queued'
                    ORDER BY position, id
                    """,
                    (conversation_id,),
                )
                .fetchall()
            )
        return [self._row_to_inbox_item(row) for row in rows]

    def mark_item_claimed(
        self, conversation_id: str, item_id: str, turn_id: str
    ) -> None:
        now = _utcnow()
        with self._lock:
            conn = self._connect()
            conn.execute(
                """
                UPDATE inbox_items SET state='claimed', claimed_turn_id=?,
                    claimed_at=?, updated_at=?, version=version+1
                WHERE id=? AND conversation_id=? AND state='queued'
                """,
                (turn_id, now, now, item_id, conversation_id),
            )
            try:
                self._append_inbox_event(
                    conn,
                    conversation_id,
                    item_id,
                    "claimed",
                    {"turn_id": turn_id},
                )
                self._bump_inbox_version(conn, conversation_id)
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def mark_item_delivered(
        self, conversation_id: str, item_id: str, *, source: str = "queue"
    ) -> None:
        now = _utcnow()
        with self._lock:
            conn = self._connect()
            conn.execute(
                """
                UPDATE inbox_items SET state='delivered', delivered_at=?,
                    updated_at=?, version=version+1
                WHERE id=? AND conversation_id=? AND state IN ('claimed','steer_pending')
                """,
                (now, now, item_id, conversation_id),
            )
            self._append_inbox_event(
                conn,
                conversation_id,
                item_id,
                "delivered",
                {"source": source},
            )
            self._bump_inbox_version(conn, conversation_id)
            conn.commit()

    def demote_steer_pending_for_turn(
        self, conversation_id: str, turn_id: str
    ) -> List[str]:
        now = _utcnow()
        with self._lock:
            conn = self._connect()
            rows = conn.execute(
                """
                SELECT id, position FROM inbox_items
                WHERE conversation_id=? AND state='steer_pending' AND bound_turn_id=?
                ORDER BY position, id
                """,
                (conversation_id, turn_id),
            ).fetchall()
            demoted: List[str] = []
            for row in rows:
                conn.execute(
                    """
                    UPDATE inbox_items SET state='queued', requested_mode='queue',
                        bound_turn_id=NULL, version=version+1, updated_at=?
                    WHERE id=?
                    """,
                    (now, str(row["id"])),
                )
                self._append_inbox_event(
                    conn,
                    conversation_id,
                    str(row["id"]),
                    "demoted",
                    {"to": "queued"},
                )
                demoted.append(str(row["id"]))
            if demoted:
                self._bump_inbox_version(conn, conversation_id)
            conn.commit()
        return demoted

    def demote_all_steer_pending(self, conversation_id: str) -> List[str]:
        with self._lock:
            conn = self._connect()
            rows = conn.execute(
                """
                SELECT id FROM inbox_items
                WHERE conversation_id=? AND state='steer_pending'
                ORDER BY position, id
                """,
                (conversation_id,),
            ).fetchall()
            demoted: List[str] = []
            for row in rows:
                conn.execute(
                    """
                    UPDATE inbox_items SET state='queued', requested_mode='queue',
                        bound_turn_id=NULL, version=version+1, updated_at=?
                    WHERE id=?
                    """,
                    (_utcnow(), str(row["id"])),
                )
                self._append_inbox_event(
                    conn,
                    conversation_id,
                    str(row["id"]),
                    "demoted",
                    {"to": "queued"},
                )
                demoted.append(str(row["id"]))
            if demoted:
                self._bump_inbox_version(conn, conversation_id)
            conn.commit()
        return demoted

    def get_steer_pending_for_turn(
        self, conversation_id: str, turn_id: str
    ) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = (
                self._connect()
                .execute(
                    """
                    SELECT * FROM inbox_items
                    WHERE conversation_id=? AND state='steer_pending'
                      AND bound_turn_id=?
                    ORDER BY position, id LIMIT 1
                    """,
                    (conversation_id, turn_id),
                )
                .fetchone()
            )
        return self._row_to_inbox_item(row) if row else None

    def mark_steer_claimed_for_delivery(
        self, conversation_id: str, turn_id: str, item_id: str
    ) -> bool:
        """Transition steer_pending -> claimed just before AgentLoop append."""
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                cursor = conn.execute(
                    """
                    UPDATE inbox_items SET state='claimed', version=version+1, updated_at=?
                    WHERE id=? AND conversation_id=? AND bound_turn_id=?
                      AND state='steer_pending'
                    """,
                    (_utcnow(), item_id, conversation_id, turn_id),
                )
                if cursor.rowcount != 1:
                    conn.commit()
                    return False
                self._append_inbox_event(
                    conn,
                    conversation_id,
                    item_id,
                    "steer_claimed",
                    {"turn_id": turn_id},
                )
                self._bump_inbox_version(conn, conversation_id)
                conn.commit()
                return True
            except Exception:
                conn.rollback()
                raise

    def recover_claimed_steers(self) -> List[str]:
        """Return interrupted in-flight steers to the durable FIFO queue."""
        now = _utcnow()
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                rows = conn.execute(
                    """
                    SELECT id, conversation_id FROM inbox_items
                    WHERE state='claimed' AND claimed_turn_id IS NULL
                      AND bound_turn_id IS NOT NULL
                    ORDER BY conversation_id, position, id
                    """
                ).fetchall()
                item_ids = [str(row["id"]) for row in rows]
                touched: set[str] = set()
                for row in rows:
                    item_id = str(row["id"])
                    conversation_id = str(row["conversation_id"])
                    conn.execute(
                        """
                        UPDATE inbox_items SET state='queued', requested_mode='queue',
                            bound_turn_id=NULL, version=version+1, updated_at=?
                        WHERE id=?
                        """,
                        (now, item_id),
                    )
                    self._append_inbox_event(
                        conn,
                        conversation_id,
                        item_id,
                        "demoted",
                        {"to": "queued", "reason": "process_restarted"},
                    )
                    touched.add(conversation_id)
                for conversation_id in touched:
                    self._bump_inbox_version(conn, conversation_id)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return item_ids

    def block_inbox_item(
        self, conversation_id: str, item_id: str, error_code: str
    ) -> None:
        with self._lock:
            conn = self._connect()
            cursor = conn.execute(
                """
                UPDATE inbox_items SET state='blocked', last_error_code=?,
                    bound_turn_id=NULL, requested_mode='queue',
                    version=version+1, updated_at=?
                WHERE id=? AND conversation_id=?
                  AND state IN ('queued','claimed','steer_pending','delivered')
                """,
                (error_code, _utcnow(), item_id, conversation_id),
            )
            if cursor.rowcount != 1:
                conn.rollback()
                return
            self._append_inbox_event(
                conn,
                conversation_id,
                item_id,
                "blocked",
                {"error_code": error_code},
            )
            self._bump_inbox_version(conn, conversation_id)
            conn.commit()

    def _ensure_inbox_meta(self, conversation_id: str) -> None:
        with self._lock:
            conn = self._connect()
            conn.execute(
                "INSERT OR IGNORE INTO inbox_meta(conversation_id) VALUES (?)",
                (conversation_id,),
            )
            conn.commit()

    @staticmethod
    def _bump_inbox_version(conn: sqlite3.Connection, conversation_id: str) -> None:
        conn.execute(
            """
            INSERT INTO inbox_meta(conversation_id, queue_version)
            VALUES (?, 2)
            ON CONFLICT(conversation_id) DO UPDATE SET
                queue_version=queue_version+1
            """,
            (conversation_id,),
        )

    @staticmethod
    def _append_inbox_event(
        conn: sqlite3.Connection,
        conversation_id: str,
        item_id: Optional[str],
        kind: str,
        payload: Dict[str, Any],
    ) -> None:
        seq = int(
            conn.execute(
                """
                SELECT COALESCE(MAX(event_seq), 0) + 1 AS value
                FROM inbox_events WHERE conversation_id=?
                """,
                (conversation_id,),
            ).fetchone()["value"]
        )
        conn.execute(
            """
            INSERT INTO inbox_events(
                id, conversation_id, item_id, event_seq, kind,
                payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                uuid.uuid4().hex,
                conversation_id,
                item_id,
                seq,
                kind,
                json.dumps(payload, ensure_ascii=False, sort_keys=True),
                _utcnow(),
            ),
        )

    @staticmethod
    def _row_to_inbox_item(row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "id": str(row["id"]),
            "conversation_id": str(row["conversation_id"]),
            "content": str(row["content"]),
            "requested_mode": str(row["requested_mode"]),
            "state": str(row["state"]),
            "position": int(row["position"]),
            "bound_turn_id": row["bound_turn_id"],
            "claimed_turn_id": row["claimed_turn_id"],
            "idempotency_key": row["idempotency_key"],
            "profile_id": row["profile_id"],
            "reasoning_effort": row["reasoning_effort"],
            "version": int(row["version"]),
            "last_error_code": row["last_error_code"],
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
            "claimed_at": row["claimed_at"],
            "delivered_at": row["delivered_at"],
        }

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

    # ------------------------------------------------------------ memory

    def create_memory_entry(
        self,
        data: Dict[str, Any],
        *,
        event_kind: str,
        event_payload: Optional[Dict[str, Any]] = None,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        result_id = str(data["id"])
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                replay = self._memory_idempotency_replay(
                    conn, idempotency_key, event_kind
                )
                if replay is not None:
                    result_id = str(replay["target_id"] or "")
                else:
                    self._insert_memory_row(conn, data)
                    self._insert_memory_event(
                        conn,
                        result_id,
                        event_kind,
                        event_payload or {},
                    )
                    self._bump_memory_scope_version(
                        conn, str(data["scope_type"]), str(data["scope_key"])
                    )
                    self._record_memory_idempotency(
                        conn,
                        idempotency_key,
                        event_kind,
                        target_id=result_id,
                        result_version=int(data.get("version", 1)),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        row = self.get_memory_entry(result_id)
        if row is None:
            raise ValueError("idempotency_target_deleted")
        return row

    def create_memory_revision(
        self,
        data: Dict[str, Any],
        *,
        supersede_entry_id: str,
        expected_version: int,
        event_payload: Optional[Dict[str, Any]] = None,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        result_id = ""
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                replay = self._memory_idempotency_replay(
                    conn, idempotency_key, "edited"
                )
                if replay is not None:
                    result_id = str(replay["target_id"] or "")
                    conn.commit()
                    row = self.get_memory_entry(result_id)
                    if row is None:
                        raise ValueError("idempotency_target_deleted")
                    return row
                current = conn.execute(
                    "SELECT * FROM memory_entries WHERE id=?",
                    (supersede_entry_id,),
                ).fetchone()
                if current is None:
                    raise ValueError("memory_not_found")
                if int(current["version"]) != expected_version:
                    raise ValueError("version_conflict")
                now = _utcnow()
                revision = dict(data)
                revision["id"] = uuid.uuid4().hex
                revision["version"] = int(current["version"]) + 1
                revision["supersedes_id"] = supersede_entry_id
                revision["created_at"] = str(current["created_at"])
                revision["updated_at"] = now
                result_id = str(revision["id"])
                self._insert_memory_row(conn, revision)
                conn.execute(
                    """
                    UPDATE memory_entries
                    SET status='superseded', updated_at=?, version=version+1
                    WHERE id=? AND version=?
                    """,
                    (now, supersede_entry_id, expected_version),
                )
                self._delete_memory_index_rows(
                    conn, supersede_entry_id, include_sources=False
                )
                self._insert_memory_event(
                    conn,
                    str(revision["id"]),
                    "edited",
                    event_payload or {"supersedes_id": supersede_entry_id},
                )
                self._bump_memory_scope_version(
                    conn, str(current["scope_type"]), str(current["scope_key"])
                )
                self._record_memory_idempotency(
                    conn,
                    idempotency_key,
                    "edited",
                    target_id=result_id,
                    result_version=int(revision["version"]),
                )
                conn.commit()
                return self.get_memory_entry(result_id)  # type: ignore[return-value]
            except Exception:
                conn.rollback()
                raise

    def get_memory_entry(self, entry_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            conn = self._connect()
            row = conn.execute(
                "SELECT * FROM memory_entries WHERE id=?", (entry_id,)
            ).fetchone()
            if row is None:
                return None
            sources = conn.execute(
                "SELECT * FROM memory_sources WHERE entry_id=? ORDER BY created_at, id",
                (entry_id,),
            ).fetchall()
        result = dict(row)
        result["sources"] = [dict(item) for item in sources]
        return result

    def list_memory_entries(
        self,
        *,
        scope_type: Optional[str] = None,
        scope_key: Optional[str] = None,
        statuses: Optional[Sequence[str]] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        clauses: List[str] = []
        params: List[Any] = []
        if scope_type is not None:
            clauses.append("scope_type=?")
            params.append(scope_type)
        if scope_key is not None:
            clauses.append("scope_key=?")
            params.append(scope_key)
        if statuses:
            clauses.append(f"status IN ({','.join('?' for _ in statuses)})")
            params.extend(statuses)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock:
            rows = (
                self._connect()
                .execute(
                    f"""
                SELECT * FROM memory_entries
                {where}
                ORDER BY updated_at DESC, id
                LIMIT ?
                """,
                    (*params, max(1, int(limit))),
                )
                .fetchall()
            )
        return [dict(row) for row in rows]

    def get_memory_entries_by_ids(
        self, entry_ids: Sequence[str]
    ) -> List[Dict[str, Any]]:
        if not entry_ids:
            return []
        rows_by_id: Dict[str, Dict[str, Any]] = {}
        # Query in chunks to stay within SQLite parameter limits for very large
        # candidate sets (tests / 2000-entry fixtures remain comfortably small).
        chunk_size = 500
        for start in range(0, len(entry_ids), chunk_size):
            chunk = list(entry_ids[start : start + chunk_size])
            with self._lock:
                rows = (
                    self._connect()
                    .execute(
                        f"""
                    SELECT * FROM memory_entries
                    WHERE id IN ({",".join("?" for _ in chunk)})
                    """,
                        chunk,
                    )
                    .fetchall()
                )
            for row in rows:
                rows_by_id[str(row["id"])] = dict(row)
        return [
            rows_by_id[str(entry_id)]
            for entry_id in entry_ids
            if entry_id in rows_by_id
        ]

    def update_memory_status(
        self,
        entry_id: str,
        *,
        status: str,
        confirmation: str,
        expected_version: int,
        event_kind: str,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                replay = self._memory_idempotency_replay(
                    conn, idempotency_key, event_kind
                )
                if replay is not None:
                    conn.commit()
                    row = self.get_memory_entry(entry_id)
                    if row is None:
                        raise ValueError("idempotency_target_deleted")
                    return row
                scope_row = conn.execute(
                    "SELECT content, scope_type, scope_key FROM memory_entries WHERE id=?",
                    (entry_id,),
                ).fetchone()
                if scope_row is None:
                    raise ValueError("memory_not_found")
                cur = conn.execute(
                    """
                    UPDATE memory_entries
                    SET status=?, confirmation=?, version=version+1, updated_at=?
                    WHERE id=? AND version=?
                    """,
                    (status, confirmation, _utcnow(), entry_id, expected_version),
                )
                if cur.rowcount == 0:
                    raise ValueError("version_conflict")
                if status == "confirmed":
                    self._insert_memory_index_rows(
                        conn, entry_id, str(scope_row["content"])
                    )
                else:
                    self._delete_memory_index_rows(
                        conn, entry_id, include_sources=False
                    )
                self._insert_memory_event(conn, entry_id, event_kind, {})
                self._bump_memory_scope_version(
                    conn,
                    str(scope_row["scope_type"]),
                    str(scope_row["scope_key"]),
                )
                self._record_memory_idempotency(
                    conn,
                    idempotency_key,
                    event_kind,
                    target_id=entry_id,
                    result_version=expected_version + 1,
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        row = self.get_memory_entry(entry_id)
        assert row is not None
        return row

    def delete_memory_entry(
        self,
        entry_id: str,
        *,
        expected_version: int,
        idempotency_key: Optional[str] = None,
    ) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                replay = self._memory_idempotency_replay(
                    conn, idempotency_key, "deleted"
                )
                if replay is not None:
                    conn.commit()
                    return
                cur = conn.execute(
                    "SELECT version, scope_type, scope_key FROM memory_entries WHERE id=?",
                    (entry_id,),
                ).fetchone()
                if cur is None:
                    raise ValueError("memory_not_found")
                if int(cur["version"]) != expected_version:
                    raise ValueError("version_conflict")
                rows = conn.execute(
                    "SELECT id, supersedes_id FROM memory_entries "
                    "WHERE scope_type=? AND scope_key=?",
                    (str(cur["scope_type"]), str(cur["scope_key"])),
                ).fetchall()
                links = {
                    str(row["id"]): (
                        str(row["supersedes_id"])
                        if row["supersedes_id"] is not None
                        else None
                    )
                    for row in rows
                }
                chain_ids = {entry_id}
                changed = True
                while changed:
                    changed = False
                    for current_id, predecessor_id in links.items():
                        if (
                            current_id in chain_ids or predecessor_id in chain_ids
                        ) and current_id not in chain_ids:
                            chain_ids.add(current_id)
                            changed = True
                        if (
                            current_id in chain_ids
                            and predecessor_id is not None
                            and predecessor_id not in chain_ids
                        ):
                            chain_ids.add(predecessor_id)
                            changed = True
                self._insert_memory_event(
                    conn,
                    entry_id,
                    "deleted",
                    {"deleted_versions": len(chain_ids)},
                )
                self._bump_memory_scope_version(
                    conn, str(cur["scope_type"]), str(cur["scope_key"])
                )
                for chained_id in chain_ids:
                    self._delete_memory_index_rows(conn, chained_id)
                placeholders = ",".join("?" for _ in chain_ids)
                conn.execute(
                    f"DELETE FROM memory_entries WHERE id IN ({placeholders})",
                    tuple(chain_ids),
                )
                self._record_memory_idempotency(
                    conn,
                    idempotency_key,
                    "deleted",
                    target_id=entry_id,
                    result_version=expected_version,
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def reset_memory_scope(
        self,
        scope_type: str,
        scope_key: str,
        *,
        idempotency_key: Optional[str] = None,
        expected_scope_version: Optional[int] = None,
    ) -> int:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                replay = self._memory_idempotency_replay(conn, idempotency_key, "reset")
                if replay is not None:
                    conn.commit()
                    return int(replay["result_count"] or 0)
                current_scope_version = self._memory_scope_version(
                    conn, scope_type, scope_key
                )
                if (
                    expected_scope_version is not None
                    and current_scope_version != expected_scope_version
                ):
                    raise ValueError("version_conflict")
                ids = [
                    str(row["id"])
                    for row in conn.execute(
                        "SELECT id FROM memory_entries WHERE scope_type=? AND scope_key=?",
                        (scope_type, scope_key),
                    ).fetchall()
                ]
                for entry_id in ids:
                    self._delete_memory_index_rows(conn, entry_id)
                cur = conn.execute(
                    "DELETE FROM memory_entries WHERE scope_type=? AND scope_key=?",
                    (scope_type, scope_key),
                )
                self._insert_memory_event(
                    conn,
                    "",
                    "reset",
                    {
                        "scope_type": scope_type,
                        "scope_key_hash": hashlib.sha256(
                            scope_key.encode("utf-8")
                        ).hexdigest(),
                        "deleted": max(0, cur.rowcount),
                    },
                )
                self._bump_memory_scope_version(conn, scope_type, scope_key)
                self._record_memory_idempotency(
                    conn,
                    idempotency_key,
                    "reset",
                    result_count=max(0, cur.rowcount),
                )
                conn.commit()
                return max(0, cur.rowcount)
            except Exception:
                conn.rollback()
                raise

    def search_memory_ids(
        self,
        query: str,
        *,
        scope_types: Optional[Sequence[str]] = None,
        scope_keys: Optional[Sequence[str]] = None,
        scope_pairs: Optional[Sequence[Tuple[str, str]]] = None,
        statuses: Optional[Sequence[str]] = None,
        limit: int = 64,
    ) -> List[str]:
        terms = terms_for_query(query)
        with self._lock:
            conn = self._connect()
            conditions: List[str] = []
            params: List[Any] = []
            if scope_types:
                conditions.append(
                    f"me.scope_type IN ({','.join('?' for _ in scope_types)})"
                )
                params.extend(scope_types)
            if scope_keys:
                conditions.append(
                    f"me.scope_key IN ({','.join('?' for _ in scope_keys)})"
                )
                params.extend(scope_keys)
            if scope_pairs:
                pair_clauses = " OR ".join(
                    "(me.scope_type = ? AND me.scope_key = ?)" for _ in scope_pairs
                )
                conditions.append(f"({pair_clauses})")
                for item in scope_pairs:
                    params.extend(item)
            if statuses:
                conditions.append(f"me.status IN ({','.join('?' for _ in statuses)})")
                params.extend(statuses)
            if not terms:
                where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
                rows = conn.execute(
                    f"""
                    SELECT me.id FROM memory_entries me
                    {where}
                    ORDER BY me.updated_at DESC, me.id
                    LIMIT ?
                    """,
                    (*params, max(1, int(limit))),
                ).fetchall()
                return [str(row["id"]) for row in rows]
            backend = _memory_backend(conn)
            if backend == "fts5":
                match = format_query_terms(terms)
                try:
                    rows = conn.execute(
                        """
                        SELECT DISTINCT me.id
                        FROM memory_entries me
                        WHERE me.id IN (
                            SELECT entry_id FROM memory_fts WHERE memory_fts MATCH ?
                        )
                        """
                        + (f" AND {' AND '.join(conditions)}" if conditions else "")
                        + " ORDER BY CASE me.scope_type "
                        "WHEN 'conversation' THEN 3 WHEN 'workspace' THEN 2 ELSE 1 END DESC, "
                        "me.id LIMIT ?",
                        (match, *params, max(1, int(limit))),
                    ).fetchall()
                    return [str(row["id"]) for row in rows]
                except sqlite3.OperationalError:
                    # FTS query syntax failure falls back to the deterministic
                    # terms table rather than failing retrieval.
                    pass
            placeholders = ",".join("?" for _ in terms)
            rows = conn.execute(
                """
                SELECT DISTINCT me.id
                FROM memory_entries me
                JOIN memory_terms mt ON mt.entry_id = me.id
                WHERE mt.term IN (%s)
                """
                % placeholders
                + (f" AND {' AND '.join(conditions)}" if conditions else "")
                + " GROUP BY me.id ORDER BY COUNT(DISTINCT mt.term) DESC, "
                "CASE me.scope_type WHEN 'conversation' THEN 3 "
                "WHEN 'workspace' THEN 2 ELSE 1 END DESC, me.id LIMIT ?",
                (*terms, *params, max(1, int(limit))),
            ).fetchall()
            return [str(row["id"]) for row in rows]

    def memory_rejected_hash_exists(
        self, normalized_hash: str, scope_type: str, scope_key: str
    ) -> bool:
        with self._lock:
            row = (
                self._connect()
                .execute(
                    """
                SELECT 1 FROM memory_entries
                WHERE status='rejected' AND normalized_hash=?
                  AND scope_type=? AND scope_key=?
                LIMIT 1
                """,
                    (normalized_hash, scope_type, scope_key),
                )
                .fetchone()
            )
        return row is not None

    def get_memory_meta(self, key: str) -> Optional[str]:
        with self._lock:
            row = (
                self._connect()
                .execute("SELECT value FROM memory_meta WHERE key=?", (key,))
                .fetchone()
            )
        return str(row["value"]) if row else None

    def get_memory_scope_version(self, scope_type: str, scope_key: str) -> int:
        with self._lock:
            return self._memory_scope_version(self._connect(), scope_type, scope_key)

    @staticmethod
    def _memory_scope_version(
        conn: sqlite3.Connection, scope_type: str, scope_key: str
    ) -> int:
        row = conn.execute(
            "SELECT version FROM memory_scope_versions "
            "WHERE scope_type=? AND scope_key=?",
            (scope_type, scope_key),
        ).fetchone()
        return int(row["version"]) if row is not None else 0

    @staticmethod
    def _bump_memory_scope_version(
        conn: sqlite3.Connection, scope_type: str, scope_key: str
    ) -> None:
        conn.execute(
            """
            INSERT INTO memory_scope_versions(scope_type, scope_key, version)
            VALUES (?, ?, 1)
            ON CONFLICT(scope_type, scope_key)
            DO UPDATE SET version=memory_scope_versions.version+1
            """,
            (scope_type, scope_key),
        )

    def get_memory_idempotency_result(
        self, idempotency_key: Optional[str], operation: str
    ) -> Optional[Dict[str, Any]]:
        if not idempotency_key:
            return None
        with self._lock:
            row = (
                self._connect()
                .execute(
                    "SELECT * FROM memory_idempotency WHERE idempotency_key=?",
                    (idempotency_key,),
                )
                .fetchone()
            )
        if row is not None and str(row["operation"]) != operation:
            raise ValueError("idempotency_conflict")
        return dict(row) if row is not None else None

    def set_memory_meta(self, key: str, value: str) -> None:
        with self._lock:
            conn = self._connect()
            conn.execute(
                "INSERT INTO memory_meta(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
            conn.commit()

    def record_memory_usage(
        self,
        *,
        turn_id: str,
        entry_id: str,
        rank: int,
        reason: str,
        snapshot_hash: str,
    ) -> None:
        row = self.get_memory_entry(entry_id)
        if row is None:
            return
        self.record_memory_projection_usage(
            turn_id=turn_id,
            entries=[row],
            reason=reason,
            snapshot_hash=snapshot_hash,
            ranks=[rank],
        )

    def record_memory_projection_usage(
        self,
        *,
        turn_id: str,
        entries: Sequence[Dict[str, Any]],
        reason: str,
        snapshot_hash: str,
        ranks: Optional[Sequence[int]] = None,
    ) -> None:
        """Persist one immutable projection audit atomically and idempotently."""
        with self._lock:
            conn = self._connect()
            now = _utcnow()
            try:
                conn.execute("BEGIN IMMEDIATE")
                for index, entry in enumerate(entries):
                    rank = int(ranks[index]) if ranks is not None else index + 1
                    cur = conn.execute(
                        """
                        INSERT OR IGNORE INTO memory_usage(
                            turn_id, entry_id, rank, reason, snapshot_hash, used_at,
                            scope_type, scope_key, kind, title,
                            source_conversation_id, source_turn_id
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            turn_id,
                            str(entry["id"]),
                            rank,
                            reason,
                            snapshot_hash,
                            now,
                            entry.get("scope_type"),
                            entry.get("scope_key"),
                            entry.get("kind"),
                            entry.get("title"),
                            entry.get("source_conversation_id"),
                            entry.get("source_turn_id"),
                        ),
                    )
                    if cur.rowcount:
                        conn.execute(
                            """
                            UPDATE memory_entries
                            SET use_count=use_count+1, last_used_at=?
                            WHERE id=?
                            """,
                            (now, str(entry["id"])),
                        )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def verify_memory_index(self) -> bool:
        with self._lock:
            conn = self._connect()
            rows = conn.execute(
                "SELECT id, content FROM memory_entries WHERE status='confirmed'"
            ).fetchall()
            expected_terms = {
                str(row["id"]): set(tokenize(str(row["content"]))) for row in rows
            }
            term_rows = conn.execute(
                "SELECT entry_id, term FROM memory_terms ORDER BY entry_id, term"
            ).fetchall()
            actual_terms: Dict[str, set[str]] = {}
            for row in term_rows:
                actual_terms.setdefault(str(row["entry_id"]), set()).add(
                    str(row["term"])
                )
            expected_terms = {
                entry_id: terms for entry_id, terms in expected_terms.items() if terms
            }
            if expected_terms != actual_terms:
                return False
            if _memory_backend(conn) == "fts5":
                expected_ids = {str(row["id"]) for row in rows}
                actual_ids = {
                    str(row["entry_id"])
                    for row in conn.execute(
                        "SELECT entry_id FROM memory_fts"
                    ).fetchall()
                }
                return expected_ids == actual_ids
            return True

    def ensure_memory_index(self) -> str:
        """Self-check and rebuild the active index, falling back if FTS is unusable."""
        try:
            valid = self.verify_memory_index()
        except sqlite3.OperationalError:
            valid = False
            self.set_memory_meta("index_backend", "terms")
        if not valid:
            self.rebuild_memory_index()
        if not self.verify_memory_index():
            raise RuntimeError("memory_index_rebuild_failed")
        with self._lock:
            return _memory_backend(self._connect())

    def rebuild_memory_index(self) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute("DELETE FROM memory_terms")
                if _memory_backend(conn) == "fts5":
                    conn.execute("DELETE FROM memory_fts")
                rows = conn.execute(
                    "SELECT id, content FROM memory_entries WHERE status='confirmed'"
                ).fetchall()
                for row in rows:
                    entry_id = str(row["id"])
                    for term in tokenize(str(row["content"])):
                        conn.execute(
                            "INSERT OR IGNORE INTO memory_terms(entry_id, term) "
                            "VALUES (?, ?)",
                            (entry_id, term),
                        )
                    if _memory_backend(conn) == "fts5":
                        conn.execute(
                            "INSERT OR REPLACE INTO memory_fts(entry_id, search_text) "
                            "VALUES (?, ?)",
                            (entry_id, searchable_text(str(row["content"]))),
                        )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def list_memory_usage(self, turn_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            rows = (
                self._connect()
                .execute(
                    """
                SELECT u.turn_id, u.entry_id, u.rank, u.reason, u.snapshot_hash,
                       u.used_at,
                       COALESCE(e.scope_type, u.scope_type) AS scope_type,
                       COALESCE(e.scope_key, u.scope_key) AS scope_key,
                       COALESCE(e.kind, u.kind) AS kind,
                       COALESCE(e.title, u.title) AS title,
                       COALESCE(e.source_conversation_id, u.source_conversation_id)
                           AS source_conversation_id,
                       COALESCE(e.source_turn_id, u.source_turn_id) AS source_turn_id
                FROM memory_usage u
                LEFT JOIN memory_entries e ON e.id = u.entry_id
                WHERE u.turn_id=?
                ORDER BY u.rank
                """,
                    (turn_id,),
                )
                .fetchall()
            )
        return [dict(row) for row in rows]

    def log_memory_event(
        self, entry_id: str, kind: str, payload: Dict[str, Any]
    ) -> None:
        with self._lock:
            conn = self._connect()
            self._insert_memory_event(conn, entry_id, kind, payload)
            conn.commit()

    @staticmethod
    def _memory_idempotency_replay(
        conn: sqlite3.Connection,
        idempotency_key: Optional[str],
        operation: str,
    ) -> Optional[sqlite3.Row]:
        if not idempotency_key:
            return None
        row = conn.execute(
            "SELECT * FROM memory_idempotency WHERE idempotency_key=?",
            (idempotency_key,),
        ).fetchone()
        if row is not None and str(row["operation"]) != operation:
            raise ValueError("idempotency_conflict")
        return row

    @staticmethod
    def _record_memory_idempotency(
        conn: sqlite3.Connection,
        idempotency_key: Optional[str],
        operation: str,
        *,
        target_id: Optional[str] = None,
        result_version: Optional[int] = None,
        result_count: Optional[int] = None,
    ) -> None:
        if not idempotency_key:
            return
        conn.execute(
            """
            INSERT INTO memory_idempotency(
                idempotency_key, operation, target_id, result_version,
                result_count, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                idempotency_key,
                operation,
                target_id,
                result_version,
                result_count,
                _utcnow(),
            ),
        )

    @staticmethod
    def _insert_memory_event(
        conn: sqlite3.Connection,
        entry_id: str,
        kind: str,
        payload: Dict[str, Any],
    ) -> None:
        conn.execute(
            """
            INSERT INTO memory_events(id, entry_id, kind, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                uuid.uuid4().hex,
                entry_id or None,
                kind,
                json.dumps(payload, ensure_ascii=False, sort_keys=True)
                if payload
                else "{}",
                _utcnow(),
            ),
        )

    def _insert_memory_row(
        self, conn: sqlite3.Connection, data: Dict[str, Any]
    ) -> None:
        conn.execute(
            """
            INSERT INTO memory_entries(
                id, scope_type, scope_key, kind, title, content, status,
                confirmation, source_conversation_id, source_turn_id,
                source_excerpt, supersedes_id, version, normalized_hash,
                created_at, updated_at, last_used_at, use_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["id"],
                data["scope_type"],
                data["scope_key"],
                data["kind"],
                data.get("title"),
                data["content"],
                data["status"],
                data["confirmation"],
                data.get("source_conversation_id"),
                data.get("source_turn_id"),
                data.get("source_excerpt"),
                data.get("supersedes_id"),
                int(data.get("version", 1)),
                data["normalized_hash"],
                data["created_at"],
                data["updated_at"],
                data.get("last_used_at"),
                int(data.get("use_count", 0)),
            ),
        )
        if data.get("source_conversation_id") or data.get("source_turn_id"):
            conn.execute(
                """
                INSERT INTO memory_sources(
                    id, entry_id, conversation_id, turn_id, excerpt, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    uuid.uuid4().hex,
                    data["id"],
                    data.get("source_conversation_id"),
                    data.get("source_turn_id"),
                    data.get("source_excerpt"),
                    data["created_at"],
                ),
            )
        if str(data.get("status")) == "confirmed":
            self._insert_memory_index_rows(
                conn, str(data["id"]), str(data.get("content", ""))
            )

    @staticmethod
    def _insert_memory_index_rows(
        conn: sqlite3.Connection, entry_id: str, content: str
    ) -> None:
        terms = tokenize(content)
        for term in terms:
            conn.execute(
                "INSERT OR IGNORE INTO memory_terms(entry_id, term) VALUES (?, ?)",
                (entry_id, term),
            )
        if _memory_backend(conn) == "fts5":
            conn.execute(
                "INSERT OR REPLACE INTO memory_fts(entry_id, search_text) "
                "VALUES (?, ?)",
                (entry_id, searchable_text(content)),
            )

    def _delete_memory_index_rows(
        self,
        conn: sqlite3.Connection,
        entry_id: str,
        *,
        include_sources: bool = True,
    ) -> None:
        if include_sources:
            conn.execute("DELETE FROM memory_sources WHERE entry_id=?", (entry_id,))
        conn.execute("DELETE FROM memory_terms WHERE entry_id=?", (entry_id,))
        if _memory_backend(conn) == "fts5":
            conn.execute("DELETE FROM memory_fts WHERE entry_id=?", (entry_id,))
        # Deliberately keep memory_usage rows: they contain no secret content
        # and preserve the audit trail of past turns after a hard delete.

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
