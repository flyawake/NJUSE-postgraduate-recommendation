"""RunController tests: single active run, cancellation, bounded events.

All offline: a ScriptedModel (from conftest) drives AgentLoop, workspaces are
tmp_path directories and the profile store/credentials live under CODING_AGENT_HOME.
"""

from __future__ import annotations

import sys
import time

import pytest

from coding_agent.web.controller import RunController, RunControllerError
from coding_agent.web.schemas import RunStartRequest
from conftest import ScriptedModel, make_tool_call, turn


def _scripted_loop_model(*turns):
    """client_factory that ignores the connection and returns a ScriptedModel."""
    model = ScriptedModel(turns)

    def factory(_connection):
        return model

    return factory, model


def _verify_turns():
    """Full glob/grep/read/edit/verify/final trajectory used by several tests."""
    return (
        turn(calls=[make_tool_call("glob", {"pattern": "src/**/*.py"})]),
        turn(
            calls=[
                make_tool_call(
                    "grep", {"pattern": "TODO", "path": ".", "include": "*.py"}
                )
            ]
        ),
        turn(
            calls=[
                make_tool_call(
                    "read_file", {"path": "src/app.py", "offset": 1, "limit": 200}
                )
            ]
        ),
        turn(
            calls=[
                make_tool_call(
                    "edit_file",
                    {
                        "path": "src/app.py",
                        "old_string": "# TODO: implement add",
                        "new_string": "def add(a, b):\n    return a + b",
                    },
                )
            ]
        ),
        turn(
            calls=[
                make_tool_call(
                    "run_command",
                    {
                        "argv": [sys.executable, "-m", "py_compile", "src/app.py"],
                        "cwd": ".",
                        "timeout_seconds": 30,
                        "purpose": "verify",
                    },
                )
            ]
        ),
        turn(text="已完成：实现 add 并通过 py_compile 验证。"),
    )


def _seed_workspace(path):
    (path / "src").mkdir(parents=True)
    (path / "src" / "app.py").write_text(
        "def sub(a, b):\n    return a - b\n\n# TODO: implement add\n", encoding="utf-8"
    )


def _wait_terminal(controller, timeout=30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if controller.snapshot().state == "terminal":
            return
        time.sleep(0.05)
    raise AssertionError("run did not reach terminal state in time")


class BlockingModel:
    """Fake model: sleeps a while per request (still interruptible between steps)."""

    def __init__(self, delay: float = 30.0) -> None:
        self.delay = delay
        self.finished = False

    def request(self, messages, tools):
        self.finished = True
        time.sleep(self.delay)
        return turn(text="done")


@pytest.fixture
def home(tmp_path):
    return tmp_path / "home"


@pytest.fixture
def seeded(tmp_path):
    _seed_workspace(tmp_path)
    return tmp_path


def make_controller(
    tmp_path, *, factory, env=None, max_events=1000, max_event_chars=1_000_000
):
    return RunController(
        home=tmp_path / "home",
        env=env or {},
        client_factory=factory,
        max_events=max_events,
        max_event_chars=max_event_chars,
    )


class TestStartAndTerminal:
    def test_full_loop_reaches_verified_terminal(self, seeded):
        factory, model = _scripted_loop_model(*_verify_turns())
        controller = make_controller(seeded, factory=factory)
        # Profile is optional: legacy env fallback needs OPENAI_API_KEY.
        controller._env["OPENAI_API_KEY"] = "sk-test"
        controller._env["OPENAI_MODEL"] = "fake-model"
        snap = controller.start(RunStartRequest(workspace=str(seeded), task="实现 add"))
        assert snap.state == "running"
        assert snap.run_id
        run_id = snap.run_id
        _wait_terminal(controller)
        snap = controller.snapshot()
        assert snap.state == "terminal"
        assert snap.status == "SUCCESS"
        assert snap.stop_reason == "FINAL_ANSWER"
        assert snap.verification_status == "VERIFIED"
        assert snap.mutated_paths == ["src/app.py"]
        assert snap.step_count >= 1
        assert snap.tool_call_count == 5
        assert snap.run_id == run_id
        assert snap.error is None
        kinds = [event.kind for event in snap.events]
        assert kinds[0] == "run_started"
        assert kinds[-1] == "run_finished"
        assert len([k for k in kinds if k == "tool_finished"]) == 5

    def test_start_validates_before_worker(self, tmp_path):
        factory, _ = _scripted_loop_model(turn(text="nope"))
        controller = make_controller(tmp_path, factory=factory)
        with pytest.raises(RunControllerError) as exc:
            controller.start(
                RunStartRequest(workspace=str(tmp_path / "missing"), task="x")
            )
        assert exc.value.code == "invalid_workspace"
        assert controller.snapshot().state == "idle"
        with pytest.raises(RunControllerError) as exc:
            controller.start(RunStartRequest(workspace=str(tmp_path), task="   "))
        assert exc.value.code == "invalid_task"

    def test_missing_legacy_config_fails_without_worker(self, seeded):
        factory, _ = _scripted_loop_model(turn(text="nope"))
        controller = make_controller(seeded, factory=factory)
        with pytest.raises(RunControllerError) as exc:
            controller.start(RunStartRequest(workspace=str(seeded), task="实现 add"))
        assert exc.value.code == "invalid_config"
        assert controller.snapshot().state == "idle"

    def test_duplicate_start_returns_stable_conflict(self, seeded):
        factory = lambda _connection: BlockingModel(delay=3.0)  # noqa: E731
        controller = make_controller(seeded, factory=factory)
        controller._env["OPENAI_API_KEY"] = "sk-test"
        controller._env["OPENAI_MODEL"] = "fake-model"
        controller.start(RunStartRequest(workspace=str(seeded), task="实现 add"))
        with pytest.raises(RunControllerError) as exc:
            controller.start(RunStartRequest(workspace=str(seeded), task="second"))
        assert exc.value.code == "run_already_active"
        # The active run survives the rejected start and can still be cancelled.
        controller.cancel()
        _wait_terminal(controller)
        assert controller.snapshot().status == "INTERRUPTED"

    def test_terminal_snapshot_is_unique(self, seeded):
        factory, _ = _scripted_loop_model(turn(text="done"))
        controller = make_controller(seeded, factory=factory)
        controller._env["OPENAI_API_KEY"] = "sk-test"
        controller._env["OPENAI_MODEL"] = "fake-model"
        controller.start(RunStartRequest(workspace=str(seeded), task="t"))
        _wait_terminal(controller)
        first = controller.snapshot()
        time.sleep(0.05)
        second = controller.snapshot()
        assert first.finished_at == second.finished_at
        assert first.state == second.state == "terminal"


class TestCancellation:
    def test_cancel_interrupts_worker(self, seeded):
        factory = lambda _connection: BlockingModel(delay=3.0)  # noqa: E731
        controller = make_controller(seeded, factory=factory)
        controller._env["OPENAI_API_KEY"] = "sk-test"
        controller._env["OPENAI_MODEL"] = "fake-model"
        start = time.monotonic()
        snap = controller.start(RunStartRequest(workspace=str(seeded), task="t"))
        assert snap.state == "running"
        cancelled = controller.cancel()
        assert cancelled.state == "running"
        _wait_terminal(controller, timeout=15)
        snap = controller.snapshot()
        assert snap.status == "INTERRUPTED"
        assert snap.stop_reason == "INTERRUPTED"
        # Cancellation must be recognized quickly, not wait for the full delay.
        assert time.monotonic() - start < 15
        # Idempotent cancel of a terminal run returns the same snapshot.
        again = controller.cancel()
        assert again.state == "terminal"
        assert again.finished_at == snap.finished_at

    def test_cancel_with_no_run_raises(self, seeded):
        factory, _ = _scripted_loop_model(turn(text="done"))
        controller = make_controller(seeded, factory=factory)
        with pytest.raises(RunControllerError) as exc:
            controller.cancel()
        assert exc.value.code == "run_not_found"


class TestBoundedEvents:
    def test_events_bounded_by_count_with_resync(self, seeded):
        factory, _ = _scripted_loop_model(*_verify_turns())
        controller = make_controller(seeded, factory=factory, max_events=3)
        controller._env["OPENAI_API_KEY"] = "sk-test"
        controller._env["OPENAI_MODEL"] = "fake-model"
        controller.start(RunStartRequest(workspace=str(seeded), task="t"))
        _wait_terminal(controller)
        snap = controller.snapshot()
        assert len(snap.events) <= 3
        assert snap.events_total > 3
        assert snap.events_retained_from == snap.events[0].id
        # Falling behind the retained tail forces a reset.
        events, reset = controller.take_events(last_id=1)
        assert reset is True
        assert len(events) == len(snap.events)
        # Incremental fetch from the retained head works.
        events, reset = controller.take_events(last_id=snap.events[0].id - 1)
        assert reset is False
        assert events[0].id == snap.events[0].id
        # Newest id returns nothing.
        events, reset = controller.take_events(last_id=snap.events[-1].id)
        assert events == [] and reset is False

    def test_events_bounded_by_chars(self, seeded):
        factory, _ = _scripted_loop_model(*_verify_turns())
        controller = make_controller(seeded, factory=factory, max_event_chars=200)
        controller._env["OPENAI_API_KEY"] = "sk-test"
        controller._env["OPENAI_MODEL"] = "fake-model"
        controller.start(RunStartRequest(workspace=str(seeded), task="t"))
        _wait_terminal(controller)
        snap = controller.snapshot()
        assert 0 < len(snap.events) < snap.events_total
