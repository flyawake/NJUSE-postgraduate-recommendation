import { useEffect, useRef, useState } from "react";
import { ApiError, api } from "@/api/client";

export type WorkspaceValidationStatus =
  | "empty"
  | "debouncing"
  | "checking"
  | "valid"
  | "invalid"
  | "error";

export interface WorkspaceValidation {
  status: WorkspaceValidationStatus;
  requestKey: string;
  resolvedPath: string | null;
  errorCode: string | null;
  retry: () => void;
}

interface ValidationResult {
  valid: boolean;
  resolvedPath: string | null;
  errorCode: string | null;
}

type ValidationOutcome =
  | { kind: "result"; result: ValidationResult }
  | { kind: "error"; errorCode: string };

interface InFlightValidation {
  controller: AbortController;
  consumers: number;
  promise: Promise<ValidationOutcome>;
}

const CACHE = new Map<string, ValidationResult>();
const IN_FLIGHT = new Map<string, InFlightValidation>();

function isAbortError(error: unknown): boolean {
  return (
    typeof error === "object" &&
    error !== null &&
    "name" in error &&
    error.name === "AbortError"
  );
}

/** Normalise only presentation-level path differences; the server owns canonicalisation. */
export function workspaceRequestKey(value: string): string {
  const trimmed = value.trim().replace(/\\/g, "/");
  if (!trimmed) return "";
  if (/^[A-Za-z]:\/$/.test(trimmed) || trimmed === "/") return trimmed;
  return trimmed.replace(/\/+$/, "");
}

function toResult(value: Awaited<ReturnType<typeof api.validateWorkspace>>): ValidationResult {
  return {
    valid: value.valid,
    resolvedPath: value.valid ? value.resolved_path ?? null : null,
    errorCode: value.valid ? null : value.error?.code ?? "invalid_workspace",
  };
}

function acquireValidation(requestKey: string): {
  promise: Promise<ValidationOutcome>;
  release: () => void;
} {
  let pending = IN_FLIGHT.get(requestKey);
  if (!pending) {
    const controller = new AbortController();
    const created: InFlightValidation = {
      controller,
      consumers: 0,
      promise: api
        .validateWorkspace(requestKey, controller.signal)
        .then((value) => ({ kind: "result" as const, result: toResult(value) }))
        .catch((error: unknown): ValidationOutcome => {
          if (isAbortError(error)) throw error;
          if (error instanceof ApiError) {
            return { kind: "error", errorCode: error.code };
          }
          return { kind: "error", errorCode: "transport_error" };
        })
        .then((outcome) => {
          // Some transports and test doubles can still resolve after abort.
          // Such a stale response must neither publish nor poison the cache.
          if (controller.signal.aborted) {
            throw new DOMException("Aborted", "AbortError");
          }
          // Only a completed domain validation (valid *or* invalid) is stable
          // enough to cache. Transport and HTTP failures must stay retryable.
          if (outcome.kind === "result") CACHE.set(requestKey, outcome.result);
          return outcome;
        })
        .finally(() => {
          // A released request may settle after a newer request for the same
          // path has occupied this key. Never delete that newer generation.
          if (IN_FLIGHT.get(requestKey) === created) IN_FLIGHT.delete(requestKey);
        }),
    };
    pending = created;
    IN_FLIGHT.set(requestKey, pending);
  }
  pending.consumers += 1;
  const ownedEntry = pending;
  let released = false;
  return {
    promise: ownedEntry.promise,
    release: () => {
      if (released) return;
      released = true;
      if (IN_FLIGHT.get(requestKey) !== ownedEntry) return;
      ownedEntry.consumers -= 1;
      if (ownedEntry.consumers <= 0) {
        ownedEntry.controller.abort();
        if (IN_FLIGHT.get(requestKey) === ownedEntry) IN_FLIGHT.delete(requestKey);
      }
    },
  };
}

/**
 * Validation is isolated from all surrounding UI state. Its request effect
 * depends only on the normalised path and an explicit retry token, so locale,
 * callbacks, task draft, profile, theme and run events cannot refetch it.
 */
export function useWorkspaceValidation(
  value: string,
  onValidated?: (resolvedPath: string | null) => void
): WorkspaceValidation {
  const requestKey = workspaceRequestKey(value);
  const callbackRef = useRef(onValidated);
  const requestKeyRef = useRef(requestKey);
  const generation = useRef(0);
  const forcedRequestKey = useRef<string | null>(null);
  const [retryGeneration, setRetryGeneration] = useState(0);
  const retryRef = useRef<() => void>(() => undefined);
  const [state, setState] = useState<Omit<WorkspaceValidation, "retry">>(() => ({
    status: requestKey ? "debouncing" : "empty",
    requestKey,
    resolvedPath: null,
    errorCode: null,
  }));

  callbackRef.current = onValidated;
  requestKeyRef.current = requestKey;
  retryRef.current = () => {
    const activeKey = requestKeyRef.current;
    if (!activeKey) return;
    // Defensive invalidation also covers an implementation change or a
    // manually seeded cache. A retry never consumes an old failure result.
    CACHE.delete(activeKey);
    forcedRequestKey.current = activeKey;
    setRetryGeneration((current) => current + 1);
  };

  useEffect(() => {
    const publish = (next: Omit<WorkspaceValidation, "retry">) => {
      setState(next);
      callbackRef.current?.(next.status === "valid" ? next.resolvedPath : null);
    };

    if (!requestKey) {
      generation.current += 1;
      publish({ status: "empty", requestKey, resolvedPath: null, errorCode: null });
      return;
    }

    const retrying = forcedRequestKey.current === requestKey;
    if (retrying) forcedRequestKey.current = null;
    const cached = retrying ? undefined : CACHE.get(requestKey);
    if (cached) {
      publish({
        status: cached.valid ? "valid" : "invalid",
        requestKey,
        resolvedPath: cached.resolvedPath,
        errorCode: cached.errorCode,
      });
      return;
    }

    const activeGeneration = ++generation.current;
    publish({ status: "debouncing", requestKey, resolvedPath: null, errorCode: null });
    let release: (() => void) | undefined;
    const beginRequest = () => {
      if (activeGeneration !== generation.current || requestKeyRef.current !== requestKey) return;
      publish({ status: "checking", requestKey, resolvedPath: null, errorCode: null });
      const acquired = acquireValidation(requestKey);
      release = acquired.release;
      acquired.promise
        .then((outcome) => {
          if (activeGeneration !== generation.current || requestKeyRef.current !== requestKey) return;
          if (outcome.kind === "error") {
            publish({ status: "error", requestKey, resolvedPath: null, errorCode: outcome.errorCode });
            return;
          }
          const { result } = outcome;
          publish({
            status: result.valid ? "valid" : "invalid",
            requestKey,
            resolvedPath: result.resolvedPath,
            errorCode: result.errorCode,
          });
        })
        .catch((error: unknown) => {
          if (activeGeneration !== generation.current || requestKeyRef.current !== requestKey) return;
          if (isAbortError(error)) return;
          publish({ status: "error", requestKey, resolvedPath: null, errorCode: "transport_error" });
        });
    };
    const timer = retrying ? undefined : setTimeout(beginRequest, 400);
    if (retrying) beginRequest();

    return () => {
      if (timer) clearTimeout(timer);
      release?.();
    };
  }, [requestKey, retryGeneration]);

  return { ...state, retry: retryRef.current };
}

/** Test-only reset. It is deliberately not imported by production UI code. */
export function resetWorkspaceValidationCache(): void {
  for (const pending of IN_FLIGHT.values()) pending.controller.abort();
  IN_FLIGHT.clear();
  CACHE.clear();
}
