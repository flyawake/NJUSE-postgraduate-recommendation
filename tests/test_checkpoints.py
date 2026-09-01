"""Checkpoint restore integration and recovery tests."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from coding_agent.checkpoints import CheckpointError, WorkspaceCheckpointRestorer
from coding_agent.conversations.service import (
    ConversationService,
    ConversationServiceError,
)


class _UnusedModel:
    def request(self, messages, tools):  # pragma: no cover - defensive
        raise AssertionError("checkpoint tests must not invoke a model")


def _service(home: Path) -> ConversationService:
    return ConversationService(
        home=home,
        env={"OPENAI_API_KEY": "sk-test", "OPENAI_MODEL": "unused"},
        client_factory=lambda _connection: _UnusedModel(),
    )


def _terminal_turn(service: ConversationService, conversation_id: str, text: str):
    turn = service._repository.create_turn(
        conversation_id, user_text=text, run_id=uuid.uuid4().hex
    )
    return service._repository.update_turn_state(
        conversation_id, turn.id, state="success"
    )


def _blob(service: ConversationService, data: bytes) -> str:
    return service._artifact_store.put(data)


def _file_change(
    service: ConversationService,
    *,
    path: str,
    change_type: str,
    before: bytes | None,
    after: bytes | None,
) -> dict:
    item = {
        "relative_path": path,
        "change_type": change_type,
        "source": "tool_confirmed",
        "additions": 0,
        "deletions": 0,
    }
    if before is not None:
        before_blob = _blob(service, before)
        item.update(
            before_blob_id=before_blob,
            before_sha=before_blob,
            before_byte_count=len(before),
        )
    if after is not None:
        after_blob = _blob(service, after)
        item.update(
            after_blob_id=after_blob,
            after_sha=after_blob,
            after_byte_count=len(after),
        )
    return item


def _save_changes(
    service: ConversationService,
    conversation_id: str,
    turn_id: str,
    files: list[dict],
    *,
    coverage: str = "complete",
) -> None:
    service._repository.save_change_set(
        change_set_id=uuid.uuid4().hex,
        conversation_id=conversation_id,
        turn_id=turn_id,
        status="final",
        additions=0,
        deletions=0,
        file_count=len(files),
        coverage=coverage,
        files=files,
    )


def _multi_turn_fixture(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    home = tmp_path / "home"
    service = _service(home)
    conversation = service.create_conversation(
        workspace_path=str(workspace), profile_id=None, title="checkpoint"
    )
    conversation_id = conversation["id"]

    a1, a2, a3 = b"a-v1\n", b"a-v2\n", b"a-v3\n"
    b_new, c_old = b"created later\n", b"deleted later\n"
    target = _terminal_turn(service, conversation_id, "target")

    turn_two = _terminal_turn(service, conversation_id, "modify and create")
    _save_changes(
        service,
        conversation_id,
        turn_two.id,
        [
            _file_change(
                service,
                path="a.txt",
                change_type="modified",
                before=a1,
                after=a2,
            ),
            _file_change(
                service,
                path="created.txt",
                change_type="created",
                before=None,
                after=b_new,
            ),
        ],
    )

    turn_three = _terminal_turn(service, conversation_id, "modify and delete")
    _save_changes(
        service,
        conversation_id,
        turn_three.id,
        [
            _file_change(
                service,
                path="a.txt",
                change_type="modified",
                before=a2,
                after=a3,
            ),
            _file_change(
                service,
                path="deleted.txt",
                change_type="deleted",
                before=c_old,
                after=None,
            ),
        ],
    )
    (workspace / "a.txt").write_bytes(a3)
    (workspace / "created.txt").write_bytes(b_new)
    return service, home, workspace, conversation_id, target, turn_two, turn_three


def test_restore_rewinds_files_and_active_dialogue_timeline(tmp_path):
    service, _home, workspace, conversation_id, target, turn_two, turn_three = (
        _multi_turn_fixture(tmp_path)
    )
    try:
        preview = service.preview_checkpoint_restore(conversation_id, target.id)
        assert preview["restorable"] is True
        assert preview["future_turn_count"] == 2
        assert preview["file_count"] == 3
        assert (
            preview["create_count"],
            preview["modify_count"],
            preview["delete_count"],
        ) == (
            1,
            1,
            1,
        )

        key = uuid.uuid4().hex
        restored = service.restore_checkpoint(
            conversation_id, target.id, idempotency_key=key, confirm=True
        )
        replayed = service.restore_checkpoint(
            conversation_id, target.id, idempotency_key=key, confirm=True
        )

        assert restored == replayed
        assert restored["state"] == "completed"
        assert restored["superseded_turn_count"] == 2
        assert (workspace / "a.txt").read_bytes() == b"a-v1\n"
        assert not (workspace / "created.txt").exists()
        assert (workspace / "deleted.txt").read_bytes() == b"deleted later\n"
        assert [
            item["id"] for item in service.list_turns(conversation_id)["items"]
        ] == [target.id]
        assert (
            service._repository.get_turn(conversation_id, turn_two.id).timeline_state
            == "superseded"
        )
        assert (
            service._repository.get_turn(conversation_id, turn_three.id).timeline_state
            == "superseded"
        )
    finally:
        service.shutdown(timeout=5)


def test_preview_fails_closed_when_workspace_has_external_edits(tmp_path):
    service, _home, workspace, conversation_id, target, *_ = _multi_turn_fixture(
        tmp_path
    )
    try:
        (workspace / "a.txt").write_text("outside timeline\n", encoding="utf-8")
        preview = service.preview_checkpoint_restore(conversation_id, target.id)
        assert preview["restorable"] is False
        assert any(
            blocker["code"] == "checkpoint_file_conflict" and blocker["path"] == "a.txt"
            for blocker in preview["blockers"]
        )
        with pytest.raises(ConversationServiceError) as raised:
            service.restore_checkpoint(
                conversation_id,
                target.id,
                idempotency_key=uuid.uuid4().hex,
                confirm=True,
            )
        assert raised.value.code == "checkpoint_file_conflict"
        assert (workspace / "a.txt").read_text(encoding="utf-8") == "outside timeline\n"
    finally:
        service.shutdown(timeout=5)


def test_apply_failure_rolls_workspace_back_to_latest_state(tmp_path, monkeypatch):
    service, _home, workspace, conversation_id, target, *_ = _multi_turn_fixture(
        tmp_path
    )
    original_apply = WorkspaceCheckpointRestorer.apply

    def fail_after_first_step(self, plan, *, on_progress=None):
        partial = dict(plan)
        partial["steps"] = list(plan["steps"][:1])
        original_apply(self, partial, on_progress=on_progress)
        raise CheckpointError("injected_failure", "injected restore failure")

    monkeypatch.setattr(WorkspaceCheckpointRestorer, "apply", fail_after_first_step)
    try:
        with pytest.raises(ConversationServiceError) as raised:
            service.restore_checkpoint(
                conversation_id,
                target.id,
                idempotency_key=uuid.uuid4().hex,
                confirm=True,
            )
        assert raised.value.code == "injected_failure"
        assert (workspace / "a.txt").read_bytes() == b"a-v3\n"
        assert (workspace / "created.txt").read_bytes() == b"created later\n"
        assert not (workspace / "deleted.txt").exists()
        assert len(service.list_turns(conversation_id)["items"]) == 3
        operation = (
            service._repository._connect()
            .execute(
                "SELECT state FROM restore_operations ORDER BY created_at DESC LIMIT 1"
            )
            .fetchone()
        )
        assert operation["state"] == "rolled_back"
    finally:
        service.shutdown(timeout=5)


def test_startup_recovers_an_interrupted_applying_restore(tmp_path):
    service, home, workspace, conversation_id, target, *_ = _multi_turn_fixture(
        tmp_path
    )
    preview = service.preview_checkpoint_restore(conversation_id, target.id)
    plan = preview["_plan"]
    operation, _ = service._repository.create_restore_operation(
        operation_id=uuid.uuid4().hex,
        conversation_id=conversation_id,
        target_turn_id=target.id,
        workspace_key=service.get_conversation(conversation_id)["workspace_key"],
        plan=plan,
        idempotency_key=uuid.uuid4().hex,
    )
    service._repository.update_restore_operation(operation["id"], state="applying")
    WorkspaceCheckpointRestorer(workspace, service._artifact_store).apply(plan)
    assert (workspace / "a.txt").read_bytes() == b"a-v1\n"
    service.shutdown(timeout=5)

    restarted = _service(home)
    try:
        assert (workspace / "a.txt").read_bytes() == b"a-v3\n"
        assert (workspace / "created.txt").read_bytes() == b"created later\n"
        assert not (workspace / "deleted.txt").exists()
        recovered = restarted._repository.get_restore_operation(operation["id"])
        assert recovered["state"] == "rolled_back"
        assert recovered["error_code"] == "checkpoint_process_restarted"
        assert len(restarted.list_turns(conversation_id)["items"]) == 3
    finally:
        restarted.shutdown(timeout=5)


def test_plan_rejects_unsafe_and_file_directory_colliding_paths(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    artifacts_home = tmp_path / "home"
    service = _service(artifacts_home)
    blob = _blob(service, b"snapshot")
    restorer = WorkspaceCheckpointRestorer(workspace, service._artifact_store)
    try:
        plan = restorer.build_plan(
            [
                {
                    "turn_id": "future",
                    "coverage": "complete",
                    "files": [
                        {
                            "relative_path": "../outside.txt",
                            "change_type": "created",
                            "source": "command_detected",
                            "after_blob_id": blob,
                            "after_sha": blob,
                        },
                        {
                            "relative_path": "node",
                            "change_type": "created",
                            "source": "command_detected",
                            "after_blob_id": blob,
                            "after_sha": blob,
                        },
                        {
                            "relative_path": "node/file.txt",
                            "change_type": "created",
                            "source": "command_detected",
                            "after_blob_id": blob,
                            "after_sha": blob,
                        },
                    ],
                }
            ],
            target_turn_id="target",
            future_turn_ids=["future"],
        )
        codes = {blocker["code"] for blocker in plan["blockers"]}
        assert "checkpoint_path_unsafe" in codes
        assert "checkpoint_path_collision" in codes
    finally:
        service.shutdown(timeout=5)


def test_corrupt_applying_journal_starts_fail_closed_and_blocks_new_turns(tmp_path):
    service, home, _workspace, conversation_id, target, *_ = _multi_turn_fixture(
        tmp_path
    )
    preview = service.preview_checkpoint_restore(conversation_id, target.id)
    operation, _ = service._repository.create_restore_operation(
        operation_id=uuid.uuid4().hex,
        conversation_id=conversation_id,
        target_turn_id=target.id,
        workspace_key=service.get_conversation(conversation_id)["workspace_key"],
        plan=preview["_plan"],
        idempotency_key=uuid.uuid4().hex,
    )
    service._repository.update_restore_operation(operation["id"], state="applying")
    connection = service._repository._connect()
    connection.execute(
        "UPDATE restore_operations SET plan_json='{' WHERE id=?", (operation["id"],)
    )
    connection.commit()
    service.shutdown(timeout=5)

    restarted = _service(home)
    try:
        recovered = restarted._repository.get_restore_operation(operation["id"])
        assert recovered["state"] == "recovery_required"
        assert recovered["plan_corrupt"] is True
        with pytest.raises(ConversationServiceError) as raised:
            restarted.start_turn(conversation_id, user_text="must remain blocked")
        assert raised.value.code == "checkpoint_recovery_required"
    finally:
        restarted.shutdown(timeout=5)
