"""Behavior tests for glob, grep and read_file (A5, A6)."""

from __future__ import annotations

import hashlib
import json

import pytest

from coding_agent.models import ToolCall
from coding_agent.tools import build_default_tools
from coding_agent.tools.base import (
    DECODE_ERROR,
    INVALID_ARGUMENT,
    PATH_NOT_ALLOWED,
    RESOURCE_LIMIT,
)
from coding_agent.tools.executor import ToolExecutor
from coding_agent.tools.observation import FileObservationTracker
from coding_agent.tools.paths import Workspace
from coding_agent.tools.policy import WorkspaceToolPolicy


@pytest.fixture
def env(tmp_path):
    tracker = FileObservationTracker()
    registry = build_default_tools(Workspace(tmp_path), tracker)
    executor = ToolExecutor(registry, WorkspaceToolPolicy())
    return tmp_path, tracker, executor


def run(executor, call_id, name, args):
    return executor.run(ToolCall(call_id, name, json.dumps(args, ensure_ascii=False)))


# ---------------------------------------------------------------- glob


def test_glob_finds_files_and_reports_paths(env):
    root, _tracker, executor = env
    (root / "a.py").write_text("", encoding="utf-8")
    (root / "sub").mkdir()
    (root / "sub" / "b.py").write_text("", encoding="utf-8")
    (root / "c.txt").write_text("", encoding="utf-8")
    outcome = run(executor, "g1", "glob", {"pattern": "**/*.py"})
    assert outcome.ok is True
    assert outcome.data["count"] == 2
    assert outcome.data["matches"] == ["a.py", "sub/b.py"]
    assert outcome.data["omitted_count"] == 0


def test_glob_skips_vcs_venv_and_cache_dirs(env):
    root, _tracker, executor = env
    for directory in (".git", ".venv", "node_modules", "__pycache__", "dist", "build"):
        (root / directory).mkdir()
        (root / directory / "skip.py").write_text("", encoding="utf-8")
    (root / "keep.py").write_text("", encoding="utf-8")
    outcome = run(executor, "g1", "glob", {"pattern": "*.py"})
    assert outcome.data["matches"] == ["keep.py"]


def test_glob_caps_at_100_and_reports_omitted(env):
    root, _tracker, executor = env
    for index in range(120):
        (root / f"f{index:03}.py").write_text("", encoding="utf-8")
    outcome = run(executor, "g1", "glob", {"pattern": "*.py"})
    assert outcome.data["count"] == 100
    assert len(outcome.data["matches"]) == 100
    assert outcome.data["omitted_count"] == 1
    assert outcome.data["omitted_count_is_lower_bound"] is True
    assert outcome.data["search_truncated"] is True
    assert outcome.data["hint"]


def test_glob_empty_match_is_success(env):
    _root, _tracker, executor = env
    outcome = run(executor, "g1", "glob", {"pattern": "*.nope"})
    assert outcome.ok is True
    assert outcome.data["count"] == 0
    assert outcome.data["matches"] == []


def test_glob_stops_at_directory_entry_budget(env, monkeypatch):
    import coding_agent.tools.glob_tool as module

    root, _tracker, executor = env
    for name in ("a.py", "b.py", "c.py"):
        (root / name).write_text("", encoding="utf-8")
    monkeypatch.setattr(module, "MAX_SCANNED_ENTRIES", 2)
    outcome = run(executor, "g1", "glob", {"pattern": "*.py"})
    assert outcome.data["scanned_entries"] == 2
    assert outcome.data["search_truncated"] is True
    assert len(outcome.data["matches"]) <= 2


def test_glob_rejects_absolute_pattern_and_traversal(env):
    _root, _tracker, executor = env
    for pattern in ("/etc/*", "C:/windows/*", "../*.py"):
        outcome = run(executor, "g1", "glob", {"pattern": pattern})
        assert outcome.ok is False
        assert outcome.error.code == INVALID_ARGUMENT


def test_glob_rejects_path_escape(env):
    _root, _tracker, executor = env
    outcome = run(executor, "g1", "glob", {"pattern": "*.py", "path": "../outside"})
    assert outcome.ok is False
    assert outcome.error.code == PATH_NOT_ALLOWED


# ---------------------------------------------------------------- grep


def test_grep_returns_file_line_and_preview(env):
    root, _tracker, executor = env
    (root / "a.py").write_text("alpha\nbeta needle\nomega\n", encoding="utf-8")
    outcome = run(executor, "g1", "grep", {"pattern": "needle"})
    assert outcome.ok is True
    assert outcome.data["match_count"] == 1
    match = outcome.data["matches"][0]
    assert match["file"] == "a.py"
    assert match["line_number"] == 2
    assert match["text"] == "beta needle"


def test_grep_no_match_is_success_empty(env):
    root, _tracker, executor = env
    (root / "a.py").write_text("hello\n", encoding="utf-8")
    outcome = run(executor, "g1", "grep", {"pattern": "zzz"})
    assert outcome.ok is True
    assert outcome.data["match_count"] == 0
    assert outcome.data["matches"] == []


def test_grep_invalid_regex_is_stable_error(env):
    _root, _tracker, executor = env
    outcome = run(executor, "g1", "grep", {"pattern": "("})
    assert outcome.ok is False
    assert outcome.error.code == INVALID_ARGUMENT


def test_grep_include_filter_and_preview_cap(env):
    root, _tracker, executor = env
    (root / "a.py").write_text("needle\n", encoding="utf-8")
    (root / "b.txt").write_text("needle\n", encoding="utf-8")
    outcome = run(executor, "g1", "grep", {"pattern": "needle", "include": "*.py"})
    assert outcome.data["match_count"] == 1
    assert outcome.data["matches"][0]["file"] == "a.py"

    long_line = "needle" + "x" * 3_000
    (root / "long.txt").write_text(long_line + "\n", encoding="utf-8")
    outcome = run(executor, "g2", "grep", {"pattern": "needle"})
    long_matches = [m for m in outcome.data["matches"] if m["file"] == "long.txt"]
    assert len(long_matches[0]["text"]) == 2_000
    assert long_matches[0]["truncated"] is True


def test_grep_caps_matches_at_200(env):
    root, _tracker, executor = env
    (root / "a.py").write_text("needle\n" * 250, encoding="utf-8")
    outcome = run(executor, "g1", "grep", {"pattern": "needle"})
    assert outcome.data["match_count"] == 200
    assert len(outcome.data["matches"]) == 200
    assert outcome.data["omitted_count"] == 1
    assert outcome.data["omitted_count_is_lower_bound"] is True
    assert outcome.data["search_truncated"] is True


def test_grep_skips_binary_files(env):
    root, _tracker, executor = env
    (root / "bin.dat").write_bytes(b"\xff\xfe\x00needle")
    (root / "a.py").write_text("needle\n", encoding="utf-8")
    outcome = run(executor, "g1", "grep", {"pattern": "needle"})
    assert outcome.ok is True
    assert outcome.data["skipped_files"] == 1
    assert outcome.data["match_count"] == 1


def test_grep_rejects_path_escape(env):
    _root, _tracker, executor = env
    outcome = run(executor, "g1", "grep", {"pattern": "x", "path": "../"})
    assert outcome.ok is False
    assert outcome.error.code == PATH_NOT_ALLOWED


# ------------------------------------------------------------ read_file


def test_read_file_window_with_line_numbers_and_next_offset(env):
    root, tracker, executor = env
    (root / "a.py").write_text("l1\nl2\nl3\nl4\n", encoding="utf-8")
    outcome = run(
        executor, "r1", "read_file", {"path": "a.py", "offset": 2, "limit": 2}
    )
    assert outcome.ok is True
    assert outcome.data["total_lines"] == 4
    assert [line["number"] for line in outcome.data["lines"]] == [2, 3]
    assert outcome.data["next_offset"] == 4
    assert tracker.is_observed("a.py")
    assert tracker.current_matches("a.py", (root / "a.py").read_bytes())


def test_read_file_records_sha256_fingerprint(env):
    root, _tracker, executor = env
    raw = b"hello\nworld\n"
    (root / "a.py").write_bytes(raw)
    outcome = run(executor, "r1", "read_file", {"path": "a.py"})
    assert outcome.data["fingerprint"] == hashlib.sha256(raw).hexdigest()


def test_read_file_limit_capped_at_500(env):
    _root, _tracker, executor = env
    outcome = run(executor, "r1", "read_file", {"path": "a.py", "limit": 501})
    assert outcome.ok is False
    assert outcome.error.code == INVALID_ARGUMENT


def test_read_file_window_respects_50kib_cap(env):
    root, _tracker, executor = env
    (root / "big.txt").write_text(("x" * 1_000 + "\n") * 200, encoding="utf-8")
    outcome = run(executor, "r1", "read_file", {"path": "big.txt", "limit": 500})
    assert outcome.ok is True
    assert outcome.data["window_bytes"] <= 50 * 1024
    assert outcome.data["window_truncated"] is True
    assert outcome.data["omitted_lines"] > 0
    assert outcome.data["next_offset"] is not None


def test_read_file_truncates_individual_lines_at_2000(env):
    root, _tracker, executor = env
    (root / "long.txt").write_text("y" * 2_500 + "\n", encoding="utf-8")
    outcome = run(executor, "r1", "read_file", {"path": "long.txt"})
    line = outcome.data["lines"][0]
    assert len(line["text"]) == 2_000
    assert line["truncated"] is True


def test_read_file_binary_is_decode_error(env):
    root, _tracker, executor = env
    (root / "bin.dat").write_bytes(b"\xff\xfe\x00")
    outcome = run(executor, "r1", "read_file", {"path": "bin.dat"})
    assert outcome.ok is False
    assert outcome.error.code == DECODE_ERROR


def test_read_file_rejects_oversized_file_before_loading_it(env, monkeypatch):
    import coding_agent.tools.read_file_tool as module

    root, _tracker, executor = env
    target = root / "huge.txt"
    target.write_bytes(b"x")
    monkeypatch.setattr(module, "MAX_FILE_BYTES", 0)
    outcome = run(executor, "r1", "read_file", {"path": "huge.txt"})
    assert outcome.ok is False
    assert outcome.error.code == RESOURCE_LIMIT


def test_read_file_rejects_absolute_and_traversal(env):
    root, _tracker, executor = env
    (root / "a.py").write_text("x\n", encoding="utf-8")
    for path in (
        "/etc/passwd",
        "C:/Windows/win.ini",
        "../outside.txt",
        "..\\outside.txt",
    ):
        outcome = run(executor, "r1", "read_file", {"path": path})
        assert outcome.ok is False
        assert outcome.error.code == PATH_NOT_ALLOWED


def test_read_file_rejects_symlink_escape(env):
    root, _tracker, executor = env
    outside = root.parent / (root.name + "-outside")
    outside.mkdir(exist_ok=True)
    (outside / "secret.txt").write_text("secret\n", encoding="utf-8")
    link = root / "link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is not available on this platform")
    outcome = run(executor, "r1", "read_file", {"path": "link/secret.txt"})
    assert outcome.ok is False
    assert outcome.error.code == PATH_NOT_ALLOWED


def test_grep_does_not_read_file_symlink_escape(env):
    root, _tracker, executor = env
    outside = root.parent / (root.name + "-outside")
    outside.mkdir(exist_ok=True)
    (outside / "secret.txt").write_text("needle outside\n", encoding="utf-8")
    try:
        (root / "link.txt").symlink_to(outside / "secret.txt")
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is not available on this platform")
    outcome = run(executor, "g1", "grep", {"pattern": "needle"})
    assert outcome.ok is True
    assert outcome.data["match_count"] == 0
    assert outcome.data["matches"] == []


def test_glob_does_not_return_file_symlink_escape(env):
    root, _tracker, executor = env
    outside = root.parent / (root.name + "-outside")
    outside.mkdir(exist_ok=True)
    (outside / "secret.txt").write_text("outside\n", encoding="utf-8")
    try:
        (root / "link.txt").symlink_to(outside / "secret.txt")
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is not available on this platform")
    outcome = run(executor, "g1", "glob", {"pattern": "*.txt"})
    assert outcome.ok is True
    assert outcome.data["matches"] == []


def test_grep_and_glob_do_not_follow_directory_symlink_escape(env):
    root, _tracker, executor = env
    outside = root.parent / (root.name + "-outside-dir")
    outside.mkdir(exist_ok=True)
    (outside / "secret.txt").write_text("needle outside\n", encoding="utf-8")
    try:
        (root / "linked-dir").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is not available on this platform")
    grep_outcome = run(executor, "g1", "grep", {"pattern": "needle"})
    assert grep_outcome.data["match_count"] == 0
    glob_outcome = run(executor, "g2", "glob", {"pattern": "**/*.txt"})
    assert glob_outcome.data["matches"] == []


def test_read_file_observation_required_for_writes(env):
    root, tracker, executor = env
    (root / "a.py").write_text("old\n", encoding="utf-8")
    write = run(
        executor,
        "w1",
        "write_file",
        {"path": "a.py", "content": "new\n"},
    )
    assert write.ok is False
    assert write.error.code == "FILE_NOT_OBSERVED"
    assert (root / "a.py").read_text(encoding="utf-8") == "old\n"
    assert run(executor, "r1", "read_file", {"path": "a.py"}).ok is True
    write = run(executor, "w2", "write_file", {"path": "a.py", "content": "new\n"})
    assert write.ok is True
    assert (root / "a.py").read_text(encoding="utf-8") == "new\n"
