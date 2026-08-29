"""task_007 web API tests for Memory Center endpoints."""

from __future__ import annotations

import os
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
    return client, headers, service, workspace, model


class TestMemoryApi:
    def test_memory_crud_and_search(self, api):
        client, headers, service, ws, _model = api
        created = client.post(
            "/api/memories",
            json={
                "scope_type": "workspace",
                "scope_key": str(ws),
                "kind": "fact",
                "content": "项目使用 FastAPI 和 React",
                "title": "stack",
            },
            headers=headers,
        )
        assert created.status_code == 201
        memory = created.json()
        mid = memory["id"]

        page = client.get(
            "/api/memories",
            params={
                "scope_type": "workspace",
                "scope_key": str(ws),
                "query": "FastAPI",
            },
            headers=headers,
        )
        assert page.status_code == 200
        assert [item["id"] for item in page.json()["items"]] == [mid]

        detail = client.get(f"/api/memories/{mid}", headers=headers)
        assert detail.status_code == 200
        assert detail.json()["content"].startswith("项目")

    def test_memory_cursor_is_bounded_filter_bound_and_snapshot_stable(self, api):
        client, headers, _service, _ws, _model = api
        created_ids = []
        for index in range(3):
            response = client.post(
                "/api/memories",
                json={
                    "scope_type": "global",
                    "scope_key": "global",
                    "kind": "fact",
                    "content": f"paged memory {index}",
                    "idempotency_key": f"paged-create-{index}",
                },
                headers=headers,
            )
            created_ids.append(response.json()["id"])

        first = client.get(
            "/api/memories",
            params={"scope_type": "global", "status": "confirmed", "limit": 2},
            headers=headers,
        ).json()
        assert len(first["items"]) == 2
        assert first["next_cursor"]

        client.post(
            "/api/memories",
            json={
                "scope_type": "global",
                "scope_key": "global",
                "kind": "fact",
                "content": "newer than snapshot",
            },
            headers=headers,
        )
        second = client.get(
            "/api/memories",
            params={
                "scope_type": "global",
                "status": "confirmed",
                "limit": 2,
                "cursor": first["next_cursor"],
            },
            headers=headers,
        ).json()
        combined = [item["id"] for item in first["items"] + second["items"]]
        assert set(combined) == set(created_ids)
        assert len(combined) == len(set(combined))

        wrong_filter = client.get(
            "/api/memories",
            params={"status": "candidate", "cursor": first["next_cursor"]},
            headers=headers,
        )
        assert wrong_filter.status_code == 400

    def test_candidate_approve_and_usage(self, api):
        client, headers, service, ws, _model = api
        cand = service._memory.create_candidate(
            scope_type="workspace",
            scope_key=str(ws.resolve()),
            kind="preference",
            content="prefer pytest",
            source_conversation_id=None,
            source_turn_id=None,
        )
        assert cand["status"] == "candidate"
        approved = client.post(
            f"/api/memories/{cand['id']}/approve",
            json={"expected_version": cand["version"]},
            headers=headers,
        )
        assert approved.status_code == 200
        assert approved.json()["status"] == "confirmed"

    def test_search_honors_candidate_status_filter(self, api):
        client, headers, service, ws, _model = api
        candidate = service._memory.create_candidate(
            scope_type="workspace",
            scope_key=os.path.normcase(str(ws.resolve())),
            kind="fact",
            content="candidate filter sentinel",
        )
        response = client.get(
            "/api/memories",
            params={
                "scope_type": "workspace",
                "scope_key": str(ws),
                "status": "candidate",
                "query": "filter sentinel",
            },
            headers=headers,
        )
        assert response.status_code == 200
        assert [item["id"] for item in response.json()["items"]] == [candidate["id"]]

    def test_workspace_scope_is_canonical_and_source_must_exist(self, api):
        client, headers, _service, ws, _model = api
        created = client.post(
            "/api/memories",
            json={
                "scope_type": "workspace",
                "scope_key": str(ws / "."),
                "kind": "fact",
                "content": "canonical workspace fact",
            },
            headers=headers,
        )
        assert created.status_code == 201
        assert created.json()["scope_key"] == os.path.normcase(str(ws.resolve()))

        invalid_source = client.post(
            "/api/memories",
            json={
                "scope_type": "workspace",
                "scope_key": str(ws),
                "kind": "fact",
                "content": "invalid source fact",
                "source_conversation_id": "missing-conversation",
            },
            headers=headers,
        )
        assert invalid_source.status_code == 404

    def test_stale_memory_version_returns_conflict_and_missing_returns_404(self, api):
        client, headers, _service, ws, _model = api
        created = client.post(
            "/api/memories",
            json={
                "scope_type": "workspace",
                "scope_key": str(ws),
                "kind": "fact",
                "content": "versioned memory",
            },
            headers=headers,
        ).json()
        edited = client.patch(
            f"/api/memories/{created['id']}",
            json={"content": "new version", "expected_version": created["version"]},
            headers=headers,
        )
        assert edited.status_code == 200
        stale = client.request(
            "DELETE",
            f"/api/memories/{created['id']}",
            json={"expected_version": created["version"]},
            headers=headers,
        )
        assert stale.status_code == 409
        missing = client.get("/api/memories/not-found", headers=headers)
        assert missing.status_code == 404

    def test_secret_rejected_by_api(self, api, caplog):
        client, headers, service, ws, _model = api
        sentinel = "supersecretvalue123"
        response = client.post(
            "/api/memories",
            json={
                "scope_type": "workspace",
                "scope_key": str(ws),
                "kind": "fact",
                "content": f"API_KEY={sentinel}",
            },
            headers=headers,
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "memory_contains_secret"
        assert sentinel not in response.text
        dump = "\n".join(service._repository._connect().iterdump())
        assert sentinel not in dump
        assert sentinel not in caplog.text

        title_sentinel = "sk-abcdefghijklmnopqrstuvwxyz123456"
        title_response = client.post(
            "/api/memories",
            json={
                "scope_type": "workspace",
                "scope_key": str(ws),
                "kind": "fact",
                "title": title_sentinel,
                "content": "safe body",
            },
            headers=headers,
        )
        assert title_response.status_code == 400
        assert title_sentinel not in title_response.text
        assert title_sentinel not in "\n".join(
            service._repository._connect().iterdump()
        )

    def test_turn_request_includes_memory_projection(self, api):
        client, headers, service, ws, model = api
        canonical = str(ws.resolve())
        service.create_memory(
            scope_type="workspace",
            scope_key=canonical,
            kind="fact",
            content="project uses FastAPI and React",
            title="stack",
        )
        conv = client.post(
            "/api/conversations",
            json={"workspace": str(ws), "profile_id": None},
            headers=headers,
        ).json()
        turn = client.post(
            f"/api/conversations/{conv['id']}/turns",
            json={"content": "what is the project stack FastAPI React"},
            headers=headers,
        ).json()
        for _ in range(100):
            if model.requests:
                break
            time.sleep(0.05)
        assert model.requests
        assert any(
            isinstance(message, dict)
            and message.get("role") == "system"
            and "<memory_context" in str(message.get("content", ""))
            for message in model.requests[0]
        )
        usage = client.get(
            f"/api/conversations/{conv['id']}/turns/{turn['id']}/memory-usage",
            headers=headers,
        )
        assert usage.status_code == 200
        assert len(usage.json()) == 1

    def test_candidate_extraction_failure_does_not_change_main_turn(self, api):
        client, headers, service, ws, model = api
        service._memory.set_candidate_enabled(True)
        conv = client.post(
            "/api/conversations",
            json={"workspace": str(ws), "profile_id": None},
            headers=headers,
        ).json()
        turn = client.post(
            f"/api/conversations/{conv['id']}/turns",
            json={"content": "candidate extractor failure"},
            headers=headers,
        ).json()
        for _ in range(100):
            if len(model.requests) >= 2:
                break
            time.sleep(0.05)
        assert len(model.requests) >= 2
        terminal = client.get(
            f"/api/conversations/{conv['id']}/turns/{turn['id']}",
            headers=headers,
        ).json()
        assert terminal["state"] == "success"
        assert service.list_memories(status="candidate")["items"] == []

    def test_secret_user_text_never_reaches_candidate_extractor(self, api):
        client, headers, service, ws, model = api
        service._memory.set_candidate_enabled(True)
        conv = client.post(
            "/api/conversations",
            json={"workspace": str(ws), "profile_id": None},
            headers=headers,
        ).json()
        turn = client.post(
            f"/api/conversations/{conv['id']}/turns",
            json={"content": "do not remember sk-abcdefghijklmnopqrstuvwxyz123456"},
            headers=headers,
        ).json()
        for _ in range(100):
            terminal = client.get(
                f"/api/conversations/{conv['id']}/turns/{turn['id']}",
                headers=headers,
            ).json()
            if terminal["state"] == "success":
                break
            time.sleep(0.02)
        time.sleep(0.05)
        assert len(model.requests) == 1
        assert service.list_memories(status="candidate")["items"] == []

    def test_turn_memory_usage_endpoint(self, api):
        client, headers, service, ws, model = api
        conv = client.post(
            "/api/conversations",
            json={"workspace": str(ws), "profile_id": None},
            headers=headers,
        ).json()
        turn = client.post(
            f"/api/conversations/{conv['id']}/turns",
            json={"content": "remember project stack"},
            headers=headers,
        ).json()
        # The memory projection is a no-op unless memory exists; create one and
        # invoke the service projection directly to produce a usage record.
        service.create_memory(
            scope_type="workspace",
            scope_key=str(ws.resolve()),
            kind="fact",
            content="project stack FastAPI React",
            source_conversation_id=conv["id"],
            source_turn_id=turn["id"],
        )
        projection = service._memory.project_for_turn(
            conversation_id=conv["id"],
            turn_id=turn["id"],
            workspace_key=os.path.normcase(str(ws.resolve())),
            user_text="project stack FastAPI React",
        )
        assert projection is not None and projection.commit_usage is not None
        projection.commit_usage()
        usage = client.get(
            f"/api/conversations/{conv['id']}/turns/{turn['id']}/memory-usage",
            headers=headers,
        )
        assert usage.status_code == 200
        assert len(usage.json()) >= 1
