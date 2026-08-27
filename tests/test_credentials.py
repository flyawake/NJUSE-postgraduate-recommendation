"""Credential service tests: env priority, write-only, atomic storage."""

from __future__ import annotations

import json

import pytest

from coding_agent.credentials import (
    CREDENTIAL_ENV_PREFIX,
    CredentialError,
    CredentialService,
    env_var_for_ref,
    validate_credential_ref,
)


@pytest.fixture
def home(tmp_path):
    return tmp_path / "home"


@pytest.fixture
def no_env():
    return {}


class TestEnvNaming:
    def test_legacy_openai_maps_to_openai_api_key(self):
        assert env_var_for_ref("openai") == "OPENAI_API_KEY"

    def test_generic_ref_mapping(self):
        assert env_var_for_ref("deepseek") == CREDENTIAL_ENV_PREFIX + "DEEPSEEK"
        assert env_var_for_ref("my-gateway") == CREDENTIAL_ENV_PREFIX + "MY_GATEWAY"

    def test_ref_validation(self):
        validate_credential_ref("ok-1_2")
        with pytest.raises(CredentialError):
            validate_credential_ref("bad ref!")
        with pytest.raises(CredentialError):
            validate_credential_ref("")


class TestResolve:
    def test_env_wins_over_local(self, home, no_env):
        service = CredentialService(
            home, env={"CODING_AGENT_CRED_DEEPSEEK": "env-secret"}
        )
        service.set("openai", "local-secret")  # a *different* ref stays writable
        # Simulate a previously saved local value for deepseek:
        service_written = CredentialService(home, env=no_env)
        service_written.set("deepseek", "local-secret")
        service = CredentialService(
            home, env={"CODING_AGENT_CRED_DEEPSEEK": "env-secret"}
        )
        resolved = service.resolve("deepseek")
        assert resolved.value == "env-secret"
        assert resolved.source == "env"

    def test_local_when_no_env(self, home, no_env):
        service = CredentialService(home, env=no_env)
        service.set("deepseek", "local-secret")
        resolved = service.resolve("deepseek")
        assert resolved.value == "local-secret"
        assert resolved.source == "local_file"

    def test_missing_raises(self, home, no_env):
        service = CredentialService(home, env=no_env)
        with pytest.raises(CredentialError) as exc:
            service.resolve("deepseek")
        assert exc.value.code == "credential_not_configured"

    def test_legacy_openai_env(self, home):
        service = CredentialService(home, env={"OPENAI_API_KEY": "sk-env"})
        assert service.resolve("openai").value == "sk-env"


class TestDescriptors:
    def test_info_shapes(self, home, no_env):
        service = CredentialService(home, env=no_env)
        info = service.info("deepseek")
        assert (info.configured, info.source, info.writable) == (False, None, True)
        service.set("deepseek", "x")
        info = service.info("deepseek")
        assert (info.configured, info.source, info.writable) == (
            True,
            "local_file",
            True,
        )

    def test_env_info_is_readonly(self, home):
        service = CredentialService(home, env={"CODING_AGENT_CRED_DEEPSEEK": "s"})
        info = service.info("deepseek")
        assert (info.configured, info.source, info.writable) == (True, "env", False)

    def test_no_value_read_back(self, home, no_env):
        """The service never exposes stored values via info or list."""
        service = CredentialService(home, env=no_env)
        service.set("deepseek", "top-secret")
        info = service.info("deepseek")
        assert not hasattr(info, "value")
        assert info.configured is True and "top-secret" not in repr(info)


class TestMutation:
    def test_set_unset_roundtrip(self, home, no_env):
        service = CredentialService(home, env=no_env)
        service.set("deepseek", "local-secret")
        raw = json.loads((home / "credentials.json").read_text("utf-8"))
        assert raw == {
            "version": 1,
            "credentials": {"deepseek": "local-secret"},
        }
        assert service.resolve("deepseek").value == "local-secret"
        service.unset("deepseek")
        assert service.info("deepseek").configured is False

    def test_env_readonly_rejects_set_and_unset(self, home):
        service = CredentialService(home, env={"CODING_AGENT_CRED_DEEPSEEK": "s"})
        with pytest.raises(CredentialError) as exc:
            service.set("deepseek", "other")
        assert exc.value.code == "credential_env_readonly"
        with pytest.raises(CredentialError) as exc:
            service.unset("deepseek")
        assert exc.value.code == "credential_env_readonly"
        assert not (home / "credentials.json").exists()

    def test_rejects_empty_or_huge_secret(self, home, no_env):
        service = CredentialService(home, env=no_env)
        with pytest.raises(CredentialError):
            service.set("deepseek", "   ")
        with pytest.raises(CredentialError):
            service.set("deepseek", "x" * 5000)

    def test_corrupt_file_never_overwritten(self, home, no_env):
        home.mkdir(parents=True)
        (home / "credentials.json").write_text("{ broken", encoding="utf-8")
        service = CredentialService(home, env=no_env)
        with pytest.raises(CredentialError):
            service.set("deepseek", "secret")
        assert (home / "credentials.json").read_text("utf-8") == "{ broken"

    def test_unknown_version_raises(self, home, no_env):
        home.mkdir(parents=True)
        (home / "credentials.json").write_text(
            json.dumps({"version": 2, "credentials": {}}), encoding="utf-8"
        )
        service = CredentialService(home, env=no_env)
        with pytest.raises(CredentialError):
            service.info("deepseek")
