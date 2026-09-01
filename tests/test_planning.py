"""Selective plan boundary, lifecycle and failure-handling tests."""

from __future__ import annotations

import pytest

from coding_agent.models import RunStatus
from coding_agent.planning import PlanLedger, PlanValidationError
from coding_agent.tools.base import PLAN_PERSIST_FAILED
from conftest import ScriptedModel, build_loop, make_tool_call, turn


def plan_call(statuses=("in_progress", "pending")):
    return make_tool_call(
        "update_plan",
        {
            "explanation": "This task spans dependent outcomes.",
            "plan": [
                {"step": "Inspect the architecture", "status": statuses[0]},
                {"step": "Implement and verify", "status": statuses[1]},
            ],
        },
    )


def test_simple_task_finishes_without_creating_plan(tmp_path):
    model = ScriptedModel([turn("A concise answer")])
    loop, sink, _ = build_loop(tmp_path, model)

    result = loop.run("Explain this constant")

    assert result.status is RunStatus.SUCCESS
    assert result.plan_state is None
    assert result.plan_revision is None
    assert "plan_updated" not in sink.types()
    assert [tool["function"]["name"] for tool in model.requests[0]["tools"]][-1] == (
        "update_plan"
    )


def test_complex_task_plan_is_revisioned_and_completed(tmp_path):
    model = ScriptedModel(
        [
            turn(calls=[plan_call()]),
            turn(calls=[plan_call(("completed", "completed"))]),
            turn("Done"),
        ]
    )
    loop, sink, _ = build_loop(tmp_path, model)

    result = loop.run("Refactor multiple layers and verify them")

    updates = [event for event in sink.events if event.type == "plan_updated"]
    assert [event.payload["revision"] for event in updates] == [1, 2]
    assert result.plan_state == "completed"
    assert result.plan_revision == 2
    finished = sink.events[-1]
    assert finished.payload["plan_state"] == "completed"


def test_unfinished_plan_defers_final_answer_once(tmp_path):
    model = ScriptedModel(
        [
            turn(calls=[plan_call()]),
            turn("Premature final"),
            turn(calls=[plan_call(("completed", "completed"))]),
            turn("Actual final"),
        ]
    )
    loop, sink, _ = build_loop(tmp_path, model)

    result = loop.run("Do complex work")

    assert result.final_text == "Actual final"
    assert sink.types().count("plan_completion_deferred") == 1
    policy_messages = [
        message
        for message in model.requests[2]["messages"]
        if message["role"] == "user" and "[plan-policy]" in message["content"]
    ]
    assert len(policy_messages) == 1


def test_plan_only_rounds_do_not_consume_work_budget_and_can_close_safely(tmp_path):
    first = make_tool_call("glob", {"pattern": "*.first"})
    second = make_tool_call("glob", {"pattern": "*.second"})
    model = ScriptedModel(
        [
            turn(calls=[plan_call()]),
            turn(calls=[first]),
            turn(calls=[second]),
            turn(calls=[plan_call(("completed", "completed"))]),
            turn("Done after closing the plan"),
        ]
    )
    loop, sink, _ = build_loop(tmp_path, model, max_steps=2)

    result = loop.run("A complex task at the old step boundary")

    assert result.status is RunStatus.SUCCESS
    assert result.work_step_count == 2
    assert result.plan_step_count == 1
    assert result.finalization_step_count == 2
    assert result.step_count == 5
    assert result.plan_state == "completed"
    assert sink.types().count("step_budget_finalizing") == 1


def test_active_plan_progress_extends_budget_up_to_hard_limit(tmp_path):
    calls = [
        make_tool_call("glob", {"pattern": f"*.work-{index}"}) for index in range(4)
    ]
    model = ScriptedModel(
        [
            turn(calls=[plan_call()]),
            turn(calls=[calls[0]]),
            turn(calls=[plan_call(("completed", "in_progress"))]),
            turn(calls=[calls[1]]),
            turn(calls=[calls[2]]),
            turn(calls=[plan_call(("completed", "completed"))]),
            turn(calls=[calls[3]]),
            turn("Done at the hard boundary"),
        ]
    )
    loop, sink, _ = build_loop(
        tmp_path,
        model,
        max_steps=2,
        hard_max_steps=4,
    )

    result = loop.run("A complex task requiring adaptive budget")

    extensions = [event for event in sink.events if event.type == "step_budget_extended"]
    assert result.status is RunStatus.SUCCESS
    assert result.work_step_count == 4
    assert result.work_step_limit == 4
    assert result.hard_step_limit == 4
    assert result.budget_extensions == 1
    assert result.finalization_step_count == 1
    assert len(extensions) == 1
    assert extensions[0].payload == {
        "from": 2,
        "to": 4,
        "hard_limit": 4,
        "completed_plan_steps": 1,
    }


def test_initial_plan_cannot_claim_old_completed_steps_to_earn_extension(tmp_path):
    inspect = make_tool_call("glob", {"pattern": "*.work"})
    model = ScriptedModel(
        [
            turn(calls=[plan_call(("completed", "in_progress"))]),
            turn(calls=[inspect]),
            turn(calls=[plan_call(("completed", "completed"))]),
            turn("Done"),
        ]
    )
    loop, sink, _ = build_loop(
        tmp_path,
        model,
        max_steps=1,
        hard_max_steps=3,
    )

    result = loop.run("Do not inflate progress")

    assert result.status is RunStatus.SUCCESS
    assert result.work_step_limit == 1
    assert result.finalization_step_count == 2
    assert "step_budget_extended" not in sink.types()


def test_plan_only_request_limit_stops_plan_spam_with_paired_results(tmp_path):
    model = ScriptedModel([turn(calls=[plan_call()]) for _ in range(3)])
    loop, _sink, _ = build_loop(tmp_path, model, max_plan_steps=2)

    result = loop.run("Keep replanning forever")

    assert result.status is RunStatus.ERROR
    assert result.stop_reason.value == "MAX_STEPS"
    assert result.plan_step_count == 3
    assert "STEP_BUDGET_EXHAUSTED" in [
        message.content
        for message in loop.history
        if getattr(message, "tool_name", None) == "update_plan"
    ][-1]


def test_finalization_cannot_create_a_new_plan(tmp_path):
    inspect = make_tool_call("glob", {"pattern": "*.work"})
    model = ScriptedModel(
        [turn(calls=[inspect]), turn(calls=[plan_call()]), turn("Done")]
    )
    loop, sink, _ = build_loop(tmp_path, model, max_steps=1)

    result = loop.run("A task without a plan")

    assert result.status is RunStatus.SUCCESS
    assert result.plan_state is None
    assert "plan_updated" not in sink.types()
    plan_results = [
        message.content
        for message in loop.history
        if getattr(message, "tool_name", None) == "update_plan"
    ]
    assert len(plan_results) == 1
    assert "STEP_BUDGET_EXHAUSTED" in plan_results[0]


def test_finalization_accepts_truthful_answer_with_incomplete_plan(tmp_path):
    inspect = make_tool_call("glob", {"pattern": "*.work"})
    model = ScriptedModel(
        [turn(calls=[plan_call()]), turn(calls=[inspect]), turn("Stopped safely")]
    )
    loop, sink, _ = build_loop(tmp_path, model, max_steps=1)

    result = loop.run("Stop safely if the plan cannot progress")

    assert result.status is RunStatus.SUCCESS
    assert result.final_text == "Stopped safely"
    assert result.plan_state == "incomplete"
    assert result.finalization_step_count == 1
    assert "plan_completion_deferred" not in sink.types()


def test_plan_contract_rejects_ceremonial_or_ambiguous_plans():
    ledger = PlanLedger()
    with pytest.raises(PlanValidationError, match="2 to 7"):
        ledger.update("", [{"step": "Only step", "status": "in_progress"}])
    with pytest.raises(PlanValidationError, match="exactly one"):
        ledger.update(
            "",
            [
                {"step": "First", "status": "pending"},
                {"step": "Second", "status": "pending"},
            ],
        )
    with pytest.raises(PlanValidationError, match="must start"):
        ledger.update(
            "",
            [
                {"step": "First", "status": "completed"},
                {"step": "Second", "status": "completed"},
            ],
        )


def test_completed_plan_steps_cannot_regress_or_disappear():
    ledger = PlanLedger()
    ledger.update(
        "start",
        [
            {"step": "Inspect", "status": "in_progress"},
            {"step": "Verify", "status": "pending"},
        ],
    )
    ledger.update(
        "progress",
        [
            {"step": "Inspect", "status": "completed"},
            {"step": "Verify", "status": "in_progress"},
        ],
    )
    with pytest.raises(PlanValidationError, match="cannot be removed"):
        ledger.update(
            "bad revision",
            [
                {"step": "Inspect", "status": "pending"},
                {"step": "Verify", "status": "in_progress"},
            ],
        )


def test_plan_persistence_failure_is_structured_and_does_not_publish(tmp_path):
    def fail_persist(_snapshot, _expected):
        raise OSError("disk unavailable")

    ledger = PlanLedger(persist=fail_persist)
    from coding_agent.tools import build_default_tools
    from coding_agent.tools.executor import ToolExecutor
    from coding_agent.tools.observation import FileObservationTracker
    from coding_agent.tools.paths import Workspace
    from coding_agent.tools.policy import WorkspaceToolPolicy

    registry = build_default_tools(
        Workspace(tmp_path), FileObservationTracker(), plan_ledger=ledger
    )
    outcome = ToolExecutor(registry, WorkspaceToolPolicy()).run(plan_call())

    assert outcome.ok is False
    assert outcome.error is not None
    assert outcome.error.code == PLAN_PERSIST_FAILED
    assert outcome.error.retryable is True
    assert ledger.snapshot is None
