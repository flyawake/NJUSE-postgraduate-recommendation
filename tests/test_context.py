"""ContextManager tests: append-only history and deterministic projection."""

from __future__ import annotations

import json

import pytest

from coding_agent.context import (
    CanonicalHistory,
    ContextManager,
    _compaction_marker,
    to_provider_message,
)
from coding_agent.errors import ContextOverflowError
from coding_agent.models import (
    AssistantMessage,
    SystemMessage,
    ToolMessage,
    UserMessage,
)
from conftest import make_tool_call


def history_with_steps() -> CanonicalHistory:
    history = CanonicalHistory()
    history.append(SystemMessage("system"))
    history.append(UserMessage("original task", source="user"))
    # Step 1: glob + large successful result (replaceable later).
    glob_call = make_tool_call("glob", {"pattern": "*.py"}, "g1")
    history.append(AssistantMessage("looking", (glob_call,)))
    history.append(
        ToolMessage(
            tool_call_id="g1",
            content='{"ok":true,"data":{"matches":["' + "x" * 5000 + '"]}}',
            tool_name="glob",
            ok=True,
            resource_key=".::*.py",
        )
    )
    # Step 2: read_file success for a.py (latest read for a.py).
    read_call = make_tool_call("read_file", {"path": "a.py"}, "r1")
    history.append(AssistantMessage("reading", (read_call,)))
    history.append(
        ToolMessage(
            tool_call_id="r1",
            content='{"ok":true,"data":{"path":"a.py","lines":[' + '"y" * 4000' + "]}}",
            tool_name="read_file",
            ok=True,
            resource_key="a.py",
            is_read_success=True,
            file_path="a.py",
        )
    )
    # Step 3: an error result that must always be kept.
    fail_call = make_tool_call("grep", {"pattern": "("}, "g2")
    history.append(AssistantMessage("searching", (fail_call,)))
    history.append(
        ToolMessage(
            tool_call_id="g2",
            content='{"ok":false,"error":{"code":"INVALID_ARGUMENT","message":"bad"}}',
            tool_name="grep",
            ok=False,
            resource_key=".::(",
        )
    )
    # Step 4: edit success (recent step, kept in full).
    edit_call = make_tool_call("edit_file", {"path": "a.py"}, "e1")
    history.append(AssistantMessage("editing", (edit_call,)))
    history.append(
        ToolMessage(
            tool_call_id="e1",
            content='{"ok":true,"data":{"path":"a.py","replacements":1}}',
            tool_name="edit_file",
            ok=True,
            resource_key="a.py",
        )
    )
    return history


def test_history_is_append_only_and_projection_does_not_mutate_it():
    history = history_with_steps()
    manager = ContextManager(1_000_000)
    before = history.messages
    manager.build_request(history)
    assert history.messages == before
    with pytest.raises(AttributeError):
        history.messages = ()  # property has no setter


def test_projection_is_deterministic():
    history = history_with_steps()
    manager = ContextManager(5_000)
    first = manager.build_request(history)
    second = manager.build_request(history)
    assert first == second
    assert first.messages == second.messages
    assert first.char_count == second.char_count


def test_projection_preserves_skeleton_and_compacts_old_success_bodies():
    history = history_with_steps()
    manager = ContextManager(3_000)
    view = manager.build_request(history)
    assert view.compacted_results >= 1
    tool_messages = [message for message in view.messages if message["role"] == "tool"]
    assert len(tool_messages) == 4
    # The old glob result was compacted to a marker.
    old_glob = tool_messages[0]
    parsed = json.loads(old_glob["content"])
    assert parsed["ok"] is True
    assert parsed["omitted"] is True
    assert parsed["tool"] == "glob"
    assert parsed["original_chars"] > len(old_glob["content"])
    # Recent step and error result keep full content.
    assert "replacements" in tool_messages[-1]["content"]
    error_contents = [
        m["content"] for m in tool_messages if '"ok":false' in m["content"]
    ]
    assert len(error_contents) == 1


def test_latest_read_window_per_file_is_kept_even_in_old_steps():
    history = CanonicalHistory()
    history.append(SystemMessage("s"))
    history.append(UserMessage("t"))

    def add_read(step: int) -> None:
        call = make_tool_call("read_file", {"path": "a.py"}, f"r{step}")
        # Old reads carry large bodies so the budget forces their compaction;
        # the latest read is small and must survive the protection rule.
        body = "x" * 2_000 if step < 3 else "content 3"
        history.append(AssistantMessage("", (call,)))
        history.append(
            ToolMessage(
                tool_call_id=f"r{step}",
                content=json.dumps(
                    {
                        "ok": True,
                        "data": {
                            "path": "a.py",
                            "fingerprint": f"v{step}",
                            "lines": [body],
                        },
                    }
                ),
                tool_name="read_file",
                ok=True,
                resource_key="a.py",
                is_read_success=True,
                file_path="a.py",
            )
        )

    def add_glob(step: int) -> None:
        call = make_tool_call("glob", {"pattern": f"*.{step}"}, f"g{step}")
        history.append(AssistantMessage("", (call,)))
        history.append(
            ToolMessage(
                tool_call_id=f"g{step}",
                content=json.dumps({"ok": True, "data": {"pattern": f"*.{step}"}}),
                tool_name="glob",
                ok=True,
                resource_key=f".::*.{step}",
            )
        )

    for step in range(4):
        add_read(step)  # latest read for a.py is step 3, an old step
    add_glob(4)
    add_glob(5)  # recent steps contain only globs

    manager = ContextManager(3_000)
    view = manager.build_request(history)
    tool_messages = [m for m in view.messages if m["role"] == "tool"]
    contents = [m["content"] for m in tool_messages]
    assert sum(1 for c in contents if '"content 3"' in c) == 1
    assert sum(1 for c in contents if '"content 0"' in c) == 0
    assert sum(1 for c in contents if '"omitted":true' in c) >= 2


def test_tight_budget_compacts_oldest_first():
    history = CanonicalHistory()
    history.append(SystemMessage("s"))
    history.append(UserMessage("t"))
    for step in range(4):
        call = make_tool_call("glob", {"pattern": f"*.{step}"}, f"g{step}")
        history.append(AssistantMessage("", (call,)))
        history.append(
            ToolMessage(
                tool_call_id=f"g{step}",
                content=json.dumps(
                    {
                        "ok": True,
                        "data": {"pattern": f"*.{step}", "matches": ["x" * 1000]},
                    }
                ),
                tool_name="glob",
                ok=True,
                resource_key=f".::*.{step}",
            )
        )
    manager = ContextManager(3_400)
    view = manager.build_request(history)
    tool_messages = [m for m in view.messages if m["role"] == "tool"]
    compacted = [
        json.loads(m["content"])
        for m in tool_messages
        if '"omitted":true' in m["content"]
    ]
    full = [
        json.loads(m["content"])
        for m in tool_messages
        if '"omitted":true' not in m["content"]
    ]
    assert len(compacted) >= 1
    # The two most recent steps are protected and stay full.
    assert {item["data"]["pattern"] for item in full} >= {"*.2", "*.3"}
    assert view.char_count <= 3_400


def test_protected_overflow_raises():
    history = CanonicalHistory()
    history.append(SystemMessage("s" * 100))
    history.append(UserMessage("t" * 100))
    manager = ContextManager(10)
    with pytest.raises(ContextOverflowError) as excinfo:
        manager.build_request(history)
    assert excinfo.value.budget == 10
    assert excinfo.value.char_count > 10


def test_auto_compacts_old_assistant_reasoning():
    history = CanonicalHistory()
    history.append(SystemMessage("system"))
    history.append(UserMessage("task", source="user"))
    history.append(AssistantMessage("short answer", (), reasoning="r" * 50_000))
    manager = ContextManager(1_000)
    view = manager.build_request(history)
    assert view.compacted_assistants >= 1
    assert view.char_count <= 1_000
    assistant = view.messages[-1]
    assert assistant["role"] == "assistant"
    assert "reasoning truncated by context manager" in assistant["reasoning_content"]
    assert len(assistant["reasoning_content"]) < 1_000


def test_recent_steps_keep_full_reasoning_and_text():
    history = CanonicalHistory()
    history.append(SystemMessage("system"))
    history.append(UserMessage("task", source="user"))
    call = make_tool_call("glob", {"pattern": "*.py"}, "g1")
    history.append(AssistantMessage("short", (call,), reasoning="r" * 20_000))
    history.append(
        ToolMessage(
            tool_call_id="g1",
            content='{"ok":true,"data":{"matches":[]}}',
            tool_name="glob",
            ok=True,
            resource_key=".::*.py",
        )
    )
    manager = ContextManager(100_000)
    view = manager.build_request(history)
    assert view.compacted_assistants == 0
    assistant = view.messages[-2]
    assert assistant["reasoning_content"] == "r" * 20_000


def test_default_char_budget_is_258k():
    from coding_agent.context import DEFAULT_CHAR_BUDGET

    assert DEFAULT_CHAR_BUDGET == 258_000


def test_provider_message_shapes():
    call = make_tool_call("glob", {"pattern": "*.py"}, "c1")
    assert to_provider_message(AssistantMessage("", (call,))) == {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "c1",
                "type": "function",
                "function": {"name": "glob", "arguments": '{"pattern": "*.py"}'},
            }
        ],
    }
    assert to_provider_message(UserMessage("x", source="completion_policy")) == {
        "role": "user",
        "content": "[completion-policy] x",
    }
    assert to_provider_message(ToolMessage("c1", "{}", "glob", True, ".")) == {
        "role": "tool",
        "tool_call_id": "c1",
        "content": "{}",
    }


def test_compaction_marker_is_deterministic():
    message = ToolMessage("c1", '{"data": "' + "z" * 100 + '"}', "glob", True, ".::*")
    assert _compaction_marker(message) == _compaction_marker(message)
    parsed = json.loads(_compaction_marker(message))
    assert parsed["omitted_chars"] > 0
    assert parsed["original_chars"] == len(message.content)


def test_char_budget_validation():
    with pytest.raises(ValueError):
        ContextManager(0)


def test_steps_only_start_at_assistant_tool_turns():
    history = CanonicalHistory()
    history.append(SystemMessage("s"))
    history.append(UserMessage("t"))
    history.append(AssistantMessage("no tools", ()))
    manager = ContextManager()
    assert manager._segment_steps(list(history.messages)) == []
