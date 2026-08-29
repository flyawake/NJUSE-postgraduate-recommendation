"""ToolSpec/Registry/Executor contract tests (A4)."""

from __future__ import annotations

import json
import sys

import pytest

from coding_agent.models import ToolCall
from coding_agent.tools import build_default_tools
from coding_agent.tools.base import (
    INTERNAL_ERROR,
    INVALID_ARGUMENT,
    INVALID_JSON_ARGS,
    POLICY_DENIED,
    UNKNOWN_TOOL,
    ToolEffect,
    ToolExecutionError,
    ToolSpec,
)
from coding_agent.tools.executor import ToolExecutor
from coding_agent.tools.observation import FileObservationTracker
from coding_agent.tools.paths import Workspace
from coding_agent.tools.policy import PolicyDecision, PolicyResult, WorkspaceToolPolicy
from coding_agent.tools.registry import ToolRegistry


@pytest.fixture
def tracker():
    return FileObservationTracker()


@pytest.fixture
def registry(tmp_path, tracker):
    return build_default_tools(Workspace(tmp_path), tracker)


EXPECTED_EFFECTS = {
    "glob": ToolEffect.READ,
    "grep": ToolEffect.READ,
    "read_file": ToolEffect.READ,
    "write_file": ToolEffect.WRITE,
    "edit_file": ToolEffect.WRITE,
    "run_command": ToolEffect.EXECUTE,
    "web_search": ToolEffect.READ,
    "web_fetch": ToolEffect.READ,
}


def test_all_default_tools_registered_with_contract(registry):
    assert list(registry.names()) == [
        "glob",
        "grep",
        "read_file",
        "write_file",
        "edit_file",
        "run_command",
        "web_search",
        "web_fetch",
    ]
    for name in registry.names():
        spec = registry.get(name)
        assert isinstance(spec, ToolSpec)
        assert spec.effect is EXPECTED_EFFECTS[name]
        schema = spec.schema
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False
        assert "properties" in schema and "required" in schema
        assert all(key in schema["properties"] for key in schema["required"])
        # Provider schema materialization mirrors the ToolSpec.
        provider = [
            t for t in registry.provider_tools() if t["function"]["name"] == name
        ][0]
        assert provider["type"] == "function"
        assert provider["function"]["parameters"] == schema


@pytest.mark.parametrize(
    ("name", "minimal_args"),
    [
        ("glob", {"pattern": "*.py"}),
        ("grep", {"pattern": "needle"}),
        ("read_file", {"path": "missing.txt"}),
        ("write_file", {"path": "new.txt", "content": "hello"}),
        ("edit_file", {"path": "missing.txt", "old_string": "a", "new_string": "b"}),
        (
            "run_command",
            {
                "argv": [sys.executable, "-c", "print('ok')"],
                "cwd": ".",
                "timeout_seconds": 5,
            },
        ),
        ("web_search", {"query": "Python release"}),
        ("web_fetch", {"url": "https://example.com/"}),
    ],
)
def test_validator_accepts_minimal_args_and_rejects_wrong_types(
    registry, name, minimal_args
):
    spec = registry.get(name)
    assert isinstance(spec.validator(minimal_args), dict)
    broken = dict(minimal_args)
    first_required = spec.schema["required"][0]
    broken[first_required] = 12345
    with pytest.raises(ToolExecutionError) as excinfo:
        spec.validator(broken)
    assert excinfo.value.code == INVALID_ARGUMENT


def test_executor_decodes_and_dispatches_success(tmp_path, tracker, registry):
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    executor = ToolExecutor(registry, WorkspaceToolPolicy())
    call = ToolCall("c1", "read_file", '{"path": "a.py"}')
    outcome = executor.run(call)
    assert outcome.ok is True
    assert outcome.data["path"] == "a.py"
    assert outcome.data["total_lines"] == 1
    parsed = json.loads(outcome.model_content())
    assert parsed["ok"] is True


def test_invalid_json_arguments_become_structured_error(registry):
    executor = ToolExecutor(registry, WorkspaceToolPolicy())
    outcome = executor.run(ToolCall("c1", "glob", "not json"))
    assert outcome.ok is False
    assert outcome.error.code == INVALID_JSON_ARGS
    assert json.loads(outcome.model_content())["error"]["code"] == INVALID_JSON_ARGS


def test_non_object_arguments_become_structured_error(registry):
    executor = ToolExecutor(registry, WorkspaceToolPolicy())
    outcome = executor.run(ToolCall("c1", "glob", "[1, 2]"))
    assert outcome.error is not None and outcome.error.code == INVALID_JSON_ARGS


def test_unknown_tool_is_structured_error(registry):
    executor = ToolExecutor(registry, WorkspaceToolPolicy())
    outcome = executor.run(ToolCall("c1", "no_such_tool", "{}"))
    assert outcome.error is not None and outcome.error.code == UNKNOWN_TOOL
    assert "no_such_tool" in outcome.error.message


def test_handler_crash_is_normalized_to_internal_error(registry):
    class BrokenPolicy:
        def decide(self, *args, **kwargs):
            return PolicyResult.allow()

    def broken_handler(_args):
        raise RuntimeError("boom")

    spec = ToolSpec(
        name="broken",
        description="broken tool",
        schema={"type": "object", "properties": {}, "additionalProperties": False},
        effect=ToolEffect.READ,
        validator=lambda args: args,
        handler=broken_handler,
    )
    local = ToolRegistry()
    local.register(spec)
    outcome = ToolExecutor(local, BrokenPolicy()).run(ToolCall("c1", "broken", "{}"))
    assert outcome.ok is False
    assert outcome.error is not None and outcome.error.code == INTERNAL_ERROR


def test_policy_denies_write_and_execute_before_side_effect(
    tmp_path, tracker, registry
):
    class DenyWriteExecute:
        def decide(self, effect, tool_name, normalized_args, call_id):
            if effect in (ToolEffect.WRITE, ToolEffect.EXECUTE):
                return PolicyResult(PolicyDecision.DENY, "test deny")
            return PolicyResult.allow()

    executor = ToolExecutor(registry, DenyWriteExecute())
    write = executor.run(
        ToolCall("cw", "write_file", '{"path": "blocked.txt", "content": "x"}')
    )
    assert write.ok is False
    assert write.error is not None and write.error.code == POLICY_DENIED
    assert write.call_id == "cw"
    assert not (tmp_path / "blocked.txt").exists()

    run = executor.run(
        ToolCall(
            "cr",
            "run_command",
            json.dumps({"argv": [sys.executable, "-c", "print('no')"]}),
        )
    )
    assert run.ok is False
    assert run.error is not None and run.error.code == POLICY_DENIED


def test_prepare_then_execute_matches_single_call_pipeline(tmp_path, tracker, registry):
    (tmp_path / "a.py").write_text("print('hi')\n", encoding="utf-8")
    executor = ToolExecutor(registry, WorkspaceToolPolicy())
    call = ToolCall("c1", "read_file", '{"path": "a.py"}')
    prepared = executor.prepare(call)
    assert prepared.error is None
    outcome = executor.execute(prepared)
    assert outcome.ok is True


def test_model_rendering_is_deterministic_json(tmp_path, tracker, registry):
    (tmp_path / "a.py").write_text("x\n", encoding="utf-8")
    executor = ToolExecutor(registry, WorkspaceToolPolicy())
    first = executor.run(ToolCall("c1", "read_file", '{"path": "a.py"}'))
    second = executor.run(ToolCall("c2", "read_file", '{"path": "a.py"}'))
    parsed = json.loads(first.model_content())
    assert parsed["ok"] is True
    assert first.model_content() == second.model_content()
    assert first.summary() != first.model_content()  # short display is separate


def test_summary_is_short_and_redacted(registry):
    executor = ToolExecutor(registry, WorkspaceToolPolicy())
    outcome = executor.run(
        ToolCall(
            "c1",
            "run_command",
            '{"argv": ["python", "-c", "print(1)"], "purpose": "inspect"}',
        )
    )
    assert len(outcome.summary()) <= 160
    assert "API" not in outcome.summary()
