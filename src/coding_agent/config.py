"""Configuration loading and validation.

API keys are read from the environment only; CLI flags may override model and
base URL but never carry the key.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Optional

from .credentials import ResolvedCredential
from .errors import ConfigError
from .provider_config import (
    WIRE_API_CHAT_COMPLETIONS,
    WIRE_API_RESPONSES,
    ProviderProfile,
)

DEFAULT_MAX_STEPS = 20
DEFAULT_CHAR_BUDGET = 258_000
MIN_MAX_STEPS = 1
MAX_MAX_STEPS = 200
DEFAULT_CONTEXT_WINDOW_TOKENS = 128_000
MIN_CONTEXT_WINDOW_TOKENS = 16_000
MAX_CONTEXT_WINDOW_TOKENS = 4_000_000


@dataclass(frozen=True)
class Config:
    workspace: Path
    api_key: str
    model: str
    base_url: Optional[str]
    max_steps: int = DEFAULT_MAX_STEPS
    char_budget: int = DEFAULT_CHAR_BUDGET
    wire_api: str = WIRE_API_CHAT_COMPLETIONS
    context_window_tokens: int = DEFAULT_CONTEXT_WINDOW_TOKENS


@dataclass(frozen=True)
class ResolvedModelConnection:
    """A fully resolved model connection ready for ModelClientFactory.

    ``source`` is a human-readable descriptor for diagnostics (e.g.
    ``profile:deepseek`` or ``legacy-env``); it never contains secrets.
    """

    api_key: str
    model: str
    base_url: Optional[str]
    wire_api: str
    source: str
    profile_id: Optional[str] = None
    profile_display_name: Optional[str] = None
    provider_id: str = "openai"
    reasoning_mode: str = "auto"
    reasoning_effort: Optional[str] = None
    context_window_tokens: int = DEFAULT_CONTEXT_WINDOW_TOKENS


def _profile_connection(
    profile: ProviderProfile,
    credential: ResolvedCredential,
) -> ResolvedModelConnection:
    return ResolvedModelConnection(
        api_key=credential.value,
        model=profile.model,
        base_url=profile.base_url,
        wire_api=profile.wire_api,
        source=f"profile:{profile.provider_id}:{credential.source}",
        profile_id=profile.id,
        profile_display_name=profile.display_name,
        provider_id=profile.provider_id,
        reasoning_mode=profile.reasoning_mode,
        reasoning_effort=profile.reasoning_effort,
        context_window_tokens=profile.context_window_tokens,
    )


def _legacy_connection(env: Dict[str, str]) -> ResolvedModelConnection:
    key = (env.get("OPENAI_API_KEY") or "").strip()
    model = (env.get("OPENAI_MODEL") or "").strip()
    base_url = (env.get("OPENAI_BASE_URL") or "").strip() or None
    if not key:
        raise ConfigError("OPENAI_API_KEY 未设置，且没有可用的模型 profile")
    if not model:
        raise ConfigError("OPENAI_MODEL 未设置，且没有可用的模型 profile")
    return ResolvedModelConnection(
        api_key=key,
        model=model,
        base_url=base_url,
        wire_api="openai_chat_completions",
        source="legacy-env",
        provider_id="openai",
        context_window_tokens=_resolve_context_window(
            env.get("OPENAI_CONTEXT_WINDOW_TOKENS")
        ),
    )


def resolve_connection(
    *,
    profiles: Dict[str, ProviderProfile],
    active_profile: Optional[str],
    explicit_profile: Optional[str],
    env: Dict[str, str],
    credential_resolver: Callable[[str], ResolvedCredential],
) -> ResolvedModelConnection:
    """Resolve the model connection for a run.

    Priority: explicit profile > active profile > legacy ``OPENAI_*`` env.
    An explicitly selected or active profile that cannot be used fails with a
    ConfigError; the loop never silently switches to another provider.
    """
    selected: Optional[ProviderProfile] = None
    if explicit_profile is not None:
        if explicit_profile not in profiles:
            raise ConfigError(f"显式指定的 profile 不存在：{explicit_profile}")
        selected = profiles[explicit_profile]
    elif active_profile is not None:
        if active_profile not in profiles:
            raise ConfigError(f"当前激活的 profile 不存在：{active_profile}")
        selected = profiles[active_profile]

    if selected is not None:
        if selected.wire_api not in (WIRE_API_CHAT_COMPLETIONS, WIRE_API_RESPONSES):
            raise ConfigError(
                f"profile {selected.id} 使用不支持的 wire_api：{selected.wire_api}"
            )
        if selected.credential_ref:
            credential = credential_resolver(selected.credential_ref)
        else:
            raise ConfigError(
                f"profile {selected.id} 未配置凭据，请在设置页写入或设置环境变量"
            )
        return _profile_connection(selected, credential)

    return _legacy_connection(env)


def _resolve_workspace(workspace: str | Path) -> Path:
    workspace_path = Path(workspace).expanduser()
    try:
        workspace_path = workspace_path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ConfigError(f"工作区不存在或不可访问：{workspace_path}") from exc
    if not workspace_path.is_dir():
        raise ConfigError(f"工作区不是目录：{workspace_path}")
    return workspace_path


def _resolve_steps(max_steps: Optional[int]) -> int:
    steps = DEFAULT_MAX_STEPS if max_steps is None else max_steps
    if (
        not isinstance(steps, int)
        or isinstance(steps, bool)
        or not (MIN_MAX_STEPS <= steps <= MAX_MAX_STEPS)
    ):
        raise ConfigError(
            f"--max-steps 必须是 {MIN_MAX_STEPS}-{MAX_MAX_STEPS} 之间的整数，收到 {steps!r}"
        )
    return steps


def _resolve_context_window(value: Optional[str | int]) -> int:
    if value is None or value == "":
        return DEFAULT_CONTEXT_WINDOW_TOKENS
    if isinstance(value, bool):
        raise ConfigError("context window 必须是整数")
    try:
        tokens = int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError("OPENAI_CONTEXT_WINDOW_TOKENS 必须是整数") from exc
    if not (MIN_CONTEXT_WINDOW_TOKENS <= tokens <= MAX_CONTEXT_WINDOW_TOKENS):
        raise ConfigError(
            f"context window 必须在 {MIN_CONTEXT_WINDOW_TOKENS}-{MAX_CONTEXT_WINDOW_TOKENS} 之间"
        )
    return tokens


def load_config_from_connection(
    workspace: str | Path,
    connection: ResolvedModelConnection,
    *,
    model: Optional[str] = None,
    base_url: Optional[str] = None,
    max_steps: Optional[int] = None,
) -> Config:
    """Build a Config from an already-resolved connection (GUI/profile path).

    ``model``/``base_url`` override only this run; they are never written
    back to the profile store.
    """
    resolved_model = (model or connection.model).strip()
    if not resolved_model:
        raise ConfigError("模型名称为空（请检查 profile 的 model 字段）")
    from .provider_config import validate_provider_url

    resolved_base_url = connection.base_url
    if base_url:
        resolved_base_url = validate_provider_url(base_url)
    elif resolved_base_url is not None:
        # Legacy and profile-provided URLs share the exact same validator.
        resolved_base_url = validate_provider_url(resolved_base_url)
    return Config(
        workspace=_resolve_workspace(workspace),
        api_key=connection.api_key,
        model=resolved_model,
        base_url=resolved_base_url,
        max_steps=_resolve_steps(max_steps),
        char_budget=DEFAULT_CHAR_BUDGET,
        wire_api=connection.wire_api,
        context_window_tokens=connection.context_window_tokens,
    )


def load_config(
    workspace: str | Path,
    *,
    model: Optional[str] = None,
    base_url: Optional[str] = None,
    max_steps: Optional[int] = None,
    api_key: Optional[str] = None,
    env: Optional[dict[str, str]] = None,
) -> Config:
    """Build and validate a Config.

    ``api_key`` and ``env`` are test seams; production always reads the key
    from the process environment.
    """
    environment: dict[str, str] = os.environ if env is None else env
    key = (environment.get("OPENAI_API_KEY") or "").strip()
    if api_key is not None:
        key = api_key.strip()
    resolved_model = (model or environment.get("OPENAI_MODEL") or "").strip()
    resolved_base_url = (
        base_url or environment.get("OPENAI_BASE_URL") or ""
    ).strip() or None
    if resolved_base_url is not None:
        from .provider_config import validate_provider_url

        resolved_base_url = validate_provider_url(resolved_base_url)

    missing = []
    if not key:
        missing.append("OPENAI_API_KEY")
    if not resolved_model:
        missing.append("OPENAI_MODEL（或 --model）")
    if missing:
        raise ConfigError("缺少必需配置：" + "、".join(missing))

    steps = _resolve_steps(max_steps)
    workspace_path = _resolve_workspace(workspace)

    return Config(
        workspace=workspace_path,
        api_key=key,
        model=resolved_model,
        base_url=resolved_base_url,
        max_steps=steps,
        char_budget=DEFAULT_CHAR_BUDGET,
        context_window_tokens=_resolve_context_window(
            environment.get("OPENAI_CONTEXT_WINDOW_TOKENS")
        ),
    )
