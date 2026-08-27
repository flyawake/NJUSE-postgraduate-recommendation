"""Tool policy seam.

MVP policy is non-interactive ALLOW/DENY; approval UI is explicitly out of
scope. AgentLoop owns call IDs, repeat detection and group cancellation;
policy decisions are evaluated inside ToolExecutor before any handler runs.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

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
    """Default MVP policy: allow every registered tool.

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
