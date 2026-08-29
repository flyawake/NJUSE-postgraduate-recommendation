import type { components } from "./schema";

export type Health = components["schemas"]["HealthDTO"];
export type Bootstrap = components["schemas"]["BootstrapDTO"];
export type RunSnapshot = components["schemas"]["RunSnapshotDTO"];
export type ToolEvent = components["schemas"]["ToolEventDTO"];
export type Profile = components["schemas"]["ProfileDTO"];
export type ProviderPreset = components["schemas"]["ProviderPresetDTO"];
export type CredentialInfo = components["schemas"]["CredentialInfoDTO"];
export type ErrorDetail = components["schemas"]["ErrorDetail"];
export type WorkspaceValidate = components["schemas"]["WorkspaceValidateResponse"];
export type WorkspacePick = components["schemas"]["WorkspacePickResponse"];
export type RunStartRequest = components["schemas"]["RunStartRequest"];
export type ProfileInput = components["schemas"]["ProfileInput"];
export type Conversation = components["schemas"]["ConversationDTO"];
export type ConversationPage = components["schemas"]["ConversationPageDTO"];
export type Turn = components["schemas"]["TurnDTO"];
export type TurnPage = components["schemas"]["TurnPageDTO"];
export type StreamCheckpoint = components["schemas"]["StreamCheckpointDTO"];
export type StreamSnapshot = components["schemas"]["StreamSnapshotDTO"];
export type ChangeSet = components["schemas"]["ChangeSetDTO"];
export type FileChange = components["schemas"]["FileChangeDTO"];
export type FilePreview = components["schemas"]["PreviewDTO"];
export type ConversationCreateRequest = components["schemas"]["ConversationCreateRequest"];
export type ConversationRenameRequest = components["schemas"]["ConversationRenameRequest"];
export type ConversationVersionRequest = components["schemas"]["ConversationVersionRequest"];
export type TurnCreateRequest = components["schemas"]["TurnCreateRequest"];
export type InboxItem = components["schemas"]["InboxItemDTO"];
export type InboxSnapshot = components["schemas"]["InboxSnapshotDTO"];
export type InboxEnqueueRequest = components["schemas"]["InboxEnqueueRequest"];
export type InboxEditRequest = components["schemas"]["InboxEditRequest"];
export type InboxOrderRequest = components["schemas"]["InboxOrderRequest"];
export type InboxVersionRequest = components["schemas"]["InboxVersionRequest"];
export type Memory = components["schemas"]["MemoryDTO"];
export type MemoryPage = components["schemas"]["MemoryPageDTO"];
export type MemoryCreateRequest = components["schemas"]["MemoryCreateRequest"];
export type MemoryEditRequest = components["schemas"]["MemoryEditRequest"];
export type MemoryVersionRequest = components["schemas"]["MemoryVersionRequest"];
export type MemoryResetRequest = components["schemas"]["MemoryResetRequest"];
export type MemorySettingsRequest = components["schemas"]["MemorySettingsRequest"];
export type MemorySettings = components["schemas"]["MemorySettingsDTO"];
export type MemoryUsage = components["schemas"]["MemoryUsageDTO"];

export class ApiError extends Error {
  readonly code: string;
  readonly field?: string | null;
  readonly status: number;

  constructor(code: string, message: string, status: number, field?: string | null) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
    this.field = field;
  }
}

let sessionToken: string | null = null;

export function setSessionToken(token: string | null): void {
  sessionToken = token;
}

export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  headers.set("Accept", "application/json");
  if (init?.body) headers.set("Content-Type", "application/json");
  const method = (init?.method ?? "GET").toUpperCase();
  if (sessionToken && method !== "GET" && method !== "HEAD" && method !== "OPTIONS") {
    headers.set("X-Coding-Agent-Token", sessionToken);
  }
  let response: Response;
  try {
    response = await fetch(path, { ...init, headers });
  } catch (error: unknown) {
    // Cancellation is control flow for hooks that supersede or unmount a
    // request. Preserve it instead of presenting it as a transport failure.
    if (
      typeof error === "object" &&
      error !== null &&
      "name" in error &&
      error.name === "AbortError"
    ) {
      throw error;
    }
    // Locale-neutral marker; the UI renders it through i18n.
    throw new ApiError("transport_error", "", 0);
  }
  if (response.status === 204) return undefined as T;
  let body: unknown = null;
  const text = await response.text();
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = null;
  }
  if (!response.ok) {
    const error = (body as { error?: ErrorDetail } | null)?.error;
    throw new ApiError(
      error?.code ?? "http_error",
      error?.message ?? `HTTP ${response.status}`,
      response.status,
      error?.field
    );
  }
  return body as T;
}

export const api = {
  health: () => apiFetch<Health>("/api/health"),

  /**
   * Bootstrap also arms the session token used by every state-changing
   * request. It lives here (not in a caller's queryFn) so any component
   * storing the bootstrap result still activates CSRF protection.
   */
  bootstrap: async (): Promise<Bootstrap> => {
    const data = await apiFetch<Bootstrap>("/api/bootstrap");
    sessionToken = data.session_token;
    return data;
  },

  validateWorkspace: (path: string, signal?: AbortSignal) =>
    apiFetch<WorkspaceValidate>("/api/workspace/validate", {
      method: "POST",
      body: JSON.stringify({ path }),
      signal,
    }),

  /** Open the OS-native folder picker on the server machine. */
  pickWorkspace: () =>
    apiFetch<WorkspacePick>("/api/workspace/pick", { method: "POST" }),

  startRun: (request: RunStartRequest) =>
    apiFetch<RunSnapshot>("/api/runs", { method: "POST", body: JSON.stringify(request) }),

  getRun: (runId: string) => apiFetch<RunSnapshot>(`/api/runs/${encodeURIComponent(runId)}`),

  cancelRun: (runId: string) =>
    apiFetch<RunSnapshot>(`/api/runs/${encodeURIComponent(runId)}/cancel`, { method: "POST" }),

  listConversations: (params?: { archived?: boolean; query?: string; limit?: number; cursor?: string }) => {
    const query = new URLSearchParams();
    if (params?.archived !== undefined) query.set("archived", String(params.archived));
    if (params?.query) query.set("query", params.query);
    if (params?.limit) query.set("limit", String(params.limit));
    if (params?.cursor) query.set("cursor", params.cursor);
    const suffix = query.toString() ? `?${query.toString()}` : "";
    return apiFetch<ConversationPage>(`/api/conversations${suffix}`);
  },

  createConversation: (input: ConversationCreateRequest) =>
    apiFetch<Conversation>("/api/conversations", { method: "POST", body: JSON.stringify(input) }),

  getConversation: (id: string) =>
    apiFetch<Conversation>(`/api/conversations/${encodeURIComponent(id)}`),

  renameConversation: (id: string, input: ConversationRenameRequest) =>
    apiFetch<Conversation>(`/api/conversations/${encodeURIComponent(id)}`, {
      method: "PATCH",
      body: JSON.stringify(input),
    }),

  archiveConversation: (id: string, input: ConversationVersionRequest) =>
    apiFetch<Conversation>(`/api/conversations/${encodeURIComponent(id)}/archive`, {
      method: "POST",
      body: JSON.stringify(input),
    }),

  unarchiveConversation: (id: string, input: ConversationVersionRequest) =>
    apiFetch<Conversation>(`/api/conversations/${encodeURIComponent(id)}/unarchive`, {
      method: "POST",
      body: JSON.stringify(input),
    }),

  deleteConversation: (id: string, input: ConversationVersionRequest) =>
    apiFetch<void>(`/api/conversations/${encodeURIComponent(id)}`, {
      method: "DELETE",
      body: JSON.stringify(input),
    }),

  listTurns: (id: string, params?: { limit?: number; cursor?: string }) => {
    const query = new URLSearchParams();
    if (params?.limit) query.set("limit", String(params.limit));
    if (params?.cursor) query.set("cursor", params.cursor);
    const suffix = query.toString() ? `?${query.toString()}` : "";
    return apiFetch<TurnPage>(`/api/conversations/${encodeURIComponent(id)}/turns${suffix}`);
  },

  startTurn: (id: string, input: TurnCreateRequest) =>
    apiFetch<Turn>(`/api/conversations/${encodeURIComponent(id)}/turns`, {
      method: "POST",
      body: JSON.stringify(input),
    }),

  getTurn: (id: string, turnId: string) =>
    apiFetch<Turn>(`/api/conversations/${encodeURIComponent(id)}/turns/${encodeURIComponent(turnId)}`),

  getTurnEvents: (id: string, turnId: string, params?: { afterSeq?: number; limit?: number }) => {
    const query = new URLSearchParams();
    if (params?.afterSeq) query.set("after_seq", String(params.afterSeq));
    if (params?.limit) query.set("limit", String(params.limit));
    const suffix = query.toString() ? `?${query.toString()}` : "";
    return (
    apiFetch<ToolEvent[]>(
      `/api/conversations/${encodeURIComponent(id)}/turns/${encodeURIComponent(turnId)}/events${suffix}`
    ));
  },

  cancelTurn: (id: string, turnId: string) =>
    apiFetch<Turn>(`/api/conversations/${encodeURIComponent(id)}/turns/${encodeURIComponent(turnId)}/cancel`, {
      method: "POST",
    }),

  getStreamSnapshot: (id: string, turnId: string) =>
    apiFetch<StreamSnapshot>(
      `/api/conversations/${encodeURIComponent(id)}/turns/${encodeURIComponent(turnId)}/stream`
    ),

  getInbox: (id: string) =>
    apiFetch<InboxSnapshot>(`/api/conversations/${encodeURIComponent(id)}/inbox`),

  enqueueInbox: (id: string, input: InboxEnqueueRequest) =>
    apiFetch<InboxSnapshot>(`/api/conversations/${encodeURIComponent(id)}/inbox`, {
      method: "POST",
      body: JSON.stringify(input),
    }),

  editInbox: (id: string, itemId: string, input: InboxEditRequest) =>
    apiFetch<InboxSnapshot>(
      `/api/conversations/${encodeURIComponent(id)}/inbox/${encodeURIComponent(itemId)}`,
      {
        method: "PATCH",
        body: JSON.stringify(input),
      }
    ),

  removeInbox: (id: string, itemId: string, input: InboxVersionRequest) =>
    apiFetch<InboxSnapshot>(
      `/api/conversations/${encodeURIComponent(id)}/inbox/${encodeURIComponent(itemId)}`,
      {
        method: "DELETE",
        body: JSON.stringify(input),
      }
    ),

  reorderInbox: (id: string, input: InboxOrderRequest) =>
    apiFetch<InboxSnapshot>(
      `/api/conversations/${encodeURIComponent(id)}/inbox/order`,
      { method: "PUT", body: JSON.stringify(input) }
    ),

  steerInbox: (id: string, itemId: string, input: InboxVersionRequest) =>
    apiFetch<InboxSnapshot>(
      `/api/conversations/${encodeURIComponent(id)}/inbox/${encodeURIComponent(itemId)}/steer`,
      { method: "POST", body: JSON.stringify(input) }
    ),

  retryInbox: (id: string, itemId: string, input: InboxVersionRequest) =>
    apiFetch<InboxSnapshot>(
      `/api/conversations/${encodeURIComponent(id)}/inbox/${encodeURIComponent(itemId)}/retry`,
      { method: "POST", body: JSON.stringify(input) }
    ),

  getTurnChanges: (id: string, turnId: string) =>
    apiFetch<ChangeSet>(`/api/conversations/${encodeURIComponent(id)}/turns/${encodeURIComponent(turnId)}/changes`),

  getFileChange: (id: string, turnId: string, changeId: string) =>
    apiFetch<FileChange>(`/api/conversations/${encodeURIComponent(id)}/turns/${encodeURIComponent(turnId)}/changes/${encodeURIComponent(changeId)}`),

  getFilePreview: (id: string, turnId: string, changeId: string, mode = "diff") =>
    apiFetch<FilePreview>(
      `/api/conversations/${encodeURIComponent(id)}/turns/${encodeURIComponent(turnId)}/changes/${encodeURIComponent(changeId)}/preview?mode=${encodeURIComponent(mode)}`
    ),

  listMemories: (params?: { scope_type?: string; scope_key?: string; status?: string; query?: string; limit?: number; cursor?: string }) => {
    const query = new URLSearchParams();
    if (params?.scope_type) query.set("scope_type", params.scope_type);
    if (params?.scope_key) query.set("scope_key", params.scope_key);
    if (params?.status) query.set("status", params.status);
    if (params?.query) query.set("query", params.query);
    if (params?.limit) query.set("limit", String(params.limit));
    if (params?.cursor) query.set("cursor", params.cursor);
    const suffix = query.toString() ? `?${query.toString()}` : "";
    return apiFetch<MemoryPage>(`/api/memories${suffix}`);
  },

  createMemory: (input: MemoryCreateRequest) =>
    apiFetch<Memory>("/api/memories", { method: "POST", body: JSON.stringify(input) }),

  getMemory: (id: string) =>
    apiFetch<Memory>(`/api/memories/${encodeURIComponent(id)}`),

  editMemory: (id: string, input: MemoryEditRequest) =>
    apiFetch<Memory>(`/api/memories/${encodeURIComponent(id)}`, {
      method: "PATCH",
      body: JSON.stringify(input),
    }),

  deleteMemory: (id: string, input: MemoryVersionRequest) =>
    apiFetch<void>(`/api/memories/${encodeURIComponent(id)}`, {
      method: "DELETE",
      body: JSON.stringify(input),
    }),

  approveMemory: (id: string, input: MemoryVersionRequest) =>
    apiFetch<Memory>(`/api/memories/${encodeURIComponent(id)}/approve`, {
      method: "POST",
      body: JSON.stringify(input),
    }),

  rejectMemory: (id: string, input: MemoryVersionRequest) =>
    apiFetch<Memory>(`/api/memories/${encodeURIComponent(id)}/reject`, {
      method: "POST",
      body: JSON.stringify(input),
    }),

  resetMemories: (input: MemoryResetRequest) =>
    apiFetch<{ scope_type: string; scope_key: string; deleted: number }>("/api/memories/reset", {
      method: "POST",
      body: JSON.stringify(input),
    }),

  getMemorySettings: (scopeType?: string, scopeKey?: string) => {
    const query = new URLSearchParams();
    if (scopeType) query.set("scope_type", scopeType);
    if (scopeKey) query.set("scope_key", scopeKey);
    const suffix = query.toString() ? `?${query.toString()}` : "";
    return apiFetch<MemorySettings>(`/api/memories/settings${suffix}`);
  },

  setMemorySettings: (input: MemorySettingsRequest) =>
    apiFetch<MemorySettings>("/api/memories/settings", {
      method: "POST",
      body: JSON.stringify(input),
    }),

  getTurnMemoryUsage: (conversationId: string, turnId: string) =>
    apiFetch<MemoryUsage[]>(`/api/conversations/${encodeURIComponent(conversationId)}/turns/${encodeURIComponent(turnId)}/memory-usage`),

  listProfiles: () => apiFetch<Profile[]>("/api/profiles"),

  createProfile: (input: ProfileInput) =>
    apiFetch<Profile>("/api/profiles", { method: "POST", body: JSON.stringify(input) }),

  updateProfile: (profileId: string, input: ProfileInput) =>
    apiFetch<Profile>(`/api/profiles/${encodeURIComponent(profileId)}`, {
      method: "PUT",
      body: JSON.stringify(input),
    }),

  deleteProfile: (profileId: string) =>
    apiFetch<void>(`/api/profiles/${encodeURIComponent(profileId)}`, { method: "DELETE" }),

  activateProfile: (profileId: string) =>
    apiFetch<Profile>(`/api/profiles/${encodeURIComponent(profileId)}/activate`, { method: "POST" }),

  getCredential: (profileId: string) =>
    apiFetch<CredentialInfo>(`/api/profiles/${encodeURIComponent(profileId)}/credential`),

  setCredential: (profileId: string, secret: string) =>
    apiFetch<CredentialInfo>(`/api/profiles/${encodeURIComponent(profileId)}/credential`, {
      method: "PUT",
      body: JSON.stringify({ secret }),
    }),

  unsetCredential: (profileId: string) =>
    apiFetch<CredentialInfo>(`/api/profiles/${encodeURIComponent(profileId)}/credential`, {
      method: "DELETE",
    }),
};
