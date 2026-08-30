"""Canonical journal adapter that persists AgentLoop appends in atomic groups.

AgentLoop remains the owner of the in-memory canonical history. This journal is
injected only when a turn runs under a Conversation so every appended canonical
message is mirrored to the SQLite fact source using the same group discipline:

* user / system / non-tool assistant messages become committed single-item
  groups;
* an assistant message containing tool calls opens a pending group;
* tool results are appended to that group and the group is committed only when
  every call id has a matching result;
* if the process dies before commit, the pending group stays pending and is
  recovered as INTERRUPTED at startup instead of being sent to a provider.
"""

from __future__ import annotations

from typing import Optional, Set

from ..models import AssistantMessage, CanonicalMessage, ToolMessage
from .store import SQLiteConversationRepository


class CanonicalJournal:
    def __init__(
        self,
        repository: SQLiteConversationRepository,
        conversation_id: str,
        turn_id: str,
    ) -> None:
        self._repo = repository
        self._conversation_id = conversation_id
        self._turn_id = turn_id
        self._group_id: Optional[str] = None
        self._expected_calls: Set[str] = set()
        self._received_results: Set[str] = set()

    def append(self, message: CanonicalMessage) -> None:
        if isinstance(message, AssistantMessage) and message.tool_calls:
            self._begin_tool_group(message)
            return
        if isinstance(message, ToolMessage) and self._group_id is not None:
            self._append_tool_result(message)
            return
        self._append_simple(message)

    def close(self) -> None:
        """Called by the service after a turn ends.

        A pending tool group means the worker did not finish all results. The
        service is responsible for recovery: this method only abandons the
        group so it can never be treated as committed canonical history.
        """
        if (
            self._group_id is not None
            and self._expected_calls != self._received_results
        ):
            self._repo.abandon_pending_groups_for_turn(
                self._conversation_id, self._turn_id
            )
        self._group_id = None
        self._expected_calls = set()
        self._received_results = set()

    def abandon_current_tool_group(self) -> None:
        """Fail closed when one provider tool group violates the protocol."""
        if self._group_id is not None:
            self._repo.abandon_pending_groups_for_turn(
                self._conversation_id, self._turn_id
            )
        self._group_id = None
        self._expected_calls = set()
        self._received_results = set()

    def _begin_tool_group(self, message: AssistantMessage) -> None:
        self._group_id, _ = self._repo.begin_canonical_group(
            self._conversation_id, self._turn_id, kind="tool"
        )
        self._expected_calls = {call.id for call in message.tool_calls if call.id}
        self._received_results = set()
        self._store_item(message)

    def _append_tool_result(self, message: ToolMessage) -> None:
        self._store_item(message)
        if message.tool_call_id:
            self._received_results.add(message.tool_call_id)
        if self._expected_calls and self._expected_calls <= self._received_results:
            self._repo.commit_canonical_group(self._group_id or "")
            self._group_id = None
            self._expected_calls = set()
            self._received_results = set()

    def _append_simple(self, message: CanonicalMessage) -> None:
        group_id, _ = self._repo.begin_canonical_group(
            self._conversation_id, self._turn_id, kind="simple"
        )
        self._store_item_in_group(group_id, message)
        self._repo.commit_canonical_group(group_id)

    def _store_item(self, message: CanonicalMessage) -> None:
        assert self._group_id is not None
        self._store_item_in_group(self._group_id, message)

    def _store_item_in_group(self, group_id: str, message: CanonicalMessage) -> None:
        seq = self._repo.next_canonical_seq(self._conversation_id)
        self._repo.append_canonical_item(
            self._conversation_id,
            self._turn_id,
            group_id,
            seq,
            message,
        )
