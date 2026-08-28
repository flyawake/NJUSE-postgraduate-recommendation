import { afterEach, describe, expect, it, vi } from "vitest";
import { apiFetch, setSessionToken } from "@/api/client";

afterEach(() => {
  vi.unstubAllGlobals();
  setSessionToken(null);
});

describe("apiFetch cancellation", () => {
  it("preserves AbortError as cancellation control flow", async () => {
    const abortError = new DOMException("Aborted", "AbortError");
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(abortError));

    await expect(apiFetch("/api/test", { signal: new AbortController().signal })).rejects.toBe(
      abortError
    );
  });
});
