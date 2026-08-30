"""run_command behavior tests (A7)."""

from __future__ import annotations

import json
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from coding_agent.models import ToolCall
from coding_agent.tools import build_default_tools
from coding_agent.tools.base import (
    INVALID_ARGUMENT,
    PATH_NOT_ALLOWED,
    TIMEOUT,
    TOOL_ABORTED,
)
from coding_agent.tools.executor import ToolExecutor
from coding_agent.tools.observation import FileObservationTracker
from coding_agent.tools.paths import Workspace
from coding_agent.tools.policy import WorkspaceToolPolicy


@pytest.fixture
def env(tmp_path):
    tracker = FileObservationTracker()
    registry = build_default_tools(Workspace(tmp_path), tracker)
    return tmp_path, tracker, ToolExecutor(registry, WorkspaceToolPolicy())


def run(executor, call_id, args):
    return executor.run(ToolCall(call_id, "run_command", json.dumps(args)))


def test_run_command_captures_stdout_and_zero_exit(env):
    root, _tracker, executor = env
    outcome = run(
        executor,
        "c1",
        {
            "argv": [sys.executable, "-c", "print('hello world')"],
            "cwd": ".",
            "timeout_seconds": 10,
            "purpose": "inspect",
        },
    )
    assert outcome.ok is True
    assert "hello world" in outcome.data["stdout"]
    assert outcome.data["returncode"] == 0
    assert outcome.data["purpose"] == "inspect"


def test_nonzero_exit_is_a_successful_observation(env):
    _root, _tracker, executor = env
    outcome = run(
        executor,
        "c1",
        {
            "argv": [
                sys.executable,
                "-c",
                "import sys; print('oops', file=sys.stderr); sys.exit(3)",
            ],
            "purpose": "verify",
        },
    )
    assert outcome.ok is True
    assert outcome.data["returncode"] == 3
    assert "oops" in outcome.data["stderr"]


def test_cwd_is_restricted_to_workspace(env):
    root, _tracker, executor = env
    (root / "sub").mkdir()
    outcome = run(
        executor,
        "c1",
        {
            "argv": [sys.executable, "-c", "open('made.txt', 'w').write('x')"],
            "cwd": "sub",
            "purpose": "other",
        },
    )
    assert outcome.ok is True
    assert (root / "sub" / "made.txt").exists()

    for bad_cwd in ("../", "/tmp", "C:/Windows"):
        outcome = run(executor, "c2", {"argv": ["cmd"], "cwd": bad_cwd})
        assert outcome.ok is False
        assert outcome.error.code == PATH_NOT_ALLOWED


def test_validator_rejects_bad_argv_timeout_and_purpose(env):
    _root, _tracker, executor = env
    cases = [
        {"argv": []},
        {"argv": "python"},
        {"argv": ["python", 123]},
        {"argv": ["python"], "timeout_seconds": 0},
        {"argv": ["python"], "timeout_seconds": 121},
        {"argv": ["python"], "purpose": "evil"},
    ]
    for args in cases:
        outcome = run(executor, "c1", args)
        assert outcome.ok is False
        assert outcome.error.code == INVALID_ARGUMENT


def test_popen_uses_shell_false_and_correct_cwd(env, monkeypatch):
    import coding_agent.tools.run_command_tool as module

    root, _tracker, executor = env

    class FakeProcess:
        returncode = 0

        def communicate(self, timeout=None):
            return b"out", b"err"

    captured = {}

    class FakePopen:
        returncode = 0

        def __init__(self, argv, **kwargs):
            captured["argv"] = argv
            captured["kwargs"] = kwargs

        def communicate(self, timeout=None):
            return b"out", b"err"

        def poll(self):
            return 0

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(module.subprocess, "Popen", FakePopen)
    outcome = run(
        executor,
        "c1",
        {"argv": ["python", "-c", "print(1)"], "cwd": ".", "purpose": "other"},
    )
    assert outcome.ok is True
    assert captured["kwargs"]["shell"] is False
    assert captured["kwargs"]["cwd"] == str(root)
    assert captured["argv"] == ["python", "-c", "print(1)"]


def test_head_tail_retention_and_omitted_report(env, monkeypatch):
    import coding_agent.tools.run_command_tool as module

    _root, _tracker, executor = env

    class FakePopen:
        returncode = 0

        def __init__(self, argv, **kwargs):
            pass

        def communicate(self, timeout=None):
            return b"a" * 20_000, b"b" * 9_000

        def poll(self):
            return 0

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(module.subprocess, "Popen", FakePopen)
    outcome = run(executor, "c1", {"argv": ["x"], "purpose": "other"})
    assert outcome.ok is True
    assert len(outcome.data["stdout"]) == 10_000
    assert outcome.data["stdout"][:400] == "a" * 400
    assert outcome.data["stdout"][-600:] == "a" * 600
    assert outcome.data["stdout_truncated"] is True
    assert outcome.data["stdout_omitted"] == 10_000
    assert outcome.data["stderr_truncated"] is False


def test_real_process_output_is_drained_into_constant_size_head_tail(env):
    _root, _tracker, executor = env
    outcome = run(
        executor,
        "c1",
        {
            "argv": [sys.executable, "-c", "import sys; sys.stdout.write('a'*2000000)"],
            "timeout_seconds": 10,
            "purpose": "inspect",
        },
    )
    assert outcome.ok is True
    assert len(outcome.data["stdout"]) == 10_000
    assert outcome.data["stdout_omitted"] == 1_990_000
    assert outcome.data["stdout_truncated"] is True


def test_windows_taskkill_failure_falls_back_to_process_kill(monkeypatch):
    import coding_agent.tools.run_command_tool as module

    class FakeProcess:
        pid = 123

        def __init__(self):
            self.killed = False

        def poll(self):
            return None

        def kill(self):
            self.killed = True

    process = FakeProcess()
    monkeypatch.setattr(module.os, "name", "nt")
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=1),
    )
    module._terminate_tree(process)
    assert process.killed is True


def test_timeout_kills_long_command(env):
    _root, _tracker, executor = env
    start = time.monotonic()
    outcome = run(
        executor,
        "c1",
        {
            "argv": [sys.executable, "-c", "import time; time.sleep(30)"],
            "timeout_seconds": 1,
            "purpose": "other",
        },
    )
    elapsed = time.monotonic() - start
    assert outcome.ok is False
    assert outcome.error.code == TIMEOUT
    assert elapsed < 15


def test_cancellation_aborts_running_command(tmp_path):
    flag = {"cancel": False}
    tracker = FileObservationTracker()
    registry = build_default_tools(Workspace(tmp_path), tracker, lambda: flag["cancel"])
    executor = ToolExecutor(registry, WorkspaceToolPolicy(), lambda: flag["cancel"])
    box = {}

    def target():
        box["outcome"] = executor.run(
            ToolCall(
                "c1",
                "run_command",
                json.dumps(
                    {
                        "argv": [sys.executable, "-c", "import time; time.sleep(30)"],
                        "timeout_seconds": 30,
                        "purpose": "other",
                    }
                ),
            )
        )

    thread = threading.Thread(target=target)
    thread.start()
    time.sleep(0.6)
    flag["cancel"] = True
    thread.join(timeout=10)
    assert not thread.is_alive()
    outcome = box["outcome"]
    assert outcome.ok is False
    assert outcome.error.code == TOOL_ABORTED


def test_command_not_found_is_stable_error(env):
    _root, _tracker, executor = env
    outcome = run(
        executor,
        "c1",
        {"argv": ["definitely-not-a-real-command-xyz"], "purpose": "other"},
    )
    assert outcome.ok is False
    assert outcome.error.code == "COMMAND_NOT_FOUND"
