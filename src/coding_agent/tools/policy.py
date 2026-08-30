"""Tool policy seam, including interactive host-execution approval."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Protocol

from .approval import PermissionBroker
from .base import ToolEffect


class PolicyDecision(str, Enum):
    ALLOW = "ALLOW"
    DENY = "DENY"


@dataclass(frozen=True)
class PolicyResult:
    decision: PolicyDecision
    reason: str = ""

    @classmethod
    def allow(cls) -> "PolicyResult":
        return cls(PolicyDecision.ALLOW)

    @classmethod
    def deny(cls, reason: str = "policy denied this tool call") -> "PolicyResult":
        return cls(PolicyDecision.DENY, reason=reason)


class ToolPolicy(Protocol):
    def decide(
        self,
        effect: ToolEffect,
        tool_name: str,
        normalized_args: dict,
        call_id: str,
    ) -> PolicyResult: ...


class WorkspaceToolPolicy:
    """Trusted non-interactive policy used by the CLI and direct library API.

    Workspace boundary enforcement happens inside the tool handlers and the
    path guard, not in the policy layer.
    """

    def decide(
        self,
        effect: ToolEffect,
        tool_name: str,
        normalized_args: dict,
        call_id: str,
    ) -> PolicyResult:
        return PolicyResult.allow()


class InteractiveWorkspaceToolPolicy:
    """Apply a live, conversation-scoped host-command permission policy."""

    def __init__(
        self,
        broker: PermissionBroker,
        *,
        conversation_id: str,
        turn_id: str,
        is_cancelled: Callable[[], bool],
        command_policy: Callable[[], str] = lambda: "ask",
    ) -> None:
        self._broker = broker
        self._conversation_id = conversation_id
        self._turn_id = turn_id
        self._is_cancelled = is_cancelled
        self._command_policy = command_policy

    def decide(
        self,
        effect: ToolEffect,
        tool_name: str,
        normalized_args: dict,
        call_id: str,
    ) -> PolicyResult:
        if effect is not ToolEffect.EXECUTE:
            return PolicyResult.allow()
        try:
            policy = self._command_policy()
        except Exception:
            return PolicyResult.deny("could not read conversation command policy")
        if policy == "allow":
            return PolicyResult.allow()
        if policy == "deny":
            return PolicyResult.deny("conversation policy denied host command")
        if policy != "ask":
            return PolicyResult.deny("invalid conversation command policy")
        allowed = self._broker.request_command(
            conversation_id=self._conversation_id,
            turn_id=self._turn_id,
            call_id=call_id,
            normalized_args=normalized_args,
            is_cancelled=self._is_cancelled,
        )
        if allowed:
            return PolicyResult.allow()
        return PolicyResult.deny("user denied or cancelled host command permission")
