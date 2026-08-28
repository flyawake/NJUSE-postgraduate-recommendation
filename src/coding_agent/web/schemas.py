"""Public API DTOs (whitelisted, secret-free).

Everything crossing the HTTP boundary is defined here: fixed field names,
stable error codes, and per-event-type payload whitelists. SDK types, raw
AgentLoop payloads and exceptions never leak into these models. Field names
are snake_case and match the OpenAPI schema used by the generated frontend
types.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from ..public_redaction import PUBLIC_TOOL_TARGET_MAX_CHARS

MAX_TASK_CHARS = 100_000
MAX_WORKSPACE_CHARS = 1024

# ------------------------------------------------------------- input models


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RunStartRequest(StrictModel):
    workspace: str = Field(min_length=1, max_length=MAX_WORKSPACE_CHARS)
    task: str = Field(min_length=1, max_length=MAX_TASK_CHARS)
    profile_id: Optional[str] = None


class WorkspaceValidateRequest(StrictModel):
    path: str = Field(min_length=1, max_length=MAX_WORKSPACE_CHARS)


class ProfileInput(StrictModel):
    provider_id: str
    display_name: str
    base_url: str
    model: str
    credential_ref: Optional[str] = None


class CredentialSetRequest(StrictModel):
    secret: str = Field(min_length=1, max_length=4096)


# ------------------------------------------------------------- output models


class ErrorDetail(BaseModel):
    code: str
    message: str
    field: Optional[str] = None


class ErrorResponse(BaseModel):
    error: ErrorDetail


class WorkspaceValidateResponse(BaseModel):
    valid: bool
    resolved_path: Optional[str] = None
    error: Optional[ErrorDetail] = None


class WorkspacePickResponse(BaseModel):
    """Result of the OS-native folder picker hosted by the local server."""

    cancelled: bool = False
    path: Optional[str] = None
    error: Optional[ErrorDetail] = None


class CredentialInfoDTO(BaseModel):
    configured: bool
    source: Optional[str]  # "env" | "local_file" | null
    writable: bool


class ProfileDTO(BaseModel):
    id: str
    provider_id: str
    display_name: str
    wire_api: str
    base_url: str
    model: str
    credential_ref: Optional[str] = None
    credential: CredentialInfoDTO


class ProviderPresetDTO(BaseModel):
    provider_id: str
    display_name: str
    default_base_url: str
    default_model: str
    note: str = ""


class ToolEventDTO(BaseModel):
    """One public event with a monotonic id and a whitelisted payload."""

    id: int
    kind: str
    step: int
    phase: str
    # Present only for tool_started. It is prepared from normalized arguments
    # before the display summary is truncated, never reconstructed by the UI.
    target: Optional[str] = Field(default=None, max_length=PUBLIC_TOOL_TARGET_MAX_CHARS)
    payload: Dict[str, Any]


class VerificationDTO(BaseModel):
    command: Optional[str] = None
    exit_code: Optional[int] = None


class RunSnapshotDTO(BaseModel):
    run_id: str
    state: str  # idle | running | terminal
    status: Optional[str] = None  # SUCCESS | ERROR | INTERRUPTED
    phase: Optional[str] = None
    stop_reason: Optional[str] = None
    verification_status: Optional[str] = None
    final_text: Optional[str] = None
    task: Optional[str] = None
    step_count: int = 0
    provider_attempt_count: int = 0
    tool_call_count: int = 0
    mutated_paths: List[str] = Field(default_factory=list)
    last_verification: Optional[VerificationDTO] = None
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    elapsed_ms: Optional[int] = None
    error: Optional[ErrorDetail] = None
    events: List[ToolEventDTO] = Field(default_factory=list)
    events_total: int = 0
    events_retained_from: int = 0


class HealthDTO(BaseModel):
    status: str  # "ok"
    version: str
    run_state: str  # idle | running | terminal
    run_id: Optional[str] = None


class CapabilitiesDTO(BaseModel):
    wire_apis: List[str]
    max_steps: int
    char_budget: int


class UIPreferencesDTO(BaseModel):
    # Server does not persist locale/theme; the client keeps its own
    # non-sensitive UI preferences in localStorage. Fields stay in the DTO
    # for forward compatibility.
    locale: Optional[str] = None
    theme: Optional[str] = None


class BootstrapDTO(BaseModel):
    server_version: str
    session_token: str
    state: str
    run: Optional[RunSnapshotDTO] = None
    profiles: List[ProfileDTO] = Field(default_factory=list)
    active_profile_id: Optional[str] = None
    provider_presets: List[ProviderPresetDTO] = Field(default_factory=list)
    capabilities: CapabilitiesDTO
    ui: UIPreferencesDTO


# -------------------------------------------------- event payload whitelist

# Per-kind whitelist applied by RunController when adapting AgentEvent
# payloads. Anything not listed is dropped; values are already short
# summaries produced by the kernel.
EVENT_PAYLOAD_KEYS: Dict[str, frozenset] = {
    "run_started": frozenset({"task_chars"}),
    "step_started": frozenset({"char_count", "budget"}),
    "model_retry": frozenset({"attempt", "next_attempt", "reason"}),
    "assistant_received": frozenset({"text_chars", "tool_call_count"}),
    "tool_started": frozenset({"call_id", "name", "arguments", "target"}),
    "tool_finished": frozenset({"call_id", "name", "ok", "error_code", "summary"}),
    "completion_deferred": frozenset({"verification_status"}),
    "run_finished": frozenset(
        {
            "status",
            "stop_reason",
            "verification_status",
            "mutated_paths",
            "step_count",
            "provider_attempt_count",
            "tool_call_count",
        }
    ),
}

KNOWN_EVENT_KINDS = frozenset(EVENT_PAYLOAD_KEYS)
