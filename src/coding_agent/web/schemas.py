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


class ConversationCreateRequest(StrictModel):
    workspace: str = Field(min_length=1, max_length=MAX_WORKSPACE_CHARS)
    profile_id: Optional[str] = None
    title: Optional[str] = Field(default=None, max_length=200)


class ConversationRenameRequest(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    expected_version: int = Field(ge=1)


class ConversationVersionRequest(StrictModel):
    expected_version: int = Field(ge=1)
    confirm: bool = False


class TurnCreateRequest(StrictModel):
    content: str = Field(min_length=1, max_length=MAX_TASK_CHARS)
    idempotency_key: Optional[str] = Field(default=None, max_length=128)
    profile_id: Optional[str] = None
    reasoning_effort: Optional[str] = None


class WorkspaceValidateRequest(StrictModel):
    path: str = Field(min_length=1, max_length=MAX_WORKSPACE_CHARS)


class ProfileInput(StrictModel):
    provider_id: str
    display_name: str
    base_url: str
    model: str
    credential_ref: Optional[str] = None
    wire_api: Optional[str] = None
    reasoning_mode: Optional[str] = None
    reasoning_effort: Optional[str] = None
    show_reasoning: Optional[bool] = None


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
    reasoning_mode: str = "auto"
    reasoning_effort: Optional[str] = None
    show_reasoning: bool = False
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


class ConversationDTO(BaseModel):
    id: str
    title: str
    title_source: str
    workspace_path: str
    workspace_key: str
    profile_id: Optional[str] = None
    state: str
    version: int
    created_at: str
    last_activity_at: str
    archived_at: Optional[str] = None
    latest_turn: Optional["TurnDTO"] = None


class ConversationPageDTO(BaseModel):
    items: List[ConversationDTO]
    next_cursor: Optional[str] = None


class TurnDTO(BaseModel):
    id: str
    conversation_id: str
    ordinal: int
    state: str
    run_id: Optional[str] = None
    user_text: str
    created_at: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    error_code: Optional[str] = None
    active: bool = False


class InboxEnqueueRequest(StrictModel):
    content: str = Field(min_length=1, max_length=MAX_TASK_CHARS)
    mode: str = "queue"
    idempotency_key: Optional[str] = Field(default=None, max_length=128)
    profile_id: Optional[str] = None
    reasoning_effort: Optional[str] = None


class InboxEditRequest(StrictModel):
    content: Optional[str] = Field(default=None, max_length=MAX_TASK_CHARS)
    mode: Optional[str] = None
    expected_version: int = Field(ge=1)


class InboxVersionRequest(StrictModel):
    expected_version: int = Field(ge=1)


class InboxOrderRequest(StrictModel):
    ordered_ids: List[str]
    expected_queue_version: int = Field(ge=1)


class InboxItemDTO(BaseModel):
    id: str
    conversation_id: str
    content: str
    requested_mode: str
    state: str
    position: int
    bound_turn_id: Optional[str] = None
    claimed_turn_id: Optional[str] = None
    idempotency_key: Optional[str] = None
    profile_id: Optional[str] = None
    reasoning_effort: Optional[str] = None
    version: int
    last_error_code: Optional[str] = None
    created_at: str
    updated_at: str
    claimed_at: Optional[str] = None
    delivered_at: Optional[str] = None


class InboxSnapshotDTO(BaseModel):
    queue_version: int
    items: List[InboxItemDTO] = Field(default_factory=list)
    recent_events: List[Dict[str, Any]] = Field(default_factory=list)


class MemoryCreateRequest(StrictModel):
    scope_type: str
    scope_key: str
    kind: str
    content: str = Field(min_length=1, max_length=4000)
    title: Optional[str] = Field(default=None, max_length=120)
    source_conversation_id: Optional[str] = None
    source_turn_id: Optional[str] = None
    source_excerpt: Optional[str] = Field(default=None, max_length=500)
    idempotency_key: Optional[str] = Field(default=None, min_length=1, max_length=120)


class MemoryEditRequest(StrictModel):
    content: str = Field(min_length=1, max_length=4000)
    kind: Optional[str] = None
    title: Optional[str] = Field(default=None, max_length=120)
    expected_version: int = Field(ge=1)
    idempotency_key: Optional[str] = Field(default=None, min_length=1, max_length=120)


class MemoryVersionRequest(StrictModel):
    expected_version: int = Field(ge=1)
    idempotency_key: Optional[str] = Field(default=None, min_length=1, max_length=120)


class MemoryResetRequest(StrictModel):
    scope_type: str
    scope_key: str
    confirm: bool = False
    idempotency_key: Optional[str] = Field(default=None, min_length=1, max_length=120)
    expected_scope_version: Optional[int] = Field(default=None, ge=0)


class MemorySettingsRequest(StrictModel):
    scope_type: str = "global"
    scope_key: str = "global"
    enabled: bool
    candidate_enabled: Optional[bool] = None


class MemoryDTO(BaseModel):
    id: str
    scope_type: str
    scope_key: str
    kind: str
    title: Optional[str] = None
    content: str
    status: str
    confirmation: str
    source_conversation_id: Optional[str] = None
    source_turn_id: Optional[str] = None
    source_excerpt: Optional[str] = None
    supersedes_id: Optional[str] = None
    version: int
    normalized_hash: str
    created_at: str
    updated_at: str
    last_used_at: Optional[str] = None
    use_count: int
    sources: List[Dict[str, Any]] = Field(default_factory=list)


class MemoryPageDTO(BaseModel):
    items: List[MemoryDTO]
    next_cursor: Optional[str] = None


class MemoryUsageDTO(BaseModel):
    turn_id: str
    entry_id: str
    rank: int
    reason: str
    snapshot_hash: str
    used_at: str
    scope_type: Optional[str] = None
    scope_key: Optional[str] = None
    kind: Optional[str] = None
    title: Optional[str] = None
    source_conversation_id: Optional[str] = None
    source_turn_id: Optional[str] = None


class MemorySettingsDTO(BaseModel):
    enabled: bool
    candidate_enabled: bool = False
    scope_type: Optional[str] = None
    scope_key: Optional[str] = None
    scope_version: int = 0


class StreamCheckpointDTO(BaseModel):
    run_id: str
    attempt: int
    channel: str
    text: str
    char_count: int
    event_seq: int
    updated_at: str


class StreamSnapshotDTO(BaseModel):
    checkpoints: List[StreamCheckpointDTO] = Field(default_factory=list)


class TurnPageDTO(BaseModel):
    items: List[TurnDTO]
    next_cursor: Optional[str] = None


class FileChangeDTO(BaseModel):
    id: str
    relative_path: str
    old_relative_path: Optional[str] = None
    change_type: str
    source: str
    before_blob_id: Optional[str] = None
    after_blob_id: Optional[str] = None
    before_sha: Optional[str] = None
    after_sha: Optional[str] = None
    additions: int = 0
    deletions: int = 0
    binary: bool = False
    preview_status: str = "available"
    warnings: List[str] = Field(default_factory=list)


class ChangeSetDTO(BaseModel):
    id: str
    conversation_id: str
    turn_id: str
    status: str
    additions: int
    deletions: int
    file_count: int
    coverage: str
    finalized_at: Optional[str] = None
    files: List[FileChangeDTO] = Field(default_factory=list)


class PreviewDTO(BaseModel):
    change_id: str
    relative_path: str
    change_type: str
    mode: str
    lines: List[str] = Field(default_factory=list)
    additions: int = 0
    deletions: int = 0
    truncated: bool = False
    binary: bool = False
    before_sha: Optional[str] = None
    after_sha: Optional[str] = None
    current_sha: Optional[str] = None
    diverged: bool = False
    error: Optional[ErrorDetail] = None


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
    "model_stream_started": frozenset({"attempt"}),
    "assistant_text_delta": frozenset({"delta", "attempt"}),
    "reasoning_delta": frozenset({"delta", "attempt", "visibility"}),
    "reasoning_summary_delta": frozenset({"delta", "summary_index", "attempt"}),
    "stream_attempt_abandoned": frozenset({"attempt", "reason"}),
    "assistant_received": frozenset(
        {"text_chars", "tool_call_count", "attempt", "elapsed_ms"}
    ),
    "tool_started": frozenset({"call_id", "name", "arguments", "target"}),
    "tool_finished": frozenset({"call_id", "name", "ok", "error_code", "summary"}),
    "completion_deferred": frozenset({"verification_status"}),
    "steer_delivered": frozenset({"item_id", "chars"}),
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
