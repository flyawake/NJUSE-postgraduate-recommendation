"""In-memory, per-call permission broker for privileged local execution.

The worker blocks before process creation while the local UI owns the decision.
Requests are deliberately ephemeral: an app restart interrupts the turn rather
than replaying a previously approved host command.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import PurePath
from typing import Any, Callable, Dict, List, Optional

APPROVAL_TIMEOUT_SECONDS = 10 * 60
APPROVAL_POLL_SECONDS = 0.2


@dataclass
class PermissionRequest:
    id: str
    conversation_id: str
    turn_id: str
    call_id: str
    tool_name: str
    executable: str
    argv: List[str]
    cwd: str
    purpose: str
    created_at: float
    _decision: Optional[bool] = None
    _event: threading.Event = field(default_factory=threading.Event, repr=False)

    def public_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "conversation_id": self.conversation_id,
            "turn_id": self.turn_id,
            "call_id": self.call_id,
            "tool_name": self.tool_name,
            "executable": self.executable,
            "argv": list(self.argv),
            "cwd": self.cwd,
            "purpose": self.purpose,
            "capabilities": [
                "start_host_process",
                "access_outside_workspace",
                "network_access",
                "inherit_host_environment",
            ],
            "created_at": self.created_at,
        }


class PermissionBroker:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._requests: Dict[str, PermissionRequest] = {}

    def request_command(
        self,
        *,
        conversation_id: str,
        turn_id: str,
        call_id: str,
        normalized_args: Dict[str, Any],
        is_cancelled: Callable[[], bool],
    ) -> bool:
        argv = [str(part) for part in normalized_args.get("argv", [])]
        executable = PurePath(argv[0]).name if argv else ""
        request = PermissionRequest(
            id=uuid.uuid4().hex,
            conversation_id=conversation_id,
            turn_id=turn_id,
            call_id=call_id,
            tool_name="run_command",
            executable=executable,
            argv=argv,
            cwd=str(normalized_args.get("cwd", ".")),
            purpose=str(normalized_args.get("purpose", "other")),
            created_at=time.time(),
        )
        with self._lock:
            self._requests[request.id] = request

        deadline = time.monotonic() + APPROVAL_TIMEOUT_SECONDS
        try:
            while True:
                if is_cancelled():
                    return False
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                request._event.wait(min(APPROVAL_POLL_SECONDS, remaining))
                with self._lock:
                    if request._decision is not None:
                        return request._decision
        finally:
            with self._lock:
                self._requests.pop(request.id, None)

    def list_pending(self, conversation_id: str, turn_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            items = [
                item.public_dict()
                for item in self._requests.values()
                if item.conversation_id == conversation_id
                and item.turn_id == turn_id
                and item._decision is None
            ]
        return sorted(items, key=lambda item: (item["created_at"], item["id"]))

    def resolve(
        self,
        conversation_id: str,
        turn_id: str,
        request_id: str,
        *,
        allow: bool,
    ) -> Optional[Dict[str, Any]]:
        with self._lock:
            request = self._requests.get(request_id)
            if (
                request is None
                or request.conversation_id != conversation_id
                or request.turn_id != turn_id
                or request._decision is not None
            ):
                return None
            request._decision = allow
            public = request.public_dict()
            public["decision"] = "allow" if allow else "deny"
            request._event.set()
            return public

    def cancel_turn(self, conversation_id: str, turn_id: str) -> None:
        with self._lock:
            for request in self._requests.values():
                if (
                    request.conversation_id == conversation_id
                    and request.turn_id == turn_id
                    and request._decision is None
                ):
                    request._decision = False
                    request._event.set()

    def resolve_conversation(self, conversation_id: str, *, allow: bool) -> None:
        """Resolve pending requests after a conversation policy is changed."""
        with self._lock:
            for request in self._requests.values():
                if (
                    request.conversation_id == conversation_id
                    and request._decision is None
                ):
                    request._decision = allow
                    request._event.set()

    def cancel_all(self) -> None:
        with self._lock:
            for request in self._requests.values():
                if request._decision is None:
                    request._decision = False
                    request._event.set()
