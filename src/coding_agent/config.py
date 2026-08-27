"""Configuration loading and validation.

API keys are read from the environment only; CLI flags may override model and
base URL but never carry the key.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .errors import ConfigError

DEFAULT_MAX_STEPS = 20
DEFAULT_CHAR_BUDGET = 120_000
MIN_MAX_STEPS = 1
MAX_MAX_STEPS = 50


@dataclass(frozen=True)
class Config:
    workspace: Path
    api_key: str
    model: str
    base_url: Optional[str]
    max_steps: int = DEFAULT_MAX_STEPS
    char_budget: int = DEFAULT_CHAR_BUDGET


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

    missing = []
    if not key:
        missing.append("OPENAI_API_KEY")
    if not resolved_model:
        missing.append("OPENAI_MODEL（或 --model）")
    if missing:
        raise ConfigError("缺少必需配置：" + "、".join(missing))

    steps = DEFAULT_MAX_STEPS if max_steps is None else max_steps
    if (
        not isinstance(steps, int)
        or isinstance(steps, bool)
        or not (MIN_MAX_STEPS <= steps <= MAX_MAX_STEPS)
    ):
        raise ConfigError(
            f"--max-steps 必须是 {MIN_MAX_STEPS}-{MAX_MAX_STEPS} 之间的整数，收到 {steps!r}"
        )

    workspace_path = Path(workspace).expanduser()
    try:
        workspace_path = workspace_path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ConfigError(f"工作区不存在或不可访问：{workspace_path}") from exc
    if not workspace_path.is_dir():
        raise ConfigError(f"工作区不是目录：{workspace_path}")

    return Config(
        workspace=workspace_path,
        api_key=key,
        model=resolved_model,
        base_url=resolved_base_url,
        max_steps=steps,
        char_budget=DEFAULT_CHAR_BUDGET,
    )
