import { ApiError } from "@/api/client";

export type Translate = (key: string, params?: Record<string, string | number>) => string;

/**
 * Map an API/client error to a user-facing message. Transport errors have no
 * backend message and must be rendered from the active locale instead of a
 * hardcoded string; other ApiErrors carry server-provided human text.
 */
export function apiErrorText(
  error: unknown,
  t: Translate,
  fallbackKey = "error.runFailure"
): string {
  if (error instanceof ApiError) {
    if (error.code === "transport_error") return t("error.transport");
    if (error.message) return error.message;
  }
  return t(fallbackKey);
}
