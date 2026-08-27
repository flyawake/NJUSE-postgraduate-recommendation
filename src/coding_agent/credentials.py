"""Credential providers and service (write-only, environment-first).

Credential values are referenced by a ``CredentialRef`` (a short identifier).
Resolution priority for every ref: process environment first, then the
user-level ``credentials.json`` under ``CODING_AGENT_HOME``. When the
environment provides a ref, its descriptor is read-only and GUI set/unset
requests are rejected so a local value can never silently shadow an env var.

There is intentionally **no** API to read a stored secret back to the caller
of this module: descriptors only expose ``configured/source/writable`` and
the resolved value goes straight into a model client. The credentials file
is plaintext JSON inside the user directory; on Windows encryption must not
be claimed (see README).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from .errors import ConfigError
from .storage import StorageError, atomic_write_json, ensure_home, load_json

CREDENTIALS_FILENAME = "credentials.json"
CREDENTIALS_VERSION = 1
CREDENTIAL_ENV_PREFIX = "CODING_AGENT_CRED_"
_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
MAX_SECRET_CHARS = 4096

#: Environment variables never allowed as credential sources (keys only).
BLOCKED_ENV_KEYS = frozenset(
    {
        "OPENAI_API_KEY",
        "OPENAI_MODEL",
        "OPENAI_BASE_URL",
    }
)


class CredentialError(ConfigError):
    """A credential operation failed; carries a stable machine-readable code."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "credential_invalid",
        field: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.field = field


@dataclass(frozen=True)
class CredentialInfo:
    """Public descriptor of a credential ref; never includes the secret."""

    configured: bool
    source: Optional[str]  # "env" | "local_file"
    writable: bool


@dataclass(frozen=True)
class ResolvedCredential:
    value: str
    source: str  # "env" | "local_file"


def validate_credential_ref(ref: str) -> str:
    if not isinstance(ref, str) or not _REF_RE.match(ref):
        raise CredentialError(
            "credential_ref 只能包含字母、数字、下划线与连字符，且以字母或数字开头（最长 64 字符）",
            code="credential_invalid",
            field="credential_ref",
        )
    return ref


def env_var_for_ref(ref: str) -> str:
    """Canonical environment variable name for a ref.

    The legacy ``openai`` ref maps to ``OPENAI_API_KEY`` so existing
    ``OPENAI_*`` deployments keep working; every other ref uses
    ``CODING_AGENT_CRED_<REF>``.
    """
    if ref == "openai":
        return "OPENAI_API_KEY"
    return CREDENTIAL_ENV_PREFIX + re.sub(r"[^A-Za-z0-9]", "_", ref).upper()


class CredentialService:
    """Environment-first credential resolution and write-only storage."""

    def __init__(
        self,
        home: Path,
        env: Optional[Mapping[str, str]] = None,
    ) -> None:
        self._home = Path(home)
        self._path = self._home / CREDENTIALS_FILENAME
        self._env: Mapping[str, str] = os.environ if env is None else env

    @property
    def path(self) -> Path:
        return self._path

    # ------------------------------------------------------------- resolve

    def info(self, ref: str) -> CredentialInfo:
        validate_credential_ref(ref)
        env_key = env_var_for_ref(ref)
        if self._env.get(env_key, "").strip():
            return CredentialInfo(configured=True, source="env", writable=False)
        if self._read_local().get(ref):
            return CredentialInfo(configured=True, source="local_file", writable=True)
        return CredentialInfo(configured=False, source=None, writable=True)

    def resolve(self, ref: str) -> ResolvedCredential:
        validate_credential_ref(ref)
        env_key = env_var_for_ref(ref)
        value = self._env.get(env_key, "").strip()
        if value:
            return ResolvedCredential(value=value, source="env")
        local = self._read_local().get(ref)
        if local:
            return ResolvedCredential(value=local, source="local_file")
        raise CredentialError(
            f"凭据未配置：{ref}（请设置 {env_key} 或在设置页写入）",
            code="credential_not_configured",
            field="credential_ref",
        )

    # ------------------------------------------------------------- mutate

    def set(self, ref: str, secret: str) -> CredentialInfo:
        validate_credential_ref(ref)
        if not isinstance(secret, str) or not secret.strip():
            raise CredentialError(
                "凭据不能为空", code="credential_invalid", field="secret"
            )
        value = secret.strip()
        if len(value) > MAX_SECRET_CHARS:
            raise CredentialError(
                f"凭据过长（最多 {MAX_SECRET_CHARS} 字符）",
                code="credential_invalid",
                field="secret",
            )
        self._ensure_writable(ref)
        data = self._read_local()
        data[ref] = value
        ensure_home(self._home)
        atomic_write_json(self._path, self._serialize(data))
        return CredentialInfo(configured=True, source="local_file", writable=True)

    def unset(self, ref: str) -> CredentialInfo:
        validate_credential_ref(ref)
        self._ensure_writable(ref)
        data = self._read_local()
        if ref in data:
            del data[ref]
        if data:
            ensure_home(self._home)
            atomic_write_json(self._path, self._serialize(data))
        elif self._path.exists():
            ensure_home(self._home)
            atomic_write_json(self._path, self._serialize({}))
        return self.info(ref)

    def local_refs(self) -> list[str]:
        return list(self._read_local().keys())

    # ------------------------------------------------------------- private

    def _ensure_writable(self, ref: str) -> None:
        info = self.info(ref)
        if info.source == "env":
            raise CredentialError(
                f"凭据 {ref} 由环境变量提供（{env_var_for_ref(ref)}），"
                "GUI 不可写入或清除；请直接修改环境变量",
                code="credential_env_readonly",
                field="credential_ref",
            )

    def _read_local(self) -> Dict[str, str]:
        if not self._path.exists():
            return {}
        try:
            raw = load_json(self._path)
        except StorageError as exc:
            raise CredentialError(
                "凭据文件损坏，拒绝写入以避免覆盖：请修复或删除 " + str(self._path),
                code="credential_file_corrupt",
            ) from exc
        version = raw.get("version")
        if version != CREDENTIALS_VERSION:
            raise CredentialError(
                f"不支持的凭据文件版本：{version!r}（当前支持 version=1）",
                code="credential_file_corrupt",
            )
        credentials = raw.get("credentials", {})
        if not isinstance(credentials, dict):
            raise CredentialError(
                "credentials 必须是对象", code="credential_file_corrupt"
            )
        return {str(key): str(value) for key, value in credentials.items() if value}

    @staticmethod
    def _serialize(credentials: Dict[str, str]) -> Dict[str, Any]:
        return {
            "version": CREDENTIALS_VERSION,
            "credentials": dict(sorted(credentials.items())),
        }
