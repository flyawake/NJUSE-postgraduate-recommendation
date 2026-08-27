"""Web API tests: DTO contract, run lifecycle, SSE, security, secrets.

Everything is offline: ScriptedModel fakes the provider, workspaces live in
tmp_path, CODING_AGENT_HOME points at a temp dir, and no test touches the
network.
"""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from coding_agent.web.app import create_app
from coding_agent.web.controller import RunController
from conftest import make_tool_call, turn
from test_run_controller import _scripted_loop_model, _seed_workspace, _verify_turns

FAKE_SECRET = "sk-fake-secret-12345"


@pytest.fixture
def workspace(tmp_path):
    _seed_workspace(tmp_path)
    return tmp_path


@pytest.fixture
def controller(workspace):
    factory, _ = _scripted_loop_model(*_verify_turns())
    return RunController(
        home=workspace / "home",
        env={"OPENAI_API_KEY": "sk-test", "OPENAI_MODEL": "fake-model"},
        client_factory=factory,
    )


@pytest.fixture
def client(controller):
    app = create_app(
        controller=controller,
        static_dir=controller.profile_store.path.parent / "static",
        session_token="test-session-token",
    )
    return TestClient(app, base_url="http://127.0.0.1")


def fetch_token(client) -> str:
    response = client.get("/api/bootstrap")
    assert response.status_code == 200
    return response.json()["session_token"]


def auth_headers(client) -> dict:
    return {"X-Coding-Agent-Token": fetch_token(client)}


class TestHealthAndBootstrap:
    def test_health_without_config(self, client):
        response = client.get("/api/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["run_state"] == "idle"

    def test_bootstrap_shapes_and_token(self, client):
        response = client.get("/api/bootstrap")
        body = response.json()
        assert body["session_token"] == "test-session-token"
        assert body["profiles"] == []
        assert body["active_profile_id"] is None
        assert body["capabilities"]["wire_apis"] == ["openai_chat_completions"]
        assert body["provider_presets"][0]["provider_id"] == "openai"
        assert body["run"] is None

    def test_csp_and_hardening_headers(self, client):
        response = client.get("/api/health")
        csp = response.headers["content-security-policy"]
        assert "script-src 'self'" in csp
        assert "connect-src 'self'" in csp
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["cache-control"] == "no-store"

    def test_missing_static_index_returns_hint(self, client):
        response = client.get("/")
        assert response.status_code == 503
        assert "npm run build" in response.text


class TestSecurity:
    def test_session_tokens_are_random(self, controller, workspace):
        app_a = create_app(
            controller=RunController(home=workspace / "home-a", env={}),
            static_dir=workspace,
        )
        app_b = create_app(
            controller=RunController(home=workspace / "home-b", env={}),
            static_dir=workspace,
        )
        client_a = TestClient(app_a, base_url="http://127.0.0.1")
        client_b = TestClient(app_b, base_url="http://127.0.0.1")
        token_a = client_a.get("/api/bootstrap").json()["session_token"]
        token_b = client_b.get("/api/bootstrap").json()["session_token"]
        assert token_a and token_b and token_a != token_b

    def test_bad_host_rejected(self, controller, workspace):
        app = create_app(controller=controller, static_dir=workspace)
        evil = TestClient(app, base_url="http://evil.example.test")
        response = evil.get("/api/health")
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "bad_host"

    @pytest.mark.parametrize(
        "host", ["http://127.0.0.1.evil.com", "http://127.attacker.com"]
    )
    def test_loopback_looking_dns_names_rejected(self, controller, workspace, host):
        app = create_app(controller=controller, static_dir=workspace)
        evil = TestClient(app, base_url=host)
        response = evil.get("/api/health")
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "bad_host"

    def test_state_change_requires_token(self, client):
        response = client.post(
            "/api/runs",
            json={"workspace": "x", "task": "t"},
        )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "invalid_session_token"

    def test_bad_origin_rejected_even_with_token(self, client):
        headers = auth_headers(client)
        headers["Origin"] = "http://evil.example.test"
        response = client.post(
            "/api/workspace/validate", json={"path": "."}, headers=headers
        )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "bad_origin"

    def test_same_origin_accepted(self, client):
        headers = auth_headers(client)
        headers["Origin"] = "http://127.0.0.1"
        response = client.post(
            "/api/workspace/validate", json={"path": ".", "extra": "1"}, headers=headers
        )
        assert response.status_code == 422  # extra field forbidden

    def test_different_loopback_port_origin_rejected(self, controller, workspace):
        app = create_app(
            controller=controller,
            static_dir=workspace,
            session_token="test-session-token",
        )
        port_client = TestClient(app, base_url="http://127.0.0.1:8000")
        headers = auth_headers(port_client)
        headers["Origin"] = "http://127.0.0.1:9999"
        response = port_client.post(
            "/api/workspace/validate", json={"path": "."}, headers=headers
        )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "bad_origin"

    def test_origin_with_path_or_userinfo_rejected(self, client):
        for origin in ("http://127.0.0.1/path", "http://user@127.0.0.1"):
            headers = auth_headers(client)
            headers["Origin"] = origin
            response = client.post(
                "/api/workspace/validate", json={"path": "."}, headers=headers
            )
            assert response.status_code == 403
            assert response.json()["error"]["code"] == "bad_origin"

    def test_malformed_host_port_rejected(self, client):
        response = client.get("/api/health", headers={"Host": "localhost:not-a-port"})
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "bad_host"

    def test_ipv6_loopback_host_accepted(self, client):
        assert client.get("/api/health", headers={"Host": "[::1]"}).status_code == 200

    def test_get_needs_no_token(self, client):
        assert client.get("/api/health").status_code == 200


class TestProfileAndCredentialApi:
    def test_profile_crud_and_credential_write_only(self, client):
        headers = auth_headers(client)
        response = client.post(
            "/api/profiles",
            json={
                "provider_id": "openai",
                "display_name": "OpenAI 主号",
                "base_url": "https://api.openai.com/v1",
                "model": "gpt-4o-mini",
                "credential_ref": "fake",
            },
            headers=headers,
        )
        assert response.status_code == 201
        profile = response.json()
        profile_id = profile["id"]
        assert profile["id"].startswith("openai-")
        assert profile["wire_api"] == "openai_chat_completions"
        assert profile["credential"]["configured"] is False
        assert profile["credential"]["writable"] is True

        # write credential
        response = client.put(
            f"/api/profiles/{profile_id}/credential",
            json={"secret": FAKE_SECRET},
            headers=headers,
        )
        assert response.status_code == 200
        info = response.json()
        assert info == {"configured": True, "source": "local_file", "writable": True}
        assert FAKE_SECRET not in response.text

        # read descriptor still never contains the secret
        response = client.get(f"/api/profiles/{profile_id}/credential", headers=headers)
        assert response.json()["configured"] is True
        assert FAKE_SECRET not in response.text

        # activate and list
        response = client.post(f"/api/profiles/{profile_id}/activate", headers=headers)
        assert response.json()["id"] == profile_id

        # update keeps id
        response = client.put(
            f"/api/profiles/{profile_id}",
            json={
                "provider_id": "openai",
                "display_name": "改名",
                "base_url": "https://api.openai.com/v1",
                "model": "gpt-4o",
                "credential_ref": "fake",
            },
            headers=headers,
        )
        assert response.status_code == 200
        assert response.json()["model"] == "gpt-4o"

        # unset credential
        response = client.delete(
            f"/api/profiles/{profile_id}/credential", headers=headers
        )
        assert response.json()["configured"] is False

        # delete profile
        response = client.delete(f"/api/profiles/{profile_id}", headers=headers)
        assert response.status_code == 204
        assert (
            client.get(
                f"/api/profiles/{profile_id}/credential", headers=headers
            ).status_code
            == 400
        )

    def test_invalid_profile_inputs(self, client):
        headers = auth_headers(client)
        response = client.post(
            "/api/profiles",
            json={
                "provider_id": "custom",
                "display_name": "ok",
                "base_url": "http://192.168.1.1/v1",
                "model": "m",
            },
            headers=headers,
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "invalid_config"
        assert response.json()["error"]["field"] == "base_url"

    def test_env_credential_is_readonly(self, workspace):
        factory, _ = _scripted_loop_model(*_verify_turns())
        controller = RunController(
            home=workspace / "home",
            env={"OPENAI_API_KEY": "env-key"},
            client_factory=factory,
        )
        client = TestClient(
            create_app(controller=controller, static_dir=workspace),
            base_url="http://127.0.0.1",
        )
        headers = auth_headers(client)
        profile = client.post(
            "/api/profiles",
            json={
                "provider_id": "openai",
                "display_name": "env shadowed",
                "base_url": "https://api.openai.com/v1",
                "model": "gpt-4o-mini",
                "credential_ref": "openai",
            },
            headers=headers,
        ).json()
        info = client.get(
            f"/api/profiles/{profile['id']}/credential", headers=headers
        ).json()
        assert info == {"configured": True, "source": "env", "writable": False}
        response = client.put(
            f"/api/profiles/{profile['id']}/credential",
            json={"secret": "whatever"},
            headers=headers,
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "credential_env_readonly"


class TestRunApi:
    def _start(self, client, workspace, headers, legacy=True):
        return client.post(
            "/api/runs",
            json={
                "workspace": str(workspace),
                "task": "实现 add 并验证",
                "profile_id": None,
            },
            headers=headers,
        )

    def _wait_terminal(self, client, run_id, timeout=30):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            snap = client.get(f"/api/runs/{run_id}").json()
            if snap["state"] == "terminal":
                return snap
            time.sleep(0.05)
        raise AssertionError("run did not finish")

    def test_legacy_fallback_requires_env(self, workspace):
        factory, _ = _scripted_loop_model(*_verify_turns())
        controller = RunController(
            home=workspace / "home", env={}, client_factory=factory
        )
        client = TestClient(
            create_app(controller=controller, static_dir=workspace),
            base_url="http://127.0.0.1",
        )
        headers = auth_headers(client)
        response = self._start(client, workspace, headers)
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "invalid_config"
        # No worker was created: the controller is still idle.
        assert client.get("/api/health").json()["run_state"] == "idle"

    def test_profile_run_reaches_verified(self, client, workspace):
        headers = auth_headers(client)
        created = client.post(
            "/api/profiles",
            json={
                "provider_id": "custom",
                "display_name": "本地假模型",
                "base_url": "http://127.0.0.1:9/v1",
                "model": "fake-model",
                "credential_ref": "fake",
            },
            headers=headers,
        ).json()
        client.put(
            f"/api/profiles/{created['id']}/credential",
            json={"secret": FAKE_SECRET},
            headers=headers,
        )
        response = client.post(
            "/api/runs",
            json={
                "workspace": str(workspace),
                "task": "实现 add 并验证",
                "profile_id": created["id"],
            },
            headers=headers,
        )
        assert response.status_code == 200
        snap = response.json()
        assert snap["state"] == "running"
        assert snap["run_id"]
        run_id = snap["run_id"]

        terminal = self._wait_terminal(client, run_id)
        assert terminal["state"] == "terminal"
        assert terminal["status"] == "SUCCESS"
        assert terminal["verification_status"] == "VERIFIED"
        assert terminal["mutated_paths"] == ["src/app.py"]
        assert terminal["tool_call_count"] == 5
        assert terminal["final_text"] == "已完成：实现 add 并通过 py_compile 验证。"
        assert terminal["error"] is None
        # Snapshot events are monotonic and complete.
        kinds = [event["kind"] for event in terminal["events"]]
        assert kinds[0] == "run_started" and kinds[-1] == "run_finished"
        # No secret anywhere in the API payload.
        assert FAKE_SECRET not in json.dumps(terminal)

    def test_invalid_workspace_and_unknown_run(self, client, workspace):
        headers = auth_headers(client)
        response = client.post(
            "/api/runs",
            json={"workspace": str(workspace / "no-such-path"), "task": "t"},
            headers=headers,
        )
        assert response.status_code == 400
        body = response.json()
        assert body["error"]["code"] == "invalid_workspace"
        assert body["error"]["field"] == "workspace"
        response = client.get("/api/runs/unknown-id")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "run_not_found"

    def test_workspace_validate_endpoint(self, client, workspace):
        headers = auth_headers(client)
        good = client.post(
            "/api/workspace/validate", json={"path": str(workspace)}, headers=headers
        )
        assert good.json()["valid"] is True
        bad = client.post(
            "/api/workspace/validate",
            json={"path": str(workspace / "nope")},
            headers=headers,
        )
        assert bad.json()["valid"] is False
        assert bad.json()["error"]["code"] == "invalid_workspace"

    def test_cancel_flow_idempotent(self, workspace):
        from test_run_controller import BlockingModel

        factory = lambda _connection: BlockingModel(delay=3.0)  # noqa: E731
        controller = RunController(
            home=workspace / "home",
            env={"OPENAI_API_KEY": "sk-test", "OPENAI_MODEL": "fake"},
            client_factory=factory,
        )
        client = TestClient(
            create_app(controller=controller, static_dir=workspace),
            base_url="http://127.0.0.1",
        )
        headers = auth_headers(client)
        snap = client.post(
            "/api/runs",
            json={"workspace": str(workspace), "task": "t"},
            headers=headers,
        ).json()
        run_id = snap["run_id"]
        cancelled = client.post(f"/api/runs/{run_id}/cancel", headers=headers)
        assert cancelled.status_code == 200
        terminal = self._wait_terminal(client, run_id)
        assert terminal["status"] == "INTERRUPTED"
        # Idempotent repeat cancel returns the same terminal snapshot.
        again = client.post(f"/api/runs/{run_id}/cancel", headers=headers)
        assert again.status_code == 200
        assert again.json()["finished_at"] == terminal["finished_at"]

    def test_sse_stream_events_and_end(self, client, workspace):
        headers = auth_headers(client)
        snap = client.post(
            "/api/runs",
            json={"workspace": str(workspace), "task": "t", "profile_id": None},
            headers=headers,
        )
        run_id = snap.json()["run_id"]
        # connect to the stream and consume until end
        with client.stream(
            "GET", f"/api/runs/{run_id}/events?last_event_id=0"
        ) as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            kinds = []
            ids = []
            for line in response.iter_lines():
                if not line:
                    continue
                if line.startswith("event: "):
                    kinds.append(line[len("event: ") :])
                elif line.startswith("id: "):
                    ids.append(int(line[len("id: ") :]))
                elif line.startswith("data: "):
                    pass
                if kinds and kinds[-1] == "end":
                    break
        assert "hello" in kinds
        assert "run_started" in kinds
        assert "tool_started" in kinds
        assert "run_finished" in kinds
        assert kinds[-1] == "end"
        assert ids == sorted(ids)


class TestEventRedaction:
    SENTINEL = "SENTINEL-SECRET-DO-NOT-LEAK"

    def _wait_terminal(self, client, run_id):
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            snap = client.get(f"/api/runs/{run_id}").json()
            if snap["state"] == "terminal":
                return snap
            time.sleep(0.05)
        raise AssertionError("run did not reach terminal state")

    def test_sentinel_never_reaches_snapshot_or_events(self, workspace):
        factory, _ = _scripted_loop_model(
            turn(
                calls=[
                    make_tool_call(
                        "write_file",
                        {"path": "leak.txt", "content": self.SENTINEL},
                    )
                ]
            ),
            turn(
                calls=[
                    make_tool_call(
                        "run_command",
                        {
                            "argv": ["python", "--api-key", self.SENTINEL],
                            "purpose": "inspect",
                        },
                    )
                ]
            ),
            turn(text="done"),
        )
        controller = RunController(
            home=workspace / "home",
            env={"OPENAI_API_KEY": "sk-test", "OPENAI_MODEL": "fake"},
            client_factory=factory,
        )
        client = TestClient(
            create_app(controller=controller, static_dir=workspace),
            base_url="http://127.0.0.1",
        )
        headers = auth_headers(client)
        snap = client.post(
            "/api/runs",
            json={"workspace": str(workspace), "task": "t"},
            headers=headers,
        ).json()
        terminal = self._wait_terminal(client, snap["run_id"])
        payload = json.dumps(terminal, ensure_ascii=False)
        assert self.SENTINEL not in payload
        # The redacted write arguments keep the path but hide the content.
        write_started = next(
            event
            for event in terminal["events"]
            if event["kind"] == "tool_started"
            and event["payload"].get("name") == "write_file"
        )
        assert "leak.txt" in write_started["payload"]["arguments"]
        assert self.SENTINEL not in write_started["payload"]["arguments"]
        run_finished = next(
            event
            for event in terminal["events"]
            if event["kind"] == "tool_finished"
            and event["payload"].get("name") == "run_command"
        )
        assert self.SENTINEL not in json.dumps(run_finished, ensure_ascii=False)

    def test_worker_assertion_text_is_never_exposed(self, workspace):
        sentinel = self.SENTINEL

        class BrokenLoop:
            def run(self, task):
                raise AssertionError(f"boom {sentinel}")

        controller = RunController(
            home=workspace / "home",
            env={"OPENAI_API_KEY": "sk-test", "OPENAI_MODEL": "fake"},
        )
        controller._loop_builder = lambda **_kwargs: BrokenLoop()
        client = TestClient(
            create_app(controller=controller, static_dir=workspace),
            base_url="http://127.0.0.1",
        )
        headers = auth_headers(client)
        snap = client.post(
            "/api/runs",
            json={"workspace": str(workspace), "task": "t"},
            headers=headers,
        ).json()
        terminal = self._wait_terminal(client, snap["run_id"])
        assert terminal["status"] == "ERROR"
        assert terminal["error"]["code"] == "internal_error"
        assert self.SENTINEL not in json.dumps(terminal, ensure_ascii=False)
        assert "boom" not in terminal["error"]["message"]
