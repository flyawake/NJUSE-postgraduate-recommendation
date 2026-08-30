"""Project-wide exception types.

These exceptions separate config failures, model request failures and
context budget failures from the tool-level errors defined in
:mod:`coding_agent.tools.base`. Tool failures are model-visible structured
results; the exceptions below are run-level failures recorded in RunResult.
"""

from __future__ import annotations


class CodingAgentError(Exception):
    """Base class for run-level errors of the coding agent."""


class ConfigError(CodingAgentError):
    """Configuration is missing or invalid; fail before any model call."""


class ModelRequestError(CodingAgentError):
    """A single model request attempt failed.

    ``retryable`` decides whether AgentLoop may retry the same frozen request.
    The message must never contain credentials.
    """

    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.reason = message


class ContextOverflowError(CodingAgentError):
    """A protected projection exceeds a text, token, or request-byte budget."""

    def __init__(self, count: int, budget: int, *, metric: str = "chars") -> None:
        self.count = count
        self.budget = budget
        self.metric = metric
        # Backwards-compatible attributes for callers/tests that inspect the
        # original character-budget exception.
        self.char_count = count if metric == "chars" else 0
        super().__init__(
            f"protected context is {count} {metric}, exceeding budget {budget}"
        )
