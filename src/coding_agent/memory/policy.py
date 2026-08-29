"""Server-side secret and safety policy for memory writes.

Memory is untrusted reference data. This policy fail-closes on likely
credentials, private keys, ``.env`` assignments and high-entropy tokens before
anything reaches SQLite, the API response, logs or the model projection.
"""

from __future__ import annotations

import re
from typing import Optional

from .models import MEMORY_MAX_ENTRY_CHARS

_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN(?:[A-Za-z0-9 ]*)?PRIVATE KEY-----", re.IGNORECASE
)
_AWS_ACCESS_KEY_RE = re.compile(r"\bAKIA[0-9A-Z]{16}\b")
_OPENAI_KEY_RE = re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")
_GITHUB_TOKEN_RE = re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")
_GOOGLE_API_KEY_RE = re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")
_SLACK_TOKEN_RE = re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")
_BEARER_RE = re.compile(r"\bbearer\s+[A-Za-z0-9._~+/=-]{20,}", re.IGNORECASE)
_CREDENTIAL_ASSIGNMENT_RE = re.compile(
    r"(?:api[_-]?key|secret|token|password|passwd|private[_-]?key|"
    r"access[_-]?key|credential)\s*[:=]\s*[\"']?[A-Za-z0-9_./+=~-]{6,}",
    re.IGNORECASE,
)
_ENV_ASSIGNMENT_RE = re.compile(
    r"^\s*(?:[A-Z][A-Z0-9_]*(?:SECRET|KEY|TOKEN|PASSWORD|PRIVATE|CREDENTIAL)[A-Z0-9_]*)"
    r"\s*=\s*\S+",
    re.MULTILINE | re.IGNORECASE,
)
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]+")
_HIGH_ENTROPY_RE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z0-9+/=_-]{40,}(?![A-Za-z0-9])")


class MemoryPolicyError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class MemoryPolicy:
    """Evaluate whether a memory payload is safe to persist."""

    def check(self, content: str, *, source_excerpt: Optional[str] = None) -> None:
        if not content or not content.strip():
            raise MemoryPolicyError("memory_content_empty", "记忆内容不能为空")
        if len(content) > MEMORY_MAX_ENTRY_CHARS:
            raise MemoryPolicyError(
                "memory_too_long",
                f"记忆内容超过 {MEMORY_MAX_ENTRY_CHARS} 字符上限",
            )
        if content.count("\n") > 80 or (
            len(content) > 2_000 and content.count("\n") > 20
        ):
            raise MemoryPolicyError(
                "memory_log_too_long",
                "记忆内容疑似包含大段日志或命令输出，已拒绝",
            )
        if _CONTROL_RE.search(content):
            raise MemoryPolicyError(
                "memory_contains_control", "记忆内容包含控制字符，已拒绝"
            )
        self._reject_secret(content, "content")
        if source_excerpt:
            if len(source_excerpt) > 500:
                raise MemoryPolicyError(
                    "memory_source_too_long", "记忆来源摘录超过 500 字符上限"
                )
            self._reject_secret(source_excerpt, "source_excerpt")

    def check_extraction_text(self, text: str) -> None:
        """Fail closed before user/final text is sent to the optional extractor."""
        if _CONTROL_RE.search(text):
            raise MemoryPolicyError(
                "memory_contains_control", "候选提取输入包含控制字符，已跳过"
            )
        self._reject_secret(text, "extraction_input")

    @staticmethod
    def _reject_secret(text: str, field: str) -> None:
        patterns = [
            (_PRIVATE_KEY_RE, "private_key"),
            (_AWS_ACCESS_KEY_RE, "aws_access_key"),
            (_OPENAI_KEY_RE, "openai_api_key"),
            (_GITHUB_TOKEN_RE, "github_token"),
            (_GOOGLE_API_KEY_RE, "google_api_key"),
            (_SLACK_TOKEN_RE, "slack_token"),
            (_BEARER_RE, "bearer_token"),
            (_CREDENTIAL_ASSIGNMENT_RE, "credential_assignment"),
            (_ENV_ASSIGNMENT_RE, "env_assignment"),
            (_HIGH_ENTROPY_RE, "high_entropy_token"),
        ]
        for regex, code in patterns:
            if regex.search(text):
                raise MemoryPolicyError(
                    "memory_contains_secret", "记忆内容疑似包含凭据或密钥，已拒绝"
                ) from None

    @staticmethod
    def fail_closed_reason(exc: MemoryPolicyError) -> str:
        return exc.code
