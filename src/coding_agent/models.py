"""Internal data model shared by the loop, context and CLI layers.

Vendor SDK objects must never appear here; production response normalization
lives in :mod:`coding_agent.model_client`.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, Optional, Tuple


class LoopPhase(str, Enum):
    """Explicit AgentLoop state machine phases."""

    INITIALIZING = "INITIALIZING"
    READY = "READY"
    REQUESTING_MODEL = "REQUESTING_MODEL"
    HANDLING_RESPONSE = "HANDLING_RESPONSE"
    EXECUTING_TOOLS = "EXECUTING_TOOLS"
    CHECKING_COMPLETION = "CHECKING_COMPLETION"
    TERMINAL = "TERMINAL"
    INTERRUPTED = "INTERRUPTED"


class RunStatus(str, Enum):
    SUCCESS = "SUCCESS"
    ERROR = "ERROR"
    INTERRUPTED = "INTERRUPTED"


class StopReason(str, Enum):
    FINAL_ANSWER = "FINAL_ANSWER"
    MAX_STEPS = "MAX_STEPS"
    MODEL_ERROR = "MODEL_ERROR"
    PROTOCOL_ERROR = "PROTOCOL_ERROR"
    CONTEXT_OVERFLOW = "CONTEXT_OVERFLOW"
    TOOL_FAILURE_LIMIT = "TOOL_FAILURE_LIMIT"
    REPEATED_TOOL_CALL = "REPEATED_TOOL_CALL"
    INTERRUPTED = "INTERRUPTED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class VerificationStatus(str, Enum):
    NOT_APPLICABLE = "NOT_APPLICABLE"
    VERIFIED = "VERIFIED"
    FAILED = "FAILED"
    NOT_RUN = "NOT_RUN"


@dataclass(frozen=True)
class ToolCall:
    """One assistant tool call as received from the model.

    ``arguments_raw`` keeps the exact provider JSON text; parsing happens in
    the ToolExecutor pipeline so invalid JSON becomes a structured tool result
    instead of a crash.
    """

    id: str
    name: str
    arguments_raw: str


@dataclass(frozen=True)
class ProviderContinuation:
    """Opaque, adapter-scoped state required for a provider sub-request.

    It is canonical internal data, never public display text. The neutral
    shape prevents SDK objects from crossing the adapter boundary.
    """

    wire_api: str
    item_id: str
    encrypted_content: str
    summary: Tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class AssistantTurn:
    """Normalized assistant response for one model request."""

    text: str
    tool_calls: Tuple[ToolCall, ...] = field(default_factory=tuple)
    reasoning: Optional[str] = None
    continuations: Tuple[ProviderContinuation, ...] = field(default_factory=tuple)


class MessageRole(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass(frozen=True)
class SystemMessage:
    content: str


@dataclass(frozen=True)
class UserMessage:
    content: str
    source: str = "user"  # "user" | "completion_policy" | "loop_guard"


@dataclass(frozen=True)
class AssistantMessage:
    text: str
    tool_calls: Tuple[ToolCall, ...] = field(default_factory=tuple)
    reasoning: Optional[str] = None
    continuations: Tuple[ProviderContinuation, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ToolMessage:
    """Canonical tool result.

    ``content`` is the deterministic JSON string rendered to the model. The
    remaining fields are projection metadata used by ContextManager to decide
    what may be compacted; they are immutable like everything else in the
    canonical history.
    """

    tool_call_id: str
    content: str
    tool_name: str
    ok: bool
    resource_key: str
    is_read_success: bool = False
    file_path: Optional[str] = None


CanonicalMessage = SystemMessage | UserMessage | AssistantMessage | ToolMessage


@dataclass(frozen=True)
class AgentEvent:
    """Structured, redacted lifecycle event with a monotonic run sequence."""

    sequence: int
    run_id: str
    type: str
    step: int
    phase: LoopPhase
    payload: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["phase"] = self.phase.value
        return data


@dataclass(frozen=True)
class RunResult:
    """The single structured outcome returned by AgentLoop."""

    run_id: str
    status: RunStatus
    stop_reason: StopReason
    final_text: Optional[str] = None
    step_count: int = 0
    provider_attempt_count: int = 0
    tool_call_count: int = 0
    verification_status: VerificationStatus = VerificationStatus.NOT_APPLICABLE
    mutated_paths: Tuple[str, ...] = ()
    last_verification: Optional[Dict[str, Any]] = None
    final_phase: LoopPhase = LoopPhase.INITIALIZING
    details: Dict[str, Any] = field(default_factory=dict)
    context_char_count: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        data["stop_reason"] = self.stop_reason.value
        data["verification_status"] = self.verification_status.value
        data["final_phase"] = self.final_phase.value
        data["mutated_paths"] = list(self.mutated_paths)
        return data
