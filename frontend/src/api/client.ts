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
export type RunStartRequest = components["schemas"]["RunStartRequest"];
export type ProfileInput = components["schemas"]["ProfileInput"];

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
  } catch {
    throw new ApiError("transport_error", "无法连接本地服务", 0);
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

  validateWorkspace: (path: string) =>
    apiFetch<WorkspaceValidate>("/api/workspace/validate", {
      method: "POST",
      body: JSON.stringify({ path }),
    }),

  startRun: (request: RunStartRequest) =>
    apiFetch<RunSnapshot>("/api/runs", { method: "POST", body: JSON.stringify(request) }),

  getRun: (runId: string) => apiFetch<RunSnapshot>(`/api/runs/${encodeURIComponent(runId)}`),

  cancelRun: (runId: string) =>
    apiFetch<RunSnapshot>(`/api/runs/${encodeURIComponent(runId)}/cancel`, { method: "POST" }),

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
