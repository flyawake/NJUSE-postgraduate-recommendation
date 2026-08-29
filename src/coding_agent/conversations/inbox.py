"""InboxPort: the only safe-point interface AgentLoop uses for steer delivery.

The AgentLoop never touches SQLite directly. It calls this synchronous port at
the two documented safe boundaries. The port performs a transactional claim of
one ``steer_pending`` item bound to the running turn, then the loop appends the
user message and acknowledges delivery.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from .store import SQLiteConversationRepository


class InboxPort:
    def __init__(
        self,
        repository: SQLiteConversationRepository,
        conversation_id: str,
        turn_id: str,
    ) -> None:
        self._repo = repository
        self._cid = conversation_id
        self._tid = turn_id
        self._last_boundary = 0

    def poll_steer(self, boundary_id: int) -> Optional[Dict[str, Any]]:
        if boundary_id <= self._last_boundary:
            return None
        self._last_boundary = boundary_id
        return self._repo.get_steer_pending_for_turn(self._cid, self._tid)

    def claim_steer(self, item_id: str) -> bool:
        return self._repo.mark_steer_claimed_for_delivery(self._cid, self._tid, item_id)

    def deliver_steer(self, item_id: str) -> None:
        self._repo.mark_item_delivered(self._cid, item_id, source="steer")
