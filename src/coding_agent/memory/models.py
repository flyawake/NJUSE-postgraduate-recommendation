"""Memory domain types and constants.

The model is intentionally simple: a memory entry is a user-controlled,
source-attributed fact with a lifecycle (candidate/confirmed/superseded/
rejected/deleted). The retrieval projection only reads active confirmed
entries; candidates must be approved by a human before they can be injected.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, Optional, Tuple

MEMORY_DEFAULT_TOP_K = 6
MEMORY_MAX_ENTRY_CHARS = 4_000
MEMORY_MAX_TITLE_CHARS = 120
MEMORY_MAX_SINGLE_PROJECTION_CHARS = 1_200
MEMORY_MAX_TOTAL_PROJECTION_CHARS = 6_000
MEMORY_MAX_TERMS_PER_ENTRY = 200


class MemoryScope(str, Enum):
    GLOBAL = "global"
    WORKSPACE = "workspace"
    CONVERSATION = "conversation"


class MemoryKind(str, Enum):
    PREFERENCE = "preference"
    FACT = "fact"
    DECISION = "decision"
    PROCEDURE = "procedure"


class MemoryStatus(str, Enum):
    CANDIDATE = "candidate"
    CONFIRMED = "confirmed"
    SUPERSEDED = "superseded"
    REJECTED = "rejected"
    DELETED = "deleted"


class MemoryConfirmation(str, Enum):
    EXPLICIT_UI = "explicit_ui"
    EXPLICIT_COMMAND = "explicit_command"
    USER_APPROVED = "user_approved"
    IMPORTED = "imported"


# Statuses that are visible in the active confirmed retrieval projection.
ACTIVE_MEMORY_STATUSES: Tuple[str, ...] = (MemoryStatus.CONFIRMED.value,)


@dataclass(frozen=True)
class MemoryEntry:
    id: str
    scope_type: str
    scope_key: str
    kind: str
    content: str
    status: str
    confirmation: str
    version: int
    created_at: str
    updated_at: str
    title: Optional[str] = None
    source_conversation_id: Optional[str] = None
    source_turn_id: Optional[str] = None
    source_excerpt: Optional[str] = None
    supersedes_id: Optional[str] = None
    normalized_hash: str = ""
    last_used_at: Optional[str] = None
    use_count: int = 0
    sources: Tuple[Dict[str, Any], ...] = field(default_factory=tuple)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "scope_type": self.scope_type,
            "scope_key": self.scope_key,
            "kind": self.kind,
            "title": self.title,
            "content": self.content,
            "status": self.status,
            "confirmation": self.confirmation,
            "source_conversation_id": self.source_conversation_id,
            "source_turn_id": self.source_turn_id,
            "source_excerpt": self.source_excerpt,
            "supersedes_id": self.supersedes_id,
            "version": self.version,
            "normalized_hash": self.normalized_hash,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_used_at": self.last_used_at,
            "use_count": self.use_count,
            "sources": list(self.sources),
        }


@dataclass(frozen=True)
class MemoryProjection:
    """One immutable retrieval snapshot used for a single turn."""

    block: str
    entries: Tuple[MemoryEntry, ...]
    snapshot_hash: str
    reason: str = "explicit_ui_memory"
    omitted_count: int = 0
    commit_usage: Optional[Callable[[], None]] = field(
        default=None, repr=False, compare=False
    )


@dataclass(frozen=True)
class MemoryUsageRecord:
    turn_id: str
    entry_id: str
    rank: int
    reason: str
    snapshot_hash: str
    used_at: str
    scope_type: str = ""
    scope_key: str = ""
    kind: str = ""
    title: Optional[str] = None
    source_conversation_id: Optional[str] = None
    source_turn_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "turn_id": self.turn_id,
            "entry_id": self.entry_id,
            "rank": self.rank,
            "reason": self.reason,
            "snapshot_hash": self.snapshot_hash,
            "used_at": self.used_at,
            "scope_type": self.scope_type,
            "scope_key": self.scope_key,
            "kind": self.kind,
            "title": self.title,
            "source_conversation_id": self.source_conversation_id,
            "source_turn_id": self.source_turn_id,
        }
