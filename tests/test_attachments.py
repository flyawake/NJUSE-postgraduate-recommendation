from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from coding_agent.attachments import AttachmentValidationError, validate_attachment
from coding_agent.conversations.service import (
    ConversationService,
    ConversationServiceError,
)
from coding_agent.model_client import _chat_user_content, _responses_input
from coding_agent.models import AssistantTurn
from coding_agent.web.app import create_app
from coding_agent.web.controller import RunController


class CapturingModel:
    def __init__(self) -> None:
        self.requests: list[list[dict]] = []

    def request(self, messages, tools):
        self.requests.append(messages)
        return AssistantTurn("done")


def _wait(service: ConversationService, conversation_id: str, turn_id: str):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        turn = service.get_turn(conversation_id, turn_id)
        if not turn["active"]:
            return turn
        time.sleep(0.03)
    raise AssertionError("turn did not finish")


def test_image_attachment_is_claimed_projected_and_not_embedded_in_sqlite(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    model = CapturingModel()
    service = ConversationService(
        home=tmp_path / "home",
        env={"OPENAI_API_KEY": "sk-test", "OPENAI_MODEL": "test"},
        client_factory=lambda _connection: model,
    )
    conversation = service.create_conversation(
        workspace_path=str(workspace), profile_id=None
    )
    png = b"\x89PNG\r\n\x1a\n" + b"image-body"
    attachment = service.create_attachment(
        conversation["id"], filename="shot.png", media_type="image/png", data=png
    )
    notes = service.create_attachment(
        conversation["id"],
        filename="notes.txt",
        media_type="text/plain",
        data=b"visible notes",
    )
    started = service.start_turn(
        conversation["id"],
        user_text="inspect this",
        attachment_ids=[attachment["id"], notes["id"]],
    )
    turn = _wait(service, conversation["id"], started["id"])
    assert turn["attachments"] == [attachment, notes]
    user = [item for item in model.requests[0] if item["role"] == "user"][-1]
    assert user["content"][0] == {
        "type": "input_text",
        "text": "inspect this\n附件：shot.png、notes.txt",
    }
    assert user["content"][1]["type"] == "input_image"
    assert user["content"][1]["image_url"].startswith("data:image/png;base64,")
    assert user["content"][2] == {
        "type": "input_text",
        "text": "\n--- 附件 notes.txt ---\nvisible notes",
    }
    payload = (
        service._repository._connect()
        .execute(
            "SELECT payload_json FROM canonical_items WHERE turn_id=? AND role='user'",
            (turn["id"],),
        )
        .fetchone()["payload_json"]
    )
    assert "base64" not in payload
    assert json.loads(payload)["attachments"][0]["filename"] == "shot.png"
    service.shutdown()


def test_pending_attachment_can_be_deleted_but_claimed_one_cannot(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    service = ConversationService(
        home=tmp_path / "home",
        env={"OPENAI_API_KEY": "sk-test", "OPENAI_MODEL": "test"},
        client_factory=lambda _connection: CapturingModel(),
    )
    conversation = service.create_conversation(
        workspace_path=str(workspace), profile_id=None
    )
    first = service.create_attachment(
        conversation["id"], filename="a.txt", media_type="text/plain", data=b"hello"
    )
    service.delete_attachment(conversation["id"], first["id"])
    with pytest.raises(ConversationServiceError, match="附件不存在"):
        service.read_attachment(conversation["id"], first["id"])
    second = service.create_attachment(
        conversation["id"], filename="b.txt", media_type="text/plain", data=b"world"
    )
    started = service.start_turn(
        conversation["id"], user_text="read", attachment_ids=[second["id"]]
    )
    with pytest.raises(ConversationServiceError) as excinfo:
        service.delete_attachment(conversation["id"], second["id"])
    assert excinfo.value.code == "attachment_not_found"
    _wait(service, conversation["id"], started["id"])
    service.shutdown()


def test_attachment_limits_and_type_sniffing(tmp_path):
    with pytest.raises(AttachmentValidationError) as excinfo:
        validate_attachment("fake.png", "image/png", b"not an image")
    assert excinfo.value.code == "attachment_type_mismatch"
    with pytest.raises(AttachmentValidationError) as excinfo:
        validate_attachment("program.exe", None, b"MZ")
    assert excinfo.value.code == "unsupported_attachment_type"


def test_neutral_parts_map_to_both_openai_wire_apis():
    neutral = [
        {"type": "input_text", "text": "inspect"},
        {
            "type": "input_image",
            "image_url": "data:image/png;base64,eA==",
            "detail": "auto",
        },
        {
            "type": "input_file",
            "filename": "notes.txt",
            "file_data": "data:text/plain;base64,eA==",
        },
    ]
    chat = _chat_user_content(neutral)
    assert chat[0] == {"type": "text", "text": "inspect"}
    assert chat[1]["image_url"] == {
        "url": "data:image/png;base64,eA==",
        "detail": "auto",
    }
    assert chat[2]["file"]["filename"] == "notes.txt"
    responses = _responses_input([{"role": "user", "content": neutral}])
    assert responses == [{"role": "user", "content": neutral}]


def test_attachment_http_upload_preview_delete_and_turn_contract(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    model = CapturingModel()
    service = ConversationService(
        home=tmp_path / "service-home",
        env={"OPENAI_API_KEY": "sk-test", "OPENAI_MODEL": "test"},
        client_factory=lambda _connection: model,
    )
    controller = RunController(home=tmp_path / "controller-home", env={})
    app = create_app(
        controller=controller,
        conversation_service=service,
        static_dir=tmp_path,
        session_token="test-token",
    )
    client = TestClient(app, base_url="http://127.0.0.1")
    conversation = service.create_conversation(
        workspace_path=str(workspace), profile_id=None
    )
    png = b"\x89PNG\r\n\x1a\n" + b"payload"
    upload = client.post(
        f"/api/conversations/{conversation['id']}/attachments?filename=screen.png",
        content=png,
        headers={
            "X-Coding-Agent-Token": "test-token",
            "Content-Type": "image/png",
        },
    )
    assert upload.status_code == 201
    attachment = upload.json()
    preview = client.get(
        f"/api/conversations/{conversation['id']}/attachments/{attachment['id']}"
    )
    assert preview.status_code == 200
    assert preview.content == png
    assert preview.headers["content-type"] == "image/png"
    start = client.post(
        f"/api/conversations/{conversation['id']}/turns",
        json={"content": "", "attachment_ids": [attachment["id"]]},
        headers={"X-Coding-Agent-Token": "test-token"},
    )
    assert start.status_code == 202
    assert start.json()["attachments"] == [attachment]
    claimed_delete = client.delete(
        f"/api/conversations/{conversation['id']}/attachments/{attachment['id']}",
        headers={"X-Coding-Agent-Token": "test-token"},
    )
    assert claimed_delete.status_code == 404
    _wait(service, conversation["id"], start.json()["id"])
    service.shutdown()
    controller.shutdown()


def test_attachment_claim_is_idempotent_and_cross_conversation_safe(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    service = ConversationService(
        home=tmp_path / "home",
        env={"OPENAI_API_KEY": "sk-test", "OPENAI_MODEL": "test"},
        client_factory=lambda _connection: CapturingModel(),
    )
    first = service.create_conversation(workspace_path=str(workspace), profile_id=None)
    second = service.create_conversation(workspace_path=str(workspace), profile_id=None)
    attachment = service.create_attachment(
        first["id"], filename="a.txt", media_type="text/plain", data=b"hello"
    )
    with pytest.raises(ConversationServiceError) as excinfo:
        service.start_turn(
            second["id"], user_text="wrong owner", attachment_ids=[attachment["id"]]
        )
    assert excinfo.value.code == "attachment_unavailable"
    started = service.start_turn(
        first["id"],
        user_text="once",
        attachment_ids=[attachment["id"]],
        idempotency_key="attachment-idempotency",
    )
    repeated = service.start_turn(
        first["id"],
        user_text="once",
        attachment_ids=[attachment["id"]],
        idempotency_key="attachment-idempotency",
    )
    assert repeated["id"] == started["id"]
    count = (
        service._repository._connect()
        .execute(
            "SELECT COUNT(*) AS value FROM attachments WHERE turn_id=?",
            (started["id"],),
        )
        .fetchone()["value"]
    )
    assert count == 1
    _wait(service, first["id"], started["id"])
    service.shutdown()


def test_hard_delete_removes_attachment_metadata_and_blob(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    service = ConversationService(home=tmp_path / "home", env={})
    first = service.create_conversation(workspace_path=str(workspace), profile_id=None)
    second = service.create_conversation(workspace_path=str(workspace), profile_id=None)
    attachment = service.create_attachment(
        first["id"], filename="a.txt", media_type="text/plain", data=b"private body"
    )
    ref = service._repository.get_attachment(first["id"], attachment["id"])
    assert ref is not None and service._attachment_store.exists(ref.sha256)
    with pytest.raises(ConversationServiceError) as excinfo:
        service.read_attachment(second["id"], attachment["id"])
    assert excinfo.value.code == "attachment_not_found"
    service.delete_conversation(first["id"], expected_version=first["version"])
    assert service._repository.get_attachment(first["id"], attachment["id"]) is None
    assert not service._attachment_store.exists(ref.sha256)
    service.shutdown()
