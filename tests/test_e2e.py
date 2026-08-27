"""Offline end-to-end test (A9): scripted model + real workspace tools."""

from __future__ import annotations

import sys

from coding_agent.models import RunStatus, StopReason, VerificationStatus
from conftest import (
    ScriptedModel,
    assert_valid_event_stream,
    build_loop,
    history_is_paired,
    make_tool_call,
    turn,
)


def test_full_loop_glob_grep_read_edit_verify_answer_is_verified(tmp_path):
    (tmp_path / "app.py").write_text(
        "def target():\n    return 1\n\nprint(target())\n", encoding="utf-8"
    )
    turns = [
        turn(calls=[make_tool_call("glob", {"pattern": "*.py"})]),
        turn(
            calls=[make_tool_call("grep", {"pattern": "def target", "include": "*.py"})]
        ),
        turn(
            calls=[
                make_tool_call(
                    "read_file", {"path": "app.py", "offset": 1, "limit": 50}
                )
            ]
        ),
        turn(
            calls=[
                make_tool_call(
                    "edit_file",
                    {
                        "path": "app.py",
                        "old_string": "return 1",
                        "new_string": "return 42",
                    },
                )
            ]
        ),
        turn(
            calls=[
                make_tool_call(
                    "run_command",
                    {
                        "argv": [
                            sys.executable,
                            "-c",
                            (
                                "import pathlib; t = pathlib.Path('app.py').read_text(); "
                                "assert 'return 42' in t and 'return 1' not in t"
                            ),
                        ],
                        "cwd": ".",
                        "timeout_seconds": 30,
                        "purpose": "verify",
                    },
                )
            ]
        ),
        turn("完成：app.py 中的 target 已从返回 1 改为返回 42，并已用检查脚本验证。"),
    ]
    model = ScriptedModel(turns)
    loop, sink, _model = build_loop(tmp_path, model)
    result = loop.run("把 app.py 的 target 改成返回 42 并验证")

    assert result.status is RunStatus.SUCCESS
    assert result.stop_reason is StopReason.FINAL_ANSWER
    assert result.verification_status is VerificationStatus.VERIFIED
    assert result.mutated_paths == ("app.py",)
    assert result.step_count == 6
    assert result.provider_attempt_count == 6
    assert result.tool_call_count == 5
    assert "return 42" in (tmp_path / "app.py").read_text(encoding="utf-8")
    assert any(
        "def target" in request["messages"][-1]["content"] for request in model.requests
    )
    assert history_is_paired(loop.history)
    assert_valid_event_stream(sink.events)
    assert len(model.requests) == 6


def test_end_to_end_unverified_completion_is_deferred_once(tmp_path):
    write = make_tool_call("write_file", {"path": "new.txt", "content": "hello"})
    model = ScriptedModel(
        [turn(calls=[write]), turn("写完了"), turn("最终答复：文件已写入但未验证")]
    )
    loop, sink, _model = build_loop(tmp_path, model)
    result = loop.run("创建 new.txt")
    assert result.status is RunStatus.SUCCESS
    assert result.stop_reason is StopReason.FINAL_ANSWER
    assert result.verification_status is VerificationStatus.NOT_RUN
    assert sink.types().count("completion_deferred") == 1
    assert result.mutated_paths == ("new.txt",)
    assert history_is_paired(loop.history)
    assert_valid_event_stream(sink.events)
