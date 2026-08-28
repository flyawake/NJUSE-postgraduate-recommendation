"""Web API tests for task_004 conversation endpoints.

All offline: the service uses a fake model and tmp_path agent home/workspace.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from coding_agent.conversations.service import ConversationService
from coding_agent.models import AssistantTurn
from coding_agent.web.app import create_app
from coding_agent.web.controller import RunController


class FinalModel:
    def __init__(self) -> None:
        self.requests: list[list[dict]] = []

    def request(self, messages: list[dict], tools: list[dict]) -> AssistantTurn:
        self.requests.append(messages)
        return AssistantTurn(text="done", tool_calls=())


@pytest.fixture
def api(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "a.txt").write_text("base", encoding="utf-8")
    model = FinalModel()
    service = ConversationService(
        home=tmp_path / "home",
        env={"OPENAI_API_KEY": "sk-test", "OPENAI_MODEL": "fake-model"},
        client_factory=lambda _connection: model,
    )
    controller = RunController(home=tmp_path / "controller-home", env={})
    app = create_app(
        controller=controller,
        static_dir=tmp_path,
        session_token="test-token",
        conversation_service=service,
    )
    client = TestClient(app, base_url="http://127.0.0.1")
    headers = {
        "X-Coding-Agent-Token": client.get("/api/bootstrap").json()["session_token"]
    }
    return client, headers, service, model, workspace


def wait_turn_via_api(client, headers, cid, tid):
    for _ in range(100):
        response = client.get(f"/api/conversations/{cid}/turns/{tid}", headers=headers)
        assert response.status_code == 200
        data = response.json()
        if data["state"] in ("success", "error", "interrupted", "rejected"):
            return data
        time.sleep(0.05)
    raise AssertionError("turn did not finish")


class TestConversationApi:
    def test_crud_and_lifecycle(self, api):
        client, headers, _service, _model, ws = api
        created = client.post(
            "/api/conversations",
            json={"workspace": str(ws), "profile_id": None},
            headers=headers,
        )
        assert created.status_code == 201
        conv = created.json()
        cid = conv["id"]
        assert conv["state"] == "active"

        listed = client.get("/api/conversations", headers=headers)
        assert listed.status_code == 200
        assert [item["id"] for item in listed.json()["items"]] == [cid]

        renamed = client.patch(
            f"/api/conversations/{cid}",
            json={"title": "改名", "expected_version": 1},
            headers=headers,
        )
        assert renamed.status_code == 200
        assert renamed.json()["title"] == "改名"

        archived = client.post(
            f"/api/conversations/{cid}/archive",
            json={"expected_version": 2},
            headers=headers,
        )
        assert archived.status_code == 200
        assert archived.json()["state"] == "archived"

        unarchived = client.post(
            f"/api/conversations/{cid}/unarchive",
            json={"expected_version": 3},
            headers=headers,
        )
        assert unarchived.status_code == 200
        assert unarchived.json()["state"] == "active"

        deleted = client.request(
            "DELETE",
            f"/api/conversations/{cid}",
            json={"expected_version": 4, "confirm": True},
            headers=headers,
        )
        assert deleted.status_code == 204
        missing = client.get(f"/api/conversations/{cid}", headers=headers)
        assert missing.status_code == 404

    def test_start_turn_and_poll(self, api):
        client, headers, _service, model, ws = api
        conv = client.post(
            "/api/conversations",
            json={"workspace": str(ws), "profile_id": None},
            headers=headers,
        ).json()
        started = client.post(
            f"/api/conversations/{conv['id']}/turns",
            json={"content": "do it"},
            headers=headers,
        )
        assert started.status_code == 202
        turn = started.json()
        terminal = wait_turn_via_api(client, headers, conv["id"], turn["id"])
        assert terminal["state"] == "success"
        assert len(model.requests) >= 1

        changes = client.get(
            f"/api/conversations/{conv['id']}/turns/{turn['id']}/changes",
            headers=headers,
        )
        assert changes.status_code == 200
        assert changes.json()["file_count"] == 0

    def test_stream_snapshot_endpoint(self, api):
        client, headers, _service, _model, ws = api
        conv = client.post(
            "/api/conversations",
            json={"workspace": str(ws), "profile_id": None},
            headers=headers,
        ).json()
        turn = client.post(
            f"/api/conversations/{conv['id']}/turns",
            json={"content": "snapshot"},
            headers=headers,
        ).json()
        wait_turn_via_api(client, headers, conv["id"], turn["id"])
        response = client.get(
            f"/api/conversations/{conv['id']}/turns/{turn['id']}/stream",
            headers=headers,
        )
        assert response.status_code == 200
        assert response.json() == {"checkpoints": []}

    def test_idempotent_start_turn(self, api):
        client, headers, _service, _model, ws = api
        conv = client.post(
            "/api/conversations",
            json={"workspace": str(ws), "profile_id": None},
            headers=headers,
        ).json()
        first = client.post(
            f"/api/conversations/{conv['id']}/turns",
            json={"content": "do it", "idempotency_key": "key-1"},
            headers=headers,
        )
        wait_turn_via_api(client, headers, conv["id"], first.json()["id"])
        second = client.post(
            f"/api/conversations/{conv['id']}/turns",
            json={"content": "do it", "idempotency_key": "key-1"},
            headers=headers,
        )
        assert second.status_code == 202
        assert second.json()["id"] == first.json()["id"]

    def test_version_conflict_returns_409(self, api):
        client, headers, _service, _model, ws = api
        conv = client.post(
            "/api/conversations",
            json={"workspace": str(ws), "profile_id": None},
            headers=headers,
        ).json()
        response = client.patch(
            f"/api/conversations/{conv['id']}",
            json={"title": "bad", "expected_version": 99},
            headers=headers,
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "version_conflict"

    def test_legacy_runs_endpoint_delegates_to_persisted_conversation_service(
        self, api
    ):
        client, headers, service, _model, ws = api
        started = client.post(
            "/api/runs",
            json={"workspace": str(ws), "task": "legacy compatibility"},
            headers=headers,
        )
        assert started.status_code == 200
        run_id = started.json()["run_id"]
        for _ in range(100):
            snapshot = client.get(f"/api/runs/{run_id}", headers=headers)
            assert snapshot.status_code == 200
            if snapshot.json()["state"] == "terminal":
                break
            time.sleep(0.05)
        else:
            raise AssertionError("legacy compatibility turn did not finish")
        page = service.list_conversations()
        assert len(page["items"]) == 1
        turns = service.list_turns(page["items"][0]["id"])
        assert turns["items"][0]["run_id"] == run_id
