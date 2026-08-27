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
    """Even the protected projection exceeds the configured character budget."""

    def __init__(self, char_count: int, budget: int) -> None:
        self.char_count = char_count
        self.budget = budget
        super().__init__(
            f"protected context is {char_count} chars, exceeding budget {budget}"
        )
