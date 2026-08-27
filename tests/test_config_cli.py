"""Config and CLI behavior tests (A1, A2)."""

from __future__ import annotations

import argparse

import pytest

from coding_agent import cli
from coding_agent.config import load_config
from coding_agent.credentials import CredentialService
from coding_agent.errors import ConfigError
from coding_agent.provider_config import ProfileStore, ProviderProfile
from conftest import ScriptedModel, build_loop, turn


def test_load_config_from_env_dict(tmp_path):
    config = load_config(
        str(tmp_path),
        env={"OPENAI_API_KEY": "sk-test", "OPENAI_MODEL": "test-model"},
    )
    assert config.api_key == "sk-test"
    assert config.model == "test-model"
    assert config.max_steps == 20


def test_missing_config_lists_fields(tmp_path):
    with pytest.raises(ConfigError) as excinfo:
        load_config(str(tmp_path), env={})
    message = str(excinfo.value)
    assert "OPENAI_API_KEY" in message
    assert "OPENAI_MODEL" in message


def test_cli_overrides_model_and_base_url(tmp_path):
    config = load_config(
        str(tmp_path),
        model="cli-model",
        base_url="http://localhost:9999/v1",
        env={"OPENAI_API_KEY": "sk-test", "OPENAI_MODEL": "env-model"},
    )
    assert config.model == "cli-model"
    assert config.base_url == "http://localhost:9999/v1"


def test_legacy_base_url_uses_same_url_validator(tmp_path):
    with pytest.raises(ConfigError):
        load_config(
            str(tmp_path),
            env={
                "OPENAI_API_KEY": "sk-test",
                "OPENAI_MODEL": "m",
                "OPENAI_BASE_URL": "http://api.example.com/v1",
            },
        )
    with pytest.raises(ConfigError):
        load_config(
            str(tmp_path),
            env={
                "OPENAI_API_KEY": "sk-test",
                "OPENAI_MODEL": "m",
                "OPENAI_BASE_URL": "https://user:pass@api.example.com/v1",
            },
        )
    config = load_config(
        str(tmp_path),
        env={
            "OPENAI_API_KEY": "sk-test",
            "OPENAI_MODEL": "m",
            "OPENAI_BASE_URL": "https://api.example.com/v1",
        },
    )
    assert config.base_url == "https://api.example.com/v1"


@pytest.mark.parametrize("max_steps", [0, 51, 100])
def test_max_steps_out_of_range_rejected(tmp_path, max_steps):
    with pytest.raises(ConfigError):
        load_config(
            str(tmp_path),
            max_steps=max_steps,
            env={"OPENAI_API_KEY": "sk-test", "OPENAI_MODEL": "m"},
        )


def test_workspace_must_exist(tmp_path):
    with pytest.raises(ConfigError):
        load_config(
            str(tmp_path / "definitely-not-here"),
            env={"OPENAI_API_KEY": "sk-test", "OPENAI_MODEL": "m"},
        )


def test_cli_help_exits_zero_without_env(capsys, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "--workspace" in out


def test_cli_missing_config_exits_one_and_never_prints_key(
    tmp_path, capsys, monkeypatch
):
    monkeypatch.setenv("CODING_AGENT_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-super-secret-123")
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    assert cli.main(["--workspace", str(tmp_path), "task"]) == 1
    captured = capsys.readouterr()
    assert "OPENAI_MODEL" in captured.err
    assert "sk-super-secret-123" not in captured.err
    assert "sk-super-secret-123" not in captured.out


def test_cli_invalid_max_steps_exits_one(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("CODING_AGENT_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_MODEL", "m")
    assert cli.main(["--workspace", str(tmp_path), "--max-steps", "99", "task"]) == 1
    assert "max-steps" in capsys.readouterr().err


def test_cli_offline_run_prints_answer_and_exits_zero(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("CODING_AGENT_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_MODEL", "fake-model")
    scripted = ScriptedModel([turn("已完成任务")])

    def factory(config, is_cancelled, sink):
        loop, _sink, _model = build_loop(tmp_path, scripted, sink=sink)
        return loop

    exit_code = cli.main(["--workspace", str(tmp_path), "task"], agent_factory=factory)
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "已完成任务" in captured.out
    assert "sk-test" not in captured.out
    assert "sk-test" not in captured.err


def test_cli_offline_error_exits_one(tmp_path, capsys, monkeypatch):

    from conftest import AlwaysFailModel

    monkeypatch.setenv("CODING_AGENT_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_MODEL", "fake-model")
    model = AlwaysFailModel(retryable=False)

    def factory(config, is_cancelled, sink):
        loop, _sink, _model = build_loop(tmp_path, model, sink=sink)
        return loop

    exit_code = cli.main(["--workspace", str(tmp_path), "task"], agent_factory=factory)
    captured = capsys.readouterr()
    assert exit_code == 1
    assert "MODEL_ERROR" in captured.err


def _profile(home, profile_id, model, base_url="https://api.example.com/v1"):
    store = ProfileStore(home)
    store.create(
        ProviderProfile(
            id=profile_id,
            provider_id="custom",
            display_name=profile_id,
            wire_api="openai_chat_completions",
            base_url=base_url,
            model=model,
            credential_ref=profile_id,
        )
    )
    CredentialService(home).set(profile_id, f"sk-local-{profile_id}")
    return store


def _namespace(tmp_path, profile=None, model=None, base_url=None, max_steps=None):
    return argparse.Namespace(
        workspace=str(tmp_path),
        profile=profile,
        model=model,
        base_url=base_url,
        max_steps=max_steps,
    )


def test_cli_resolver_uses_active_profile_before_legacy_env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    store = _profile(home, "p1", "active-model", "https://active.example.com/v1")
    monkeypatch.setenv("CODING_AGENT_HOME", str(home))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-legacy")
    monkeypatch.setenv("OPENAI_MODEL", "legacy-model")

    config = cli._resolve_config(_namespace(tmp_path))
    assert config.model == "active-model"
    assert config.base_url == "https://active.example.com/v1"
    assert config.api_key == "sk-local-p1"
    assert store.load().active_profile == "p1"


def test_cli_resolver_explicit_profile_overrides_active(tmp_path, monkeypatch):
    home = tmp_path / "home"
    _profile(home, "p1", "active-model")
    _profile(home, "p2", "explicit-model", "https://explicit.example.com/v1")
    monkeypatch.setenv("CODING_AGENT_HOME", str(home))

    config = cli._resolve_config(_namespace(tmp_path, profile="p2"))
    assert config.model == "explicit-model"
    assert config.base_url == "https://explicit.example.com/v1"


def test_cli_resolver_rejects_unknown_explicit_profile(tmp_path, monkeypatch):
    home = tmp_path / "home"
    _profile(home, "p1", "active-model")
    monkeypatch.setenv("CODING_AGENT_HOME", str(home))
    with pytest.raises(ConfigError):
        cli._resolve_config(_namespace(tmp_path, profile="missing"))


def test_cli_resolver_falls_back_to_legacy_env_without_profiles(tmp_path, monkeypatch):
    monkeypatch.setenv("CODING_AGENT_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-legacy")
    monkeypatch.setenv("OPENAI_MODEL", "legacy-model")
    config = cli._resolve_config(_namespace(tmp_path))
    assert config.model == "legacy-model"
    assert config.api_key == "sk-legacy"


def test_cli_resolver_rejects_corrupt_profile_store(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.json").write_text("{not json", encoding="utf-8")
    monkeypatch.setenv("CODING_AGENT_HOME", str(home))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-legacy")
    monkeypatch.setenv("OPENAI_MODEL", "legacy-model")
    with pytest.raises(ConfigError):
        cli._resolve_config(_namespace(tmp_path))
