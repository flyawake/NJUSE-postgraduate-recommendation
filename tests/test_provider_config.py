"""Profile store and provider catalog tests (offline, tmp home)."""

from __future__ import annotations

import json

import pytest

from coding_agent.provider_config import (
    CONFIG_VERSION,
    ProfileError,
    ProfileStore,
    ProviderCatalog,
    validate_profile,
    validate_provider_url,
)


def make_profile(profile_id="deepseek-main", **overrides):
    values = {
        "profile_id": profile_id,
        "provider_id": "deepseek",
        "display_name": "DeepSeek 主账号",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-chat",
        "wire_api": "openai_chat_completions",
        "credential_ref": "deepseek",
    }
    values.update(overrides)
    return validate_profile(**values)


# ---------------------------------------------------------------- URL rules


class TestProviderUrlValidation:
    def test_accepts_https_any_host(self):
        assert (
            validate_provider_url("https://api.deepseek.com")
            == "https://api.deepseek.com"
        )
        assert (
            validate_provider_url("https://vpn.example.net/v1/")
            == "https://vpn.example.net/v1/"
        )

    def test_accepts_loopback_http(self):
        assert (
            validate_provider_url("http://127.0.0.1:8080/v1")
            == "http://127.0.0.1:8080/v1"
        )
        assert (
            validate_provider_url("http://localhost:11434/v1")
            == "http://localhost:11434/v1"
        )
        assert validate_provider_url("http://[::1]:11434/v1") == "http://[::1]:11434/v1"

    def test_rejects_dns_names_that_resemble_loopback_prefixes(self):
        for url in (
            "http://127.0.0.1.evil.com/v1",
            "http://127.attacker.com/v1",
            "http://127.evil.example:8080",
        ):
            with pytest.raises(ProfileError):
                validate_provider_url(url)

    def test_rejects_invalid_ports(self):
        for url in (
            "http://127.0.0.1:bad/v1",
            "https://api.example.com:99999/v1",
            "https://api.example.com:not-a-port",
        ):
            with pytest.raises(ProfileError):
                validate_provider_url(url)

    @pytest.mark.parametrize(
        "url",
        [
            "",
            "   ",
            "api.deepseek.com",
            "ftp://api.deepseek.com",
            "/relative/path",
            "https://user:pass@api.deepseek.com",
            "https://api.deepseek.com?x=1",
            "https://api.deepseek.com#frag",
            "http://192.168.1.10/v1",
            "http://example.com/v1",
        ],
    )
    def test_rejects_bad_urls(self, url):
        with pytest.raises(ProfileError):
            validate_provider_url(url)


class TestProfileValidation:
    def test_validates_fields(self):
        profile = make_profile()
        assert profile.id == "deepseek-main"
        assert profile.credential_ref == "deepseek"
        assert profile.context_window_tokens == 128_000

    def test_context_window_is_model_specific_and_bounded(self):
        assert (
            make_profile(context_window_tokens=256_000).context_window_tokens == 256_000
        )
        for invalid in (15_999, 4_000_001, True, 128_000.5):
            with pytest.raises(ProfileError, match="context_window_tokens"):
                make_profile(context_window_tokens=invalid)

    def test_rejects_unknown_provider(self):
        with pytest.raises(ProfileError, match="provider"):
            make_profile(provider_id="anthropic")

    def test_rejects_unsupported_wire_api(self):
        with pytest.raises(ProfileError, match="wire_api"):
            make_profile(wire_api="anthropic_messages")

    def test_deepseek_chat_allows_reasoning_effort(self):
        profile = make_profile(
            provider_id="deepseek",
            wire_api="openai_chat_completions",
            reasoning_mode="visible",
            reasoning_effort="high",
        )
        assert profile.reasoning_effort == "high"

    def test_deepseek_chat_accepts_max_effort(self):
        profile = make_profile(
            provider_id="deepseek",
            wire_api="openai_chat_completions",
            reasoning_effort="max",
        )
        assert profile.reasoning_effort == "max"

    def test_rejects_empty_model(self):
        with pytest.raises(ProfileError, match="model"):
            make_profile(model="  ")

    def test_rejects_bad_profile_id(self):
        with pytest.raises(ProfileError):
            make_profile(profile_id="bad id")
        with pytest.raises(ProfileError):
            make_profile(profile_id="-leading")

    def test_optional_credential_ref(self):
        profile = make_profile(credential_ref=None)
        assert profile.credential_ref is None

    def test_rejects_incompatible_reasoning_capabilities(self):
        with pytest.raises(ProfileError, match="Responses"):
            make_profile(wire_api="openai_responses")
        with pytest.raises(ProfileError, match="reasoning_effort"):
            make_profile(provider_id="custom", reasoning_effort="high")
        with pytest.raises(ProfileError, match="reasoning_effort"):
            make_profile(reasoning_mode="off", reasoning_effort="low")
        with pytest.raises(ProfileError, match="不提供可展示"):
            make_profile(
                provider_id="openai",
                reasoning_mode="visible",
            )

    def test_corrupt_show_reasoning_string_fails_closed(self, tmp_path):
        home = tmp_path / "corrupt-bool"
        store = ProfileStore(home)
        home.mkdir(parents=True)
        raw_profile = make_profile().to_dict()
        raw_profile["show_reasoning"] = "false"
        store.path.write_text(
            json.dumps(
                {
                    "version": CONFIG_VERSION,
                    "active_profile": "deepseek-main",
                    "profiles": {"deepseek-main": raw_profile},
                }
            ),
            encoding="utf-8",
        )
        with pytest.raises(ProfileError, match="布尔值"):
            store.load()


class TestCatalog:
    def test_presets_are_chat_completions_only(self):
        catalog = ProviderCatalog()
        presets = catalog.presets()
        assert [p.provider_id for p in presets] == ["openai", "deepseek", "custom"]
        assert catalog.preset("openai").default_base_url == "https://api.openai.com/v1"
        assert catalog.preset("custom").default_base_url == ""


class TestProfileStore:
    @pytest.fixture
    def store(self, tmp_path):
        return ProfileStore(tmp_path / "home")

    def test_create_list_activate(self, store):
        store.create(make_profile(profile_id="openai-main", provider_id="openai"))
        store.create(make_profile(profile_id="deepseek-main"))
        profiles = store.list_profiles()
        assert [p.id for p in profiles] == ["deepseek-main", "openai-main"]
        # first created profile becomes active automatically
        assert store.load().active_profile == "openai-main"
        store.activate("deepseek-main")
        assert store.load().active_profile == "deepseek-main"

    def test_config_json_shape(self, store):
        store.create(make_profile(profile_id="deepseek-main"))
        raw = json.loads((store.path).read_text("utf-8"))
        assert raw["version"] == CONFIG_VERSION
        assert raw["active_profile"] == "deepseek-main"
        assert "profiles" in raw
        assert "credential_ref" in raw["profiles"]["deepseek-main"]
        assert "api_key" not in json.dumps(raw)

    def test_update_keeps_id_and_persists(self, store):
        store.create(make_profile(profile_id="deepseek-main"))
        updated = make_profile(
            profile_id="deepseek-main",
            display_name="改名",
            model="deepseek-v4",
            credential_ref=None,
        )
        store.update("deepseek-main", updated)
        assert store.get("deepseek-main").display_name == "改名"
        with pytest.raises(ProfileError, match="不可修改"):
            store.update("deepseek-main", make_profile(profile_id="other-id"))

    def test_delete_resets_active(self, store):
        store.create(make_profile(profile_id="deepseek-main"))
        store.delete("deepseek-main")
        assert store.load().active_profile is None
        assert store.get("deepseek-main") is None

    def test_corrupt_config_raises_and_is_preserved(self, store):
        store.path.parent.mkdir(parents=True)
        store.path.write_text("{ not json", encoding="utf-8")
        with pytest.raises(ProfileError, match="损坏"):
            store.list_profiles()
        with pytest.raises(ProfileError, match="损坏"):
            store.create(make_profile(profile_id="other"))
        # original file must be untouched after the failed write attempt
        assert store.path.read_text("utf-8") == "{ not json"

    def test_unknown_version_raises_and_preserves(self, store):
        store.path.parent.mkdir(parents=True)
        store.path.write_text(
            json.dumps({"version": 99, "active_profile": None, "profiles": {}}),
            encoding="utf-8",
        )
        with pytest.raises(ProfileError, match="版本"):
            store.list_profiles()

    @pytest.mark.parametrize("raw", [None, [], True, "not-an-object"])
    def test_config_root_must_be_object(self, store, raw):
        store.path.parent.mkdir(parents=True)
        store.path.write_text(json.dumps(raw), encoding="utf-8")
        with pytest.raises(ProfileError) as exc:
            store.list_profiles()
        assert exc.value.code == "config_corrupt"
        assert exc.value.field == "config"

    def test_boolean_version_is_not_integer_version_one(self, store):
        store.path.parent.mkdir(parents=True)
        store.path.write_text(
            json.dumps({"version": True, "active_profile": None, "profiles": {}}),
            encoding="utf-8",
        )
        with pytest.raises(ProfileError) as exc:
            store.list_profiles()
        assert exc.value.code == "config_corrupt"
        assert exc.value.field == "version"

    def test_active_profile_must_exist(self, store):
        store.path.parent.mkdir(parents=True)
        store.path.write_text(
            json.dumps(
                {
                    "version": CONFIG_VERSION,
                    "active_profile": "ghost",
                    "profiles": {},
                }
            ),
            encoding="utf-8",
        )
        with pytest.raises(ProfileError, match="不存在"):
            store.list_profiles()

    def test_rejects_traversal_like_field_injection(self, store):
        with pytest.raises(ProfileError):
            make_profile(profile_id="../../evil")

    def test_atomic_save_keeps_old_file_on_injection_failure(self, store):
        store.create(make_profile(profile_id="deepseek-main"))
        # Make the replace step fail cross-platform: config.json becomes a
        # directory, so os.replace(tmp, path) cannot succeed.
        store.path.unlink()
        store.path.mkdir()
        with pytest.raises(Exception):
            store.create(make_profile(profile_id="second"))
        assert store.path.is_dir()
        # The in-memory view was not corrupted by the failed write either.
        assert store.get("second") is None
        assert store.get("deepseek-main") is not None
        # The original content was not clobbered: a directory now sits where
        # the file was, and the temp file was cleaned up.
        leftovers = list(store.path.parent.glob(".config.json.*.tmp"))
        assert leftovers == []

    def test_crud_failures_roll_back_in_memory(self, store, monkeypatch):
        p1 = store.create(make_profile(profile_id="p1"))
        store.create(make_profile(profile_id="p2"))
        original_active = store.load().active_profile

        import coding_agent.provider_config as module

        def boom(_path, _data):
            raise module.StorageError("injected write failure")

        monkeypatch.setattr(module, "atomic_write_json", boom)

        with pytest.raises(module.StorageError):
            store.create(make_profile(profile_id="p3"))
        assert store.get("p3") is None

        with pytest.raises(module.StorageError):
            store.update(
                "p1",
                make_profile(profile_id="p1", model="changed-model"),
            )
        assert store.get("p1").model == p1.model

        with pytest.raises(module.StorageError):
            store.delete("p2")
        assert store.get("p2") is not None

        with pytest.raises(module.StorageError):
            store.activate("p2")
        assert store.load().active_profile == original_active

    def test_strict_parse_rejects_unknown_and_mismatched_fields(self, store):
        base = make_profile(profile_id="p1")
        raw = {
            "version": CONFIG_VERSION,
            "active_profile": None,
            "profiles": {
                "p1": {**base.to_dict(), "extra_field": "nope"},
            },
        }
        store.path.parent.mkdir(parents=True)
        store.path.write_text(json.dumps(raw), encoding="utf-8")
        with pytest.raises(ProfileError, match="未知字段"):
            store.list_profiles()

        mismatched = {**base.to_dict(), "id": "other-id"}
        store.path.write_text(
            json.dumps(
                {
                    "version": CONFIG_VERSION,
                    "active_profile": None,
                    "profiles": {"p1": mismatched},
                }
            ),
            encoding="utf-8",
        )
        with pytest.raises(ProfileError, match="不一致"):
            store.list_profiles()

        top_unknown = {
            "version": CONFIG_VERSION,
            "active_profile": None,
            "profiles": {},
            "surprise": True,
        }
        store.path.write_text(json.dumps(top_unknown), encoding="utf-8")
        with pytest.raises(ProfileError, match="未知字段"):
            store.list_profiles()
