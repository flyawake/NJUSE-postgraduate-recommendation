"""Provider profile catalog, validation and user-level persistence.

A profile describes one *model connection* (provider preset, OpenAI-
compatible base URL and model name) plus an optional reference to a
credential. Wire API is currently only ``openai_chat_completions``; the
catalog must not be confused with native protocol support.

The on-disk config lives at ``$CODING_AGENT_HOME/config.json`` (default
``~/.coding-agent``) and is shared with ``credentials.json``. It is a plain
user-level store, not a system keychain, and may contain no secrets.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .errors import ConfigError
from .netutil import is_loopback_host
from .storage import StorageError, atomic_write_json, load_json

CONFIG_VERSION = 1
WIRE_API_CHAT_COMPLETIONS = "openai_chat_completions"
WIRE_API_RESPONSES = "openai_responses"
WIRE_APIS = (WIRE_API_CHAT_COMPLETIONS, WIRE_API_RESPONSES)
CONFIG_FILENAME = "config.json"

_PROFILE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
MAX_DISPLAY_NAME = 60
MAX_MODEL = 200
MAX_BASE_URL = 500


class ProfileError(ConfigError):
    """A profile configuration is invalid."""

    def __init__(
        self,
        message: str,
        *,
        field: Optional[str] = None,
        code: str = "invalid_config",
    ) -> None:
        super().__init__(message)
        self.field = field
        self.code = code


def default_home() -> Path:
    import os

    override = os.environ.get("CODING_AGENT_HOME")
    if override:
        return Path(override).expanduser()
    return Path("~").expanduser() / ".coding-agent"


def validate_profile_id(profile_id: str) -> str:
    if not isinstance(profile_id, str) or not _PROFILE_ID_RE.match(profile_id):
        raise ProfileError(
            "profile ID 只能包含字母、数字、下划线与连字符，且以字母或数字开头（最长 64 字符）",
            field="id",
        )
    return profile_id


def validate_provider_url(url: str) -> str:
    """Validate a provider base URL.

    Rules: absolute ``http``/``https`` URL, no userinfo, no query string, no
    fragment, valid numeric port when present. HTTPS is allowed for any host;
    HTTP is allowed only for loopback addresses.
    """
    from urllib.parse import urlparse

    if not isinstance(url, str) or not url.strip():
        raise ProfileError("base URL 不能为空", field="base_url")
    value = url.strip()
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ProfileError("base URL 必须是合法的 http(s) 绝对地址", field="base_url")
    if parsed.username is not None or parsed.password is not None:
        raise ProfileError(
            "base URL 不允许包含 userinfo（用户名/密码）", field="base_url"
        )
    if parsed.query or parsed.fragment:
        raise ProfileError("base URL 不允许携带 query 或 fragment", field="base_url")
    try:
        parsed.port  # noqa: B018 - accessor raises ValueError on invalid port
    except ValueError as exc:
        raise ProfileError(
            "base URL 端口不合法（必须是数字端口）", field="base_url"
        ) from exc
    if parsed.scheme == "http" and not is_loopback_host(parsed.hostname):
        raise ProfileError(
            "HTTP 地址仅允许本机回环地址（127.0.0.1/localhost/[::1]），远程地址必须使用 HTTPS",
            field="base_url",
        )
    if len(value) > MAX_BASE_URL:
        raise ProfileError(
            f"base URL 过长（最多 {MAX_BASE_URL} 字符）", field="base_url"
        )
    return value


@dataclass(frozen=True)
class ProviderPreset:
    """Catalog entry describing one provider preset."""

    provider_id: str
    display_name: str
    default_base_url: str
    default_model: str
    note: str = ""


@dataclass(frozen=True)
class ProviderProfile:
    """One saved model connection (no secrets)."""

    id: str
    provider_id: str
    display_name: str
    wire_api: str
    base_url: str
    model: str
    credential_ref: Optional[str] = None
    reasoning_mode: str = "auto"
    reasoning_effort: Optional[str] = None
    show_reasoning: bool = False

    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "id": self.id,
            "provider_id": self.provider_id,
            "display_name": self.display_name,
            "wire_api": self.wire_api,
            "base_url": self.base_url,
            "model": self.model,
            "reasoning_mode": self.reasoning_mode,
            "reasoning_effort": self.reasoning_effort,
            "show_reasoning": self.show_reasoning,
        }
        if self.credential_ref is not None:
            data["credential_ref"] = self.credential_ref
        return data


class ProviderCatalog:
    """Built-in provider presets (all OpenAI-compatible Chat Completions)."""

    _PRESETS: Dict[str, ProviderPreset] = {
        "openai": ProviderPreset(
            provider_id="openai",
            display_name="OpenAI",
            default_base_url="https://api.openai.com/v1",
            default_model="gpt-4o-mini",
            note="OpenAI Chat Completions API",
        ),
        "deepseek": ProviderPreset(
            provider_id="deepseek",
            display_name="DeepSeek",
            default_base_url="https://api.deepseek.com",
            default_model="deepseek-chat",
            note="DeepSeek OpenAI-compatible API",
        ),
        "custom": ProviderPreset(
            provider_id="custom",
            display_name="Custom",
            default_base_url="",
            default_model="",
            note="任意 OpenAI-compatible 网关 (Chat Completions)",
        ),
    }

    def preset(self, provider_id: str) -> ProviderPreset:
        try:
            return self._PRESETS[provider_id]
        except KeyError as exc:
            raise ProfileError(
                f"未知的 provider 类型：{provider_id}（可选：{', '.join(self._PRESETS)}）",
                field="provider_id",
            ) from exc

    def presets(self) -> List[ProviderPreset]:
        return [self._PRESETS[key] for key in ("openai", "deepseek", "custom")]


def validate_profile(
    *,
    profile_id: Optional[str],
    provider_id: str,
    display_name: str,
    base_url: str,
    model: str,
    wire_api: str,
    credential_ref: Optional[str],
    reasoning_mode: str = "auto",
    reasoning_effort: Optional[str] = None,
    show_reasoning: bool = False,
) -> ProviderProfile:
    """Validate and normalize profile fields (no filesystem access)."""
    if profile_id is not None:
        validate_profile_id(profile_id)
    if not isinstance(provider_id, str) or provider_id not in ProviderCatalog._PRESETS:
        raise ProfileError(
            "provider_id 必须是 openai、deepseek 或 custom", field="provider_id"
        )
    if not isinstance(display_name, str) or not display_name.strip():
        raise ProfileError("display_name 不能为空", field="display_name")
    display_name = display_name.strip()
    if len(display_name) > MAX_DISPLAY_NAME:
        raise ProfileError(
            f"display_name 过长（最多 {MAX_DISPLAY_NAME} 字符）", field="display_name"
        )
    if wire_api not in (WIRE_API_CHAT_COMPLETIONS, WIRE_API_RESPONSES):
        raise ProfileError(
            f"不支持的 wire_api：{wire_api!r}（可选 {', '.join(WIRE_APIS)}）",
            field="wire_api",
        )
    normalized_url = validate_provider_url(base_url)
    if not isinstance(model, str) or not model.strip():
        raise ProfileError("model 不能为空", field="model")
    model = model.strip()
    if len(model) > MAX_MODEL:
        raise ProfileError(f"model 过长（最多 {MAX_MODEL} 字符）", field="model")
    normalized_ref = None
    if credential_ref is not None:
        normalized_ref = validate_profile_id(credential_ref)
    if reasoning_mode not in ("auto", "off", "visible"):
        raise ProfileError(
            "reasoning_mode 必须是 auto/off/visible", field="reasoning_mode"
        )
    if reasoning_effort not in (None, "low", "medium", "high"):
        raise ProfileError(
            "reasoning_effort 必须是 low/medium/high 或空", field="reasoning_effort"
        )
    if reasoning_mode == "off" and reasoning_effort is not None:
        raise ProfileError(
            "reasoning_mode=off 时不能设置 reasoning_effort",
            field="reasoning_effort",
        )
    if provider_id == "deepseek" and wire_api == WIRE_API_RESPONSES:
        raise ProfileError(
            "DeepSeek profile 不支持 OpenAI Responses wire API",
            field="wire_api",
        )
    if (
        wire_api == WIRE_API_CHAT_COMPLETIONS
        and provider_id != "openai"
        and reasoning_effort is not None
    ):
        raise ProfileError(
            "该 Chat Completions provider 未声明 reasoning_effort 能力",
            field="reasoning_effort",
        )
    if (
        provider_id == "openai"
        and wire_api == WIRE_API_CHAT_COMPLETIONS
        and reasoning_mode == "visible"
    ):
        raise ProfileError(
            "OpenAI Chat Completions 不提供可展示的原始 reasoning；请使用 Responses API",
            field="reasoning_mode",
        )
    if not isinstance(show_reasoning, bool):
        raise ProfileError("show_reasoning 必须是布尔值", field="show_reasoning")
    return ProviderProfile(
        id=profile_id or "",
        provider_id=provider_id,
        display_name=display_name,
        wire_api=wire_api,
        base_url=normalized_url,
        model=model,
        credential_ref=normalized_ref,
        reasoning_mode=reasoning_mode,
        reasoning_effort=reasoning_effort,
        show_reasoning=show_reasoning,
    )


@dataclass
class ConfigData:
    """In-memory view of ``config.json``."""

    profiles: Dict[str, ProviderProfile] = field(default_factory=dict)
    active_profile: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": CONFIG_VERSION,
            "active_profile": self.active_profile,
            "profiles": {
                pid: profile.to_dict() for pid, profile in self.profiles.items()
            },
        }


class ProfileStore:
    """User-level profile store with strict schema and atomic persistence."""

    def __init__(self, home: Path) -> None:
        self._home = Path(home)
        self._path = self._home / CONFIG_FILENAME
        self._data = ConfigData()
        self._loaded = False

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> ConfigData:
        """Load (once) and validate the config; corrupt files are never overwritten."""
        if self._loaded:
            return self._data
        if self._path.exists():
            try:
                raw = load_json(self._path)
            except StorageError as exc:
                raise ProfileError(
                    "config.json 损坏，已拒绝加载与写入（原文件保留）："
                    + str(self._path),
                    field="config",
                    code="config_corrupt",
                ) from exc
            self._data = self._parse(raw)
        self._loaded = True
        return self._data

    def reload(self) -> ConfigData:
        self._loaded = False
        return self.load()

    @staticmethod
    def _parse(raw: Any) -> ConfigData:
        if not isinstance(raw, dict):
            raise ProfileError(
                "config 根必须是 JSON object",
                field="config",
                code="config_corrupt",
            )
        top_unknown = set(raw) - {"version", "active_profile", "profiles"}
        if top_unknown:
            raise ProfileError(
                f"config 含未知字段：{', '.join(sorted(top_unknown))}",
                field="config",
            )
        version = raw.get("version")
        if type(version) is not int or version != CONFIG_VERSION:
            raise ProfileError(
                f"不支持的 config 版本：{version!r}（当前支持 version=1）",
                field="version",
                code="config_corrupt",
            )
        profiles_raw = raw.get("profiles", {})
        if not isinstance(profiles_raw, dict):
            raise ProfileError("profiles 必须是对象", field="profiles")
        profiles: Dict[str, ProviderProfile] = {}
        allowed_profile_keys = {
            "id",
            "provider_id",
            "display_name",
            "wire_api",
            "base_url",
            "model",
            "credential_ref",
            "reasoning_mode",
            "reasoning_effort",
            "show_reasoning",
        }
        for pid, item in profiles_raw.items():
            try:
                validate_profile_id(pid)
            except ProfileError as exc:
                raise ProfileError(
                    f"profile ID 非法：{pid!r}", field=f"profiles.{pid}"
                ) from exc
            if not isinstance(item, dict):
                raise ProfileError(
                    f"profile {pid!r} 必须是对象", field=f"profiles.{pid}"
                )
            extra = set(item) - allowed_profile_keys
            if extra:
                raise ProfileError(
                    f"profile {pid!r} 含未知字段：{', '.join(sorted(extra))}",
                    field=f"profiles.{pid}",
                )
            embedded_id = item.get("id")
            if embedded_id is not None and embedded_id != pid:
                raise ProfileError(
                    f"profile {pid!r} 内嵌 id 与键不一致：{embedded_id!r}",
                    field=f"profiles.{pid}",
                )
            missing = [
                key
                for key in (
                    "provider_id",
                    "display_name",
                    "wire_api",
                    "base_url",
                    "model",
                )
                if key not in item
            ]
            if missing:
                raise ProfileError(
                    f"profile {pid!r} 缺少字段：{', '.join(missing)}",
                    field=f"profiles.{pid}",
                )
            profiles[pid] = validate_profile(
                profile_id=pid,
                provider_id=item.get("provider_id"),
                display_name=item.get("display_name"),
                base_url=item.get("base_url"),
                model=item.get("model"),
                wire_api=item.get("wire_api"),
                credential_ref=item.get("credential_ref"),
                reasoning_mode=item.get("reasoning_mode", "auto"),
                reasoning_effort=item.get("reasoning_effort"),
                show_reasoning=item.get("show_reasoning", False),
            )
        active = raw.get("active_profile")
        if active is not None:
            validate_profile_id(active)
            if active not in profiles:
                raise ProfileError(
                    f"active_profile 指向不存在的 profile：{active!r}",
                    field="active_profile",
                )
        return ConfigData(profiles=profiles, active_profile=active)

    def _commit(self, candidate: ConfigData) -> None:
        """Persist a candidate and only then swap it into memory.

        Any StorageError propagates before ``self._data`` changes, so the
        in-memory view always matches the on-disk file.
        """
        atomic_write_json(self._path, candidate.to_dict())
        self._data = candidate
        self._loaded = True

    # ------------------------------------------------------------- CRUD

    def list_profiles(self) -> List[ProviderProfile]:
        self.load()
        return [self._data.profiles[key] for key in sorted(self._data.profiles)]

    def get(self, profile_id: str) -> Optional[ProviderProfile]:
        self.load()
        return self._data.profiles.get(profile_id)

    def create(self, input_profile: ProviderProfile) -> ProviderProfile:
        self.load()
        if input_profile.id in self._data.profiles:
            raise ProfileError(f"profile 已存在：{input_profile.id}", field="id")
        profiles = dict(self._data.profiles)
        profiles[input_profile.id] = input_profile
        active = self._data.active_profile
        if active is None:
            active = input_profile.id
        self._commit(ConfigData(profiles=profiles, active_profile=active))
        return input_profile

    def update(
        self, profile_id: str, input_profile: ProviderProfile
    ) -> ProviderProfile:
        self.load()
        if profile_id not in self._data.profiles:
            raise ProfileError(f"profile 不存在：{profile_id}", field="id")
        # The ID is fixed: the incoming profile id must match the stored one.
        if input_profile.id != profile_id:
            raise ProfileError("profile ID 创建后不可修改", field="id")
        profiles = dict(self._data.profiles)
        profiles[profile_id] = input_profile
        self._commit(
            ConfigData(profiles=profiles, active_profile=self._data.active_profile)
        )
        return input_profile

    def delete(self, profile_id: str) -> None:
        self.load()
        if profile_id not in self._data.profiles:
            raise ProfileError(f"profile 不存在：{profile_id}", field="id")
        profiles = dict(self._data.profiles)
        del profiles[profile_id]
        active = self._data.active_profile
        if active == profile_id:
            active = next(iter(sorted(profiles)), None)
        self._commit(ConfigData(profiles=profiles, active_profile=active))

    def activate(self, profile_id: str) -> ProviderProfile:
        self.load()
        if profile_id not in self._data.profiles:
            raise ProfileError(f"profile 不存在：{profile_id}", field="id")
        self._commit(
            ConfigData(profiles=dict(self._data.profiles), active_profile=profile_id)
        )
        return self._data.profiles[profile_id]
