"""Completion policy: one bounded verification reminder per run."""

from __future__ import annotations

from dataclasses import dataclass

from .models import VerificationStatus


@dataclass(frozen=True)
class CompletionDecision:
    complete: bool
    message: str = ""


DEFERRAL_MESSAGE = (
    "完成前验证提醒：本次运行已修改文件，但尚未观察到一次成功的 "
    'purpose="verify" 验证命令。请在给出最终答复前运行相关测试或检查，'
    "并依据工具结果说明验证状态；如果确实无法验证，请在最终答复中明确"
    "说明原因。"
)


class CompletionPolicy:
    """Decide whether a final answer may end the run.

    - No file changes: complete with NOT_APPLICABLE.
    - Changes already verified: complete with VERIFIED.
    - Changes unverified: defer exactly once while steps remain; afterwards
      complete with the truthful FAILED/NOT_RUN status kept in RunResult.
    """

    def decide(
        self,
        *,
        has_changes: bool,
        verification: VerificationStatus,
        step_count: int,
        max_steps: int,
        deferred: bool,
    ) -> CompletionDecision:
        if not has_changes or verification is VerificationStatus.VERIFIED:
            return CompletionDecision(complete=True)
        if deferred or step_count >= max_steps:
            return CompletionDecision(complete=True)
        return CompletionDecision(complete=False, message=DEFERRAL_MESSAGE)
