"""AgentLoop state machine, invariants and policy tests (A3, A8)."""

from __future__ import annotations

import copy
import json
import sys
import threading
import time
from typing import List

import pytest

from coding_agent.models import (
    AssistantMessage,
    LoopPhase,
    RunStatus,
    StopReason,
    ToolMessage,
    VerificationStatus,
)
from coding_agent.tools.policy import PolicyResult, WorkspaceToolPolicy
from conftest import (
    AlwaysFailModel,
    FlakyModel,
    RecordingSink,
    ScriptedModel,
    assert_valid_event_stream,
    build_loop,
    history_is_paired,
    make_tool_call,
    turn,
)


def tool_contents(history, tool_name: str | None = None) -> List[str]:
    return [
        message.content
        for message in history
        if isinstance(message, ToolMessage)
        and (tool_name is None or message.tool_name == tool_name)
    ]


# ------------------------------------------------------------ completion


def test_no_changes_final_answer_is_not_applicable(tmp_path):
    model = ScriptedModel([turn("完成了，没有修改文件。")])
    loop, sink, _model = build_loop(tmp_path, model)
    result = loop.run("task")
    assert result.status is RunStatus.SUCCESS
    assert result.stop_reason is StopReason.FINAL_ANSWER
    assert result.verification_status is VerificationStatus.NOT_APPLICABLE
    assert result.step_count == 1
    assert result.provider_attempt_count == 1
    assert result.tool_call_count == 0
    assert result.final_text == "完成了，没有修改文件。"
    assert_valid_event_stream(sink.events)


def test_verified_completion_after_write_and_verify(tmp_path):
    write = make_tool_call("write_file", {"path": "new.txt", "content": "hello"})
    verify = make_tool_call(
        "run_command",
        {
            "argv": [
                sys.executable,
                "-c",
                "import pathlib; assert pathlib.Path('new.txt').read_text() == 'hello'",
            ],
            "purpose": "verify",
            "timeout_seconds": 30,
        },
    )
    model = ScriptedModel(
        [turn(calls=[write]), turn(calls=[verify]), turn("已验证完成")]
    )
    loop, sink, _model = build_loop(tmp_path, model)
    result = loop.run("create new.txt")
    assert result.status is RunStatus.SUCCESS
    assert result.stop_reason is StopReason.FINAL_ANSWER
    assert result.verification_status is VerificationStatus.VERIFIED
    assert result.mutated_paths == ("new.txt",)
    assert result.last_verification["exit_code"] == 0
    assert result.step_count == 3
    assert result.tool_call_count == 2
    assert history_is_paired(loop.history)
    assert_valid_event_stream(sink.events)


def test_agent_events_and_verification_are_redacted_before_any_sink(tmp_path):
    sentinel = "RAW-SINK-SENTINEL-DO-NOT-LEAK"
    write = make_tool_call("write_file", {"path": "new.txt", "content": sentinel})
    verify = make_tool_call(
        "run_command",
        {
            "argv": [
                sys.executable,
                "-c",
                "import sys; sys.exit(0)",
                f"FOO={sentinel}",
                f"--header=Bearer-{sentinel}",
                sentinel,
            ],
            "purpose": "verify",
        },
    )
    model = ScriptedModel([turn(calls=[write]), turn(calls=[verify]), turn("verified")])
    loop, sink, _model = build_loop(tmp_path, model)
    result = loop.run("task")

    assert result.status is RunStatus.SUCCESS
    assert sentinel not in repr([event.payload for event in sink.events])
    assert sentinel not in result.last_verification["command"]
    assert "FOO=***" in result.last_verification["command"]
    assert "--header=***" in result.last_verification["command"]


def test_failed_verification_defers_exactly_once(tmp_path):
    write = make_tool_call("write_file", {"path": "new.txt", "content": "hello"})
    verify_fail = make_tool_call(
        "run_command",
        {
            "argv": [sys.executable, "-c", "import sys; sys.exit(2)"],
            "purpose": "verify",
            "timeout_seconds": 30,
        },
    )
    model = ScriptedModel(
        [
            turn(calls=[write]),
            turn(calls=[verify_fail]),
            turn("还没验证"),
            turn("最终答复"),
        ]
    )
    loop, sink, _model = build_loop(tmp_path, model)
    result = loop.run("task")
    assert result.status is RunStatus.SUCCESS
    assert result.stop_reason is StopReason.FINAL_ANSWER
    assert result.verification_status is VerificationStatus.FAILED
    assert result.final_text == "最终答复"
    assert sink.types().count("completion_deferred") == 1
    assert sink.types().count("run_finished") == 1
    assert_valid_event_stream(sink.events)
    # The control message is appended after the deferred answer, so it is
    # visible in the fourth (final) model request with the documented prefix.
    second_request = model.requests[3]["messages"]
    control = [
        m
        for m in second_request
        if m["role"] == "user" and "[completion-policy]" in m["content"]
    ]
    assert len(control) == 1


def test_not_run_verification_defers_exactly_once(tmp_path):
    write = make_tool_call("write_file", {"path": "new.txt", "content": "hello"})
    model = ScriptedModel([turn(calls=[write]), turn("直接答复"), turn("最终答复")])
    loop, sink, _model = build_loop(tmp_path, model)
    result = loop.run("task")
    assert result.status is RunStatus.SUCCESS
    assert result.verification_status is VerificationStatus.NOT_RUN
    assert sink.types().count("completion_deferred") == 1
    assert result.final_text == "最终答复"
    assert_valid_event_stream(sink.events)


def test_last_step_allows_unverified_completion_without_deferral(tmp_path):
    write = make_tool_call("write_file", {"path": "new.txt", "content": "hello"})
    model = ScriptedModel([turn(calls=[write]), turn("没有步骤可验证了")])
    loop, sink, _model = build_loop(tmp_path, model, max_steps=2)
    result = loop.run("task")
    assert result.status is RunStatus.SUCCESS
    assert result.verification_status is VerificationStatus.NOT_RUN
    assert "completion_deferred" not in sink.types()
    assert result.step_count == 2


def test_verify_before_any_change_is_not_applicable(tmp_path):
    verify = make_tool_call(
        "run_command",
        {
            "argv": [sys.executable, "-c", "print('ok')"],
            "purpose": "verify",
            "timeout_seconds": 30,
        },
    )
    inspect = make_tool_call(
        "run_command",
        {
            "argv": [sys.executable, "-c", "print('info')"],
            "purpose": "inspect",
            "timeout_seconds": 30,
        },
    )
    model = ScriptedModel([turn(calls=[verify, inspect]), turn("done")])
    loop, _sink, _model = build_loop(tmp_path, model)
    result = loop.run("task")
    assert result.verification_status is VerificationStatus.NOT_APPLICABLE


def test_new_mutation_resets_previous_verification(tmp_path):
    write1 = make_tool_call("write_file", {"path": "a.txt", "content": "a"})
    verify_fail = make_tool_call(
        "run_command",
        {
            "argv": [sys.executable, "-c", "import sys; sys.exit(1)"],
            "purpose": "verify",
            "timeout_seconds": 30,
        },
    )
    write2 = make_tool_call("write_file", {"path": "b.txt", "content": "b"})
    model = ScriptedModel(
        [
            turn(calls=[write1]),
            turn(calls=[verify_fail]),
            turn(calls=[write2]),
            turn("先答复"),
            turn("最终答复"),
        ]
    )
    loop, sink, _model = build_loop(tmp_path, model)
    result = loop.run("task")
    assert result.verification_status is VerificationStatus.NOT_RUN
    assert result.mutated_paths == ("a.txt", "b.txt")
    assert sink.types().count("completion_deferred") == 1


# ------------------------------------------------------- protocol shape


def test_text_with_tool_calls_is_kept_and_does_not_finish(tmp_path):
    glob_call = make_tool_call("glob", {"pattern": "*.py"})
    model = ScriptedModel([turn("先探查", [glob_call]), turn("done")])
    loop, _sink, _model = build_loop(tmp_path, model)
    result = loop.run("task")
    assert result.final_text == "done"
    assistant_messages = [m for m in loop.history if isinstance(m, AssistantMessage)]
    assert assistant_messages[0].text == "先探查"


def test_multiple_tool_calls_execute_in_order_and_pair_exactly(tmp_path):
    (tmp_path / "a.py").write_text("x\n", encoding="utf-8")
    first = make_tool_call("read_file", {"path": "a.py"})
    second = make_tool_call("glob", {"pattern": "*.py"})
    model = ScriptedModel([turn(calls=[first, second]), turn("done")])
    loop, sink, _model = build_loop(tmp_path, model)
    result = loop.run("task")
    assert result.status is RunStatus.SUCCESS
    assert result.tool_call_count == 2
    contents = tool_contents(loop.history)
    assert '"path":"a.py"' in contents[0]
    assert '"pattern":"*.py"' in contents[1]
    second_request = model.requests[1]["messages"]
    tool_messages = [m for m in second_request if m["role"] == "tool"]
    assert len(tool_messages) == 2
    assert tool_messages[0]["tool_call_id"] == first.id
    assert tool_messages[1]["tool_call_id"] == second.id
    assert history_is_paired(loop.history)
    assert_valid_event_stream(sink.events)


def test_invalid_json_arguments_become_result_and_loop_continues(tmp_path):
    from coding_agent.models import ToolCall

    bad = ToolCall("bad1", "glob", "not json")
    model = ScriptedModel([turn(calls=[bad]), turn("已处理错误")])
    loop, _sink, _model = build_loop(tmp_path, model)
    result = loop.run("task")
    assert result.status is RunStatus.SUCCESS
    assert result.tool_call_count == 1
    assert "INVALID_JSON_ARGS" in tool_contents(loop.history)[0]
    assert history_is_paired(loop.history)


def test_unknown_tool_becomes_result_and_loop_continues(tmp_path):
    unknown = make_tool_call("no_such_tool", {})
    model = ScriptedModel([turn(calls=[unknown]), turn("done")])
    loop, _sink, _model = build_loop(tmp_path, model)
    result = loop.run("task")
    assert result.status is RunStatus.SUCCESS
    assert "UNKNOWN_TOOL" in tool_contents(loop.history)[0]


def test_duplicate_call_ids_terminate_with_protocol_error(tmp_path):
    first = make_tool_call("glob", {"pattern": "*.py"}, "dup")
    second = make_tool_call("glob", {"pattern": "*.txt"}, "dup")
    model = ScriptedModel([turn(calls=[first, second])])
    loop, sink, _model = build_loop(tmp_path, model)
    result = loop.run("task")
    assert result.status is RunStatus.ERROR
    assert result.stop_reason is StopReason.PROTOCOL_ERROR
    assert len(model.requests) == 1
    contents = tool_contents(loop.history)
    assert sum("PROTOCOL_ERROR" in content for content in contents) == 1
    assert sum("ABORTED_BEFORE_DISPATCH" in content for content in contents) == 1
    assert history_is_paired(loop.history)
    assert_valid_event_stream(sink.events)


def test_empty_call_id_terminates_with_protocol_error(tmp_path):
    bad = make_tool_call("glob", {"pattern": "*.py"}, "")
    model = ScriptedModel([turn(calls=[bad])])
    loop, _sink, _model = build_loop(tmp_path, model)
    result = loop.run("task")
    assert result.stop_reason is StopReason.PROTOCOL_ERROR
    assert "PROTOCOL_ERROR" in tool_contents(loop.history)[0]


def test_empty_assistant_text_without_tools_is_protocol_error(tmp_path):
    model = ScriptedModel([turn("   ")])
    loop, sink, _model = build_loop(tmp_path, model)
    result = loop.run("task")
    assert result.status is RunStatus.ERROR
    assert result.stop_reason is StopReason.PROTOCOL_ERROR
    assert_valid_event_stream(sink.events)


def test_unknown_exception_is_sanitized_before_run_result(tmp_path):
    class BrokenModel:
        def request(self, messages, tools):
            raise RuntimeError("C:/private/secret.txt?token=SECRET-SENTINEL")

    loop, _sink, _model = build_loop(tmp_path, BrokenModel())
    result = loop.run("task")
    assert result.status is RunStatus.ERROR
    assert result.stop_reason is StopReason.INTERNAL_ERROR
    rendered = json.dumps(result.to_dict(), ensure_ascii=False)
    assert "SECRET-SENTINEL" not in rendered
    assert "C:/private" not in rendered
    assert result.details["message"] == "internal operation failed"


# -------------------------------------------------- model retry and limits


def test_retryable_model_error_exhausts_three_attempts_without_history_change(tmp_path):
    model = AlwaysFailModel(retryable=True)
    loop, sink, _model = build_loop(tmp_path, model)
    result = loop.run("task")
    assert result.status is RunStatus.ERROR
    assert result.stop_reason is StopReason.MODEL_ERROR
    assert result.provider_attempt_count == 3
    assert result.step_count == 1
    assert sink.types().count("model_retry") == 2
    assert len(model.requests) == 3
    assert (
        model.requests[0]["messages"]
        == model.requests[1]["messages"]
        == model.requests[2]["messages"]
    )
    assert len(loop.history) == 2  # system + original task only
    assert_valid_event_stream(sink.events)


def test_non_retryable_model_error_fails_immediately(tmp_path):
    model = AlwaysFailModel(retryable=False)
    loop, sink, _model = build_loop(tmp_path, model)
    result = loop.run("task")
    assert result.stop_reason is StopReason.MODEL_ERROR
    assert result.provider_attempt_count == 1
    assert "model_retry" not in sink.types()


def test_retry_then_success_counts_attempts_separately_from_steps(tmp_path):
    model = FlakyModel(failures=2, success=turn("ok"))
    loop, sink, _model = build_loop(tmp_path, model)
    result = loop.run("task")
    assert result.status is RunStatus.SUCCESS
    assert result.step_count == 1
    assert result.provider_attempt_count == 3
    assert sink.types().count("model_retry") == 2
    assert len(model.requests) == 3
    assert (
        model.requests[0]["messages"]
        == model.requests[1]["messages"]
        == model.requests[2]["messages"]
    )


def test_context_overflow_terminates_before_model_call(tmp_path):
    model = ScriptedModel([turn("never")])
    loop, sink, _model = build_loop(tmp_path, model, budget=10)
    result = loop.run("a task that overflows the tiny budget")
    assert result.status is RunStatus.ERROR
    assert result.stop_reason is StopReason.CONTEXT_OVERFLOW
    assert len(model.requests) == 0
    assert_valid_event_stream(sink.events)


def test_max_steps_allows_one_safe_final_answer_after_tool_group(tmp_path):
    first = make_tool_call("glob", {"pattern": "*.a"})
    second = make_tool_call("glob", {"pattern": "*.b"})
    model = ScriptedModel([turn(calls=[first]), turn(calls=[second]), turn("too late")])
    loop, _sink, _model = build_loop(tmp_path, model, max_steps=2)
    result = loop.run("task")
    assert result.status is RunStatus.SUCCESS
    assert result.stop_reason is StopReason.FINAL_ANSWER
    assert result.step_count == 3
    assert result.work_step_count == 2
    assert result.finalization_step_count == 1
    assert result.tool_call_count == 2
    assert len(model.requests) == 3
    assert history_is_paired(loop.history)


def test_twentieth_work_step_gets_bounded_finalization_request(tmp_path):
    calls = [make_tool_call("glob", {"pattern": f"*.p{index}"}) for index in range(20)]
    turns = [turn(calls=[call]) for call in calls] + [turn("never")]
    model = ScriptedModel(turns)
    loop, _sink, _model = build_loop(tmp_path, model, max_steps=20)
    result = loop.run("task")
    assert result.stop_reason is StopReason.FINAL_ANSWER
    assert result.step_count == 21
    assert result.work_step_count == 20
    assert result.finalization_step_count == 1
    assert result.tool_call_count == 20
    assert len(model.requests) == 21
    assert history_is_paired(loop.history)


def test_finalization_never_dispatches_workspace_tools(tmp_path):
    inspect = make_tool_call("glob", {"pattern": "*.py"})
    forbidden_one = make_tool_call(
        "write_file", {"path": "unsafe-one.txt", "content": "must not exist"}
    )
    forbidden_two = make_tool_call(
        "write_file", {"path": "unsafe-two.txt", "content": "must not exist"}
    )
    model = ScriptedModel(
        [
            turn(calls=[inspect]),
            turn(calls=[forbidden_one]),
            turn(calls=[forbidden_two]),
        ]
    )
    loop, sink, _model = build_loop(tmp_path, model, max_steps=1)

    result = loop.run("task")

    assert result.status is RunStatus.ERROR
    assert result.stop_reason is StopReason.MAX_STEPS
    assert result.finalization_step_count == 2
    assert result.tool_call_count == 3
    assert not (tmp_path / "unsafe-one.txt").exists()
    assert not (tmp_path / "unsafe-two.txt").exists()
    denied = tool_contents(loop.history, "write_file")
    assert len(denied) == 2
    assert all("STEP_BUDGET_EXHAUSTED" in content for content in denied)
    assert sink.types().count("step_budget_finalizing") == 1
    assert history_is_paired(loop.history)


def test_three_failed_tool_rounds_trigger_limit(tmp_path):
    class DenyAll:
        def decide(self, effect, tool_name, normalized_args, call_id):
            return PolicyResult.deny("test deny")

    writes = [
        make_tool_call("write_file", {"path": f"f{index}.txt", "content": "x"})
        for index in range(3)
    ]
    model = ScriptedModel([turn(calls=[write]) for write in writes])
    loop, _sink, _model = build_loop(tmp_path, model, policy=DenyAll())
    result = loop.run("task")
    assert result.status is RunStatus.ERROR
    assert result.stop_reason is StopReason.TOOL_FAILURE_LIMIT
    assert len(model.requests) == 3
    assert all("POLICY_DENIED" in content for content in tool_contents(loop.history))


def test_repeat_reminds_at_third_and_aborts_at_fifth(tmp_path):
    (tmp_path / "a.py").write_text("x\n", encoding="utf-8")
    calls = [make_tool_call("read_file", {"path": "a.py"}) for _ in range(5)]
    model = ScriptedModel([turn(calls=[call]) for call in calls])
    loop, _sink, _model = build_loop(tmp_path, model)
    result = loop.run("task")
    assert result.status is RunStatus.ERROR
    assert result.stop_reason is StopReason.REPEATED_TOOL_CALL
    contents = tool_contents(loop.history, "read_file")
    assert sum("REPEATED_TOOL_CALL" in content for content in contents) == 1
    assert sum('"ok":true' in content for content in contents) == 4
    # The reminder is appended after the 3rd execution, before the 4th request.
    fourth_request = model.requests[3]["messages"]
    reminders = [
        m
        for m in fourth_request
        if m["role"] == "user" and "[loop-guard]" in m["content"]
    ]
    assert len(reminders) == 1
    assert len(model.requests) == 5
    assert history_is_paired(loop.history)


# -------------------------------------------------- cancellation and FSM


def test_cancel_between_tool_calls_aborts_half_group(tmp_path):
    flag = {"cancel": False}

    class TriggerPolicy:
        def __init__(self):
            self._inner = WorkspaceToolPolicy()
            self._decisions = 0

        def decide(self, effect, tool_name, normalized_args, call_id):
            self._decisions += 1
            if self._decisions == 2:
                flag["cancel"] = True
            return self._inner.decide(effect, tool_name, normalized_args, call_id)

    first = make_tool_call("glob", {"pattern": "*.py"})
    second = make_tool_call("glob", {"pattern": "*.txt"})
    model = ScriptedModel([turn(calls=[first, second])])
    loop, sink, _model = build_loop(
        tmp_path, model, policy=TriggerPolicy(), cancelled=lambda: flag["cancel"]
    )
    result = loop.run("task")
    assert result.status is RunStatus.INTERRUPTED
    assert result.stop_reason is StopReason.INTERRUPTED
    contents = tool_contents(loop.history)
    assert sum('"ok":true' in content for content in contents) == 1
    assert sum("ABORTED_BEFORE_DISPATCH" in content for content in contents) == 1
    assert result.tool_call_count == 2
    assert len(model.requests) == 1
    assert history_is_paired(loop.history)
    assert_valid_event_stream(sink.events)


def test_cancel_during_policy_prevents_write_side_effect(tmp_path):
    """R3: cancellation arriving inside prepare/policy must block the handler."""
    flag = {"cancel": False}

    class TriggerPolicy:
        def __init__(self):
            self._inner = WorkspaceToolPolicy()

        def decide(self, effect, tool_name, normalized_args, call_id):
            if tool_name == "write_file":
                flag["cancel"] = True
            return self._inner.decide(effect, tool_name, normalized_args, call_id)

    first = make_tool_call("write_file", {"path": "cancelled.txt", "content": "x"})
    second = make_tool_call("write_file", {"path": "cancelled2.txt", "content": "y"})
    model = ScriptedModel([turn(calls=[first, second])])
    loop, sink, _model = build_loop(
        tmp_path, model, policy=TriggerPolicy(), cancelled=lambda: flag["cancel"]
    )
    result = loop.run("task")
    assert result.status is RunStatus.INTERRUPTED
    assert result.stop_reason is StopReason.INTERRUPTED
    assert not (tmp_path / "cancelled.txt").exists()
    assert not (tmp_path / "cancelled2.txt").exists()
    contents = tool_contents(loop.history)
    assert len(contents) == 2
    assert all("ABORTED_BEFORE_DISPATCH" in content for content in contents)
    assert result.tool_call_count == 2
    assert len(model.requests) == 1
    assert history_is_paired(loop.history)
    assert_valid_event_stream(sink.events)
    # Neither call was ever dispatched, so no tool lifecycle events fired.
    assert "tool_started" not in sink.types()


def test_cancel_during_run_command_returns_interrupted(tmp_path):
    flag = {"cancel": False}
    long_run = make_tool_call(
        "run_command",
        {
            "argv": [sys.executable, "-c", "import time; time.sleep(30)"],
            "timeout_seconds": 30,
            "purpose": "other",
        },
    )
    model = ScriptedModel([turn(calls=[long_run]), turn("never")])
    loop, sink, _model = build_loop(tmp_path, model, cancelled=lambda: flag["cancel"])
    box = {}

    def target():
        box["result"] = loop.run("task")

    thread = threading.Thread(target=target)
    thread.start()
    time.sleep(0.6)
    flag["cancel"] = True
    thread.join(timeout=15)
    assert not thread.is_alive()
    result = box["result"]
    assert result.status is RunStatus.INTERRUPTED
    assert result.stop_reason is StopReason.INTERRUPTED
    assert "TOOL_ABORTED" in tool_contents(loop.history)[0]
    assert history_is_paired(loop.history)
    assert_valid_event_stream(sink.events)


def test_cancel_before_start_emits_only_start_and_finish(tmp_path):
    model = ScriptedModel([turn("never")])
    loop, sink, _model = build_loop(tmp_path, model, cancelled=lambda: True)
    result = loop.run("task")
    assert result.status is RunStatus.INTERRUPTED
    assert sink.types() == ["run_started", "run_finished"]
    assert_valid_event_stream(sink.events)


def test_illegal_phase_transition_raises(tmp_path):
    model = ScriptedModel([])
    loop, _sink, _model = build_loop(tmp_path, model)
    loop._transit(LoopPhase.TERMINAL)
    with pytest.raises(RuntimeError, match="illegal phase transition"):
        loop._transit(LoopPhase.READY)


def test_canonical_history_grows_only_by_appending(tmp_path):
    class SnapshotSink(RecordingSink):
        def __init__(self, loop):
            super().__init__()
            self._loop = loop
            self.snapshots: List[tuple] = []

        def emit(self, event):
            super().emit(event)
            self.snapshots.append(copy.deepcopy(self._loop.history))

    write = make_tool_call("write_file", {"path": "new.txt", "content": "x"})
    verify = make_tool_call(
        "run_command",
        {
            "argv": [
                sys.executable,
                "-c",
                "import pathlib; pathlib.Path('new.txt').exists()",
            ],
            "purpose": "verify",
            "timeout_seconds": 30,
        },
    )
    model = ScriptedModel([turn(calls=[write]), turn(calls=[verify]), turn("done")])
    sink = SnapshotSink(None)
    loop, _resolved_sink, _model = build_loop(tmp_path, model, sink=sink)
    sink._loop = loop
    loop.run("task")
    for previous, current in zip(sink.snapshots, sink.snapshots[1:]):
        assert previous == tuple(current)[: len(previous)]
    assert history_is_paired(loop.history)


def test_run_result_and_events_expose_structured_verification(tmp_path):
    write = make_tool_call("write_file", {"path": "new.txt", "content": "x"})
    verify_fail = make_tool_call(
        "run_command",
        {
            "argv": [sys.executable, "-c", "import sys; sys.exit(4)"],
            "purpose": "verify",
            "timeout_seconds": 30,
        },
    )
    model = ScriptedModel(
        [turn(calls=[write]), turn(calls=[verify_fail]), turn("答复"), turn("答复2")]
    )
    loop, sink, _model = build_loop(tmp_path, model)
    result = loop.run("task")
    assert result.last_verification["exit_code"] == 4
    finished = sink.events[-1]
    assert finished.payload["verification_status"] == "FAILED"
    assert finished.payload["mutated_paths"] == ["new.txt"]
    assert_valid_event_stream(sink.events)
