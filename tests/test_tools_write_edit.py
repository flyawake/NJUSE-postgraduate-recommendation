"""write_file/edit_file behavior tests (A5)."""

from __future__ import annotations

import json

import pytest

from coding_agent.models import ToolCall
from coding_agent.tools import build_default_tools
from coding_agent.tools.base import (
    CONTENT_TOO_LARGE,
    EDIT_MULTIPLE_MATCH,
    EDIT_NO_MATCH,
    FILE_NOT_OBSERVED,
    FILE_STALE,
    PATH_NOT_ALLOWED,
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


def run(executor, call_id, name, args):
    return executor.run(ToolCall(call_id, name, json.dumps(args, ensure_ascii=False)))


def read(env, path="a.py"):
    root, _tracker, executor = env
    return run(executor, "r1", "read_file", {"path": path})


def test_write_creates_new_file_and_parent_dirs(env):
    root, tracker, executor = env
    outcome = run(
        executor,
        "w1",
        "write_file",
        {"path": "nested/dir/new.txt", "content": "hello\n"},
    )
    assert outcome.ok is True
    assert outcome.data["created"] is True
    assert (root / "nested" / "dir" / "new.txt").read_text(
        encoding="utf-8"
    ) == "hello\n"
    assert tracker.is_observed("nested/dir/new.txt")


def test_write_overwrite_requires_observation(env):
    root, _tracker, executor = env
    (root / "a.py").write_text("old\n", encoding="utf-8")
    outcome = run(executor, "w1", "write_file", {"path": "a.py", "content": "new\n"})
    assert outcome.ok is False
    assert outcome.error.code == FILE_NOT_OBSERVED
    assert (root / "a.py").read_text(encoding="utf-8") == "old\n"


def test_write_stale_version_keeps_original(env):
    root, _tracker, executor = env
    (root / "a.py").write_text("v1\n", encoding="utf-8")
    assert read(env).ok is True
    (root / "a.py").write_text("v2\n", encoding="utf-8")
    outcome = run(executor, "w1", "write_file", {"path": "a.py", "content": "v3\n"})
    assert outcome.ok is False
    assert outcome.error.code == FILE_STALE
    assert (root / "a.py").read_text(encoding="utf-8") == "v2\n"


def test_write_after_fresh_read_replaces_atomically(env):
    root, _tracker, executor = env
    (root / "a.py").write_text("v1\n", encoding="utf-8")
    assert read(env).ok is True
    outcome = run(executor, "w1", "write_file", {"path": "a.py", "content": "v2\n"})
    assert outcome.ok is True
    assert (root / "a.py").read_text(encoding="utf-8") == "v2\n"
    assert not list(root.glob(".a.py.*.tmp"))


def test_edit_requires_observation_and_fresh_version(env):
    root, _tracker, executor = env
    (root / "a.py").write_text("one\n", encoding="utf-8")
    outcome = run(
        executor,
        "e1",
        "edit_file",
        {"path": "a.py", "old_string": "one", "new_string": "two"},
    )
    assert outcome.ok is False
    assert outcome.error.code == FILE_NOT_OBSERVED

    assert read(env).ok is True
    (root / "a.py").write_text("changed\n", encoding="utf-8")
    outcome = run(
        executor,
        "e2",
        "edit_file",
        {"path": "a.py", "old_string": "changed", "new_string": "two"},
    )
    assert outcome.ok is False
    assert outcome.error.code == FILE_STALE
    assert (root / "a.py").read_text(encoding="utf-8") == "changed\n"


def test_edit_unique_match_succeeds_and_refreshes_observation(env):
    root, tracker, executor = env
    (root / "a.py").write_text("one\ntwo\n", encoding="utf-8")
    assert read(env).ok is True
    outcome = run(
        executor,
        "e1",
        "edit_file",
        {"path": "a.py", "old_string": "two", "new_string": "TWO"},
    )
    assert outcome.ok is True
    assert outcome.data["replacements"] == 1
    assert (root / "a.py").read_text(encoding="utf-8") == "one\nTWO\n"
    assert tracker.current_matches("a.py", (root / "a.py").read_bytes())


def test_edit_no_match_and_multiple_match_keep_original(env):
    root, _tracker, executor = env
    (root / "a.py").write_text("x = 1\nx = 1\n", encoding="utf-8")
    assert read(env).ok is True
    no_match = run(
        executor,
        "e1",
        "edit_file",
        {"path": "a.py", "old_string": "absent", "new_string": "y"},
    )
    assert no_match.error.code == EDIT_NO_MATCH
    multiple = run(
        executor,
        "e2",
        "edit_file",
        {"path": "a.py", "old_string": "x = 1", "new_string": "y"},
    )
    assert multiple.error.code == EDIT_MULTIPLE_MATCH
    assert (root / "a.py").read_text(encoding="utf-8") == "x = 1\nx = 1\n"


def test_edit_replace_all_replaces_every_match(env):
    root, _tracker, executor = env
    (root / "a.py").write_text("x = 1\nx = 1\n", encoding="utf-8")
    assert read(env).ok is True
    outcome = run(
        executor,
        "e1",
        "edit_file",
        {
            "path": "a.py",
            "old_string": "x = 1",
            "new_string": "x = 2",
            "replace_all": True,
        },
    )
    assert outcome.ok is True
    assert outcome.data["replacements"] == 2
    assert (root / "a.py").read_text(encoding="utf-8") == "x = 2\nx = 2\n"


def test_write_and_edit_reject_escape_paths(env):
    root, _tracker, executor = env
    (root / "a.py").write_text("x\n", encoding="utf-8")
    assert read(env).ok is True
    for path in ("../outside.txt", "/etc/passwd", "C:/Windows/win.ini"):
        write = run(executor, "w1", "write_file", {"path": path, "content": "x"})
        assert write.ok is False
        assert write.error.code == PATH_NOT_ALLOWED
        edit = run(
            executor,
            "e1",
            "edit_file",
            {"path": path, "old_string": "x", "new_string": "y"},
        )
        assert edit.ok is False
        assert edit.error.code == PATH_NOT_ALLOWED


def test_write_content_cap_is_one_mib(env):
    root, _tracker, executor = env
    big = "x" * (1024 * 1024)
    outcome = run(executor, "w1", "write_file", {"path": "big.txt", "content": big})
    assert outcome.ok is True
    too_big = big + "y"
    outcome = run(
        executor, "w2", "write_file", {"path": "big2.txt", "content": too_big}
    )
    assert outcome.ok is False
    assert outcome.error.code == CONTENT_TOO_LARGE
    assert not (root / "big2.txt").exists()


def test_edit_resulting_file_respects_content_cap(env):
    root, _tracker, executor = env
    (root / "a.py").write_text("a\n", encoding="utf-8")
    assert read(env).ok is True
    outcome = run(
        executor,
        "e1",
        "edit_file",
        {"path": "a.py", "old_string": "a", "new_string": "z" * (1024 * 1024)},
    )
    assert outcome.ok is False
    assert outcome.error.code == CONTENT_TOO_LARGE
    assert (root / "a.py").read_text(encoding="utf-8") == "a\n"


def test_failed_atomic_replace_keeps_original(env, monkeypatch):
    import coding_agent.tools.file_io as file_io

    root, _tracker, executor = env
    (root / "a.py").write_text("old\n", encoding="utf-8")
    assert read(env).ok is True

    def broken_replace(src, dst):
        raise OSError("injected replace failure")

    monkeypatch.setattr(file_io.os, "replace", broken_replace)
    outcome = run(executor, "w1", "write_file", {"path": "a.py", "content": "new\n"})
    assert outcome.ok is False
    assert outcome.error.code == "WRITE_FAILED"
    assert (root / "a.py").read_text(encoding="utf-8") == "old\n"
    assert not list(root.glob(".a.py.*.tmp"))


def test_write_to_directory_is_rejected(env):
    root, _tracker, executor = env
    (root / "d").mkdir()
    outcome = run(executor, "w1", "write_file", {"path": "d", "content": "x"})
    assert outcome.ok is False
    assert outcome.error.code == "PATH_IS_DIRECTORY"
