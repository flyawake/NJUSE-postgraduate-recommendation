import { ApiError } from "@/api/client";

export type Translate = (key: string, params?: Record<string, string | number>) => string;

const CODE_KEYS: Record<string, string> = {
  transport_error: "error.code.transport_error",
  http_error: "error.code.http_error",
  invalid_workspace: "error.code.invalid_workspace",
  invalid_task: "error.code.invalid_task",
  invalid_request: "error.code.invalid_request",
  invalid_config: "error.code.invalid_config",
  config_corrupt: "error.code.config_corrupt",
  config_io_error: "error.code.config_io_error",
  credential_invalid: "error.code.credential_invalid",
  credential_not_configured: "error.code.credential_not_configured",
  credential_env_readonly: "error.code.credential_env_readonly",
  credential_file_corrupt: "error.code.credential_file_corrupt",
  run_already_active: "error.code.run_already_active",
  run_not_found: "error.code.run_not_found",
  run_failed: "error.code.run_failed",
  model_error: "error.code.model_error",
  protocol_error: "error.code.protocol_error",
  context_overflow: "error.code.context_overflow",
  tool_failure_limit: "error.code.tool_failure_limit",
  repeated_tool_call: "error.code.repeated_tool_call",
  internal_error: "error.code.internal_error",
  bad_host: "error.code.bad_host",
  bad_origin: "error.code.bad_origin",
  invalid_session_token: "error.code.invalid_session_token",
  picker_unavailable: "error.code.picker_unavailable",
  checkpoint_restore_busy: "error.code.checkpoint_restore_busy",
  checkpoint_not_on_active_timeline: "error.code.checkpoint_not_on_active_timeline",
  checkpoint_turn_not_terminal: "error.code.checkpoint_turn_not_terminal",
  checkpoint_is_current: "error.code.checkpoint_is_current",
  checkpoint_inbox_not_empty: "error.code.checkpoint_inbox_not_empty",
  checkpoint_workspace_diverged: "error.code.checkpoint_workspace_diverged",
  checkpoint_change_set_missing: "error.code.checkpoint_change_set_missing",
  checkpoint_incomplete_changes: "error.code.checkpoint_incomplete_changes",
  checkpoint_artifact_missing: "error.code.checkpoint_artifact_missing",
  checkpoint_artifact_mismatch: "error.code.checkpoint_artifact_mismatch",
  checkpoint_artifact_unavailable: "error.code.checkpoint_artifact_unavailable",
  checkpoint_file_conflict: "error.code.checkpoint_file_conflict",
  checkpoint_symlink_unsupported: "error.code.checkpoint_symlink_unsupported",
  checkpoint_path_conflict: "error.code.checkpoint_path_conflict",
  checkpoint_path_unsafe: "error.code.checkpoint_path_unsafe",
  checkpoint_path_collision: "error.code.checkpoint_path_collision",
  checkpoint_file_unreadable: "error.code.checkpoint_file_unreadable",
  checkpoint_change_unsupported: "error.code.checkpoint_change_unsupported",
  checkpoint_timeline_changed: "error.code.checkpoint_timeline_changed",
  checkpoint_plan_corrupt: "error.code.checkpoint_plan_corrupt",
  checkpoint_restore_failed: "error.code.checkpoint_restore_failed",
  checkpoint_recovery_required: "error.code.checkpoint_recovery_required",
  checkpoint_write_failed: "error.code.checkpoint_write_failed",
  checkpoint_write_verify_failed: "error.code.checkpoint_write_verify_failed",
};

/**
 * Map a stable error code to the active locale. Backend human-readable
 * messages are intentionally NOT shown: they are zh-CN strings and would
 * leak untranslated text into the en-US UI.
 */
export function errorCodeText(code: string | null | undefined, t: Translate): string {
  if (!code) return t("error.runFailure");
  const key = CODE_KEYS[code];
  if (key) return t(key);
  return t("error.code.unknown", { code });
}

/**
 * Map an API/client error to a user-facing message from the active locale.
 */
export function apiErrorText(
  error: unknown,
  t: Translate,
  fallbackKey = "error.runFailure"
): string {
  if (error instanceof ApiError) return errorCodeText(error.code, t);
  return t(fallbackKey);
}
