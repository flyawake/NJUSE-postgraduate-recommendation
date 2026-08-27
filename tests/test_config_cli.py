"""Config and CLI behavior tests (A1, A2)."""

from __future__ import annotations

import pytest

from coding_agent import cli
from coding_agent.config import load_config
from coding_agent.errors import ConfigError
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


@pytest.mark.parametrize("max_steps", [0, 51, 100])
def test_max_steps_out_of_range_rejected(tmp_path, max_steps):
    with pytest.raises(ConfigError):
        load_config(
            str(tmp_path),
            max_steps=max_steps,
            env={"OPENAI_API_KEY": "sk-test", "OPENAI_MODEL": "m"},
        )


def test_workspace_must_exist():
    with pytest.raises(ConfigError):
        load_config(
            "Z:/definitely/not/here",
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


def test_cli_missing_config_exits_one_and_never_prints_key(capsys, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-super-secret-123")
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    assert cli.main(["--workspace", ".", "task"]) == 1
    captured = capsys.readouterr()
    assert "OPENAI_MODEL" in captured.err
    assert "sk-super-secret-123" not in captured.err
    assert "sk-super-secret-123" not in captured.out


def test_cli_invalid_max_steps_exits_one(capsys, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_MODEL", "m")
    assert cli.main(["--workspace", ".", "--max-steps", "99", "task"]) == 1
    assert "max-steps" in capsys.readouterr().err


def test_cli_offline_run_prints_answer_and_exits_zero(tmp_path, capsys, monkeypatch):
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
