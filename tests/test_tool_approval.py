from __future__ import annotations

import threading
import time

from coding_agent.tools.approval import PermissionBroker
from coding_agent.tools.base import ToolEffect
from coding_agent.tools.policy import InteractiveWorkspaceToolPolicy, PolicyDecision


def _wait_pending(broker: PermissionBroker):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        pending = broker.list_pending("conversation", "turn")
        if pending:
            return pending[0]
        time.sleep(0.01)
    raise AssertionError("permission request did not become pending")


def test_host_command_waits_for_one_time_user_approval():
    broker = PermissionBroker()
    policy = InteractiveWorkspaceToolPolicy(
        broker,
        conversation_id="conversation",
        turn_id="turn",
        is_cancelled=lambda: False,
    )
    result = {}

    thread = threading.Thread(
        target=lambda: result.setdefault(
            "value",
            policy.decide(
                ToolEffect.EXECUTE,
                "run_command",
                {"argv": ["python", "-c", "print(1)"], "cwd": ".", "purpose": "verify"},
                "call-1",
            ),
        )
    )
    thread.start()
    pending = _wait_pending(broker)
    assert thread.is_alive()
    assert pending["argv"] == ["python", "-c", "print(1)"]
    broker.resolve("conversation", "turn", pending["id"], allow=True)
    thread.join(timeout=3)
    assert result["value"].decision is PolicyDecision.ALLOW


def test_cancellation_denies_permission_without_starting_a_process():
    broker = PermissionBroker()
    cancelled = {"value": False}
    policy = InteractiveWorkspaceToolPolicy(
        broker,
        conversation_id="conversation",
        turn_id="turn",
        is_cancelled=lambda: cancelled["value"],
    )
    result = {}
    thread = threading.Thread(
        target=lambda: result.setdefault(
            "value",
            policy.decide(
                ToolEffect.EXECUTE,
                "run_command",
                {"argv": ["python"], "cwd": ".", "purpose": "other"},
                "call-1",
            ),
        )
    )
    thread.start()
    _wait_pending(broker)
    cancelled["value"] = True
    thread.join(timeout=3)
    assert result["value"].decision is PolicyDecision.DENY


def test_conversation_policy_can_allow_or_deny_without_a_prompt():
    broker = PermissionBroker()
    mode = {"value": "allow"}
    policy = InteractiveWorkspaceToolPolicy(
        broker,
        conversation_id="conversation",
        turn_id="turn",
        is_cancelled=lambda: False,
        command_policy=lambda: mode["value"],
    )
    args = {"argv": ["python", "-V"], "cwd": ".", "purpose": "verify"}

    allowed = policy.decide(ToolEffect.EXECUTE, "run_command", args, "call-allow")
    assert allowed.decision is PolicyDecision.ALLOW
    assert broker.list_pending("conversation", "turn") == []

    mode["value"] = "deny"
    denied = policy.decide(ToolEffect.EXECUTE, "run_command", args, "call-deny")
    assert denied.decision is PolicyDecision.DENY
    assert broker.list_pending("conversation", "turn") == []


def test_persisted_policy_change_resolves_an_existing_prompt():
    broker = PermissionBroker()
    policy = InteractiveWorkspaceToolPolicy(
        broker,
        conversation_id="conversation",
        turn_id="turn",
        is_cancelled=lambda: False,
    )
    result = {}
    thread = threading.Thread(
        target=lambda: result.setdefault(
            "value",
            policy.decide(
                ToolEffect.EXECUTE,
                "run_command",
                {"argv": ["python", "-V"], "cwd": ".", "purpose": "verify"},
                "call-1",
            ),
        )
    )
    thread.start()
    _wait_pending(broker)
    broker.resolve_conversation("conversation", allow=True)
    thread.join(timeout=3)
    assert result["value"].decision is PolicyDecision.ALLOW
