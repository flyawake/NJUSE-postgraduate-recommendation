import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useReducer,
  useRef,
} from "react";
import type { ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import type { RunSnapshot, ToolEvent } from "@/api/client";
import { subscribeToRunEvents } from "./sse";

export type SseStatus = "idle" | "connected" | "disconnected";

export interface RunStoreState {
  events: ToolEvent[];
  lastEventId: number;
  sseStatus: SseStatus;
  cancelling: boolean;
}

const MAX_CLIENT_EVENTS = 2000;

const initialState: RunStoreState = {
  events: [],
  lastEventId: 0,
  sseStatus: "idle",
  cancelling: false,
};

type Action =
  | { type: "APPEND"; events: ToolEvent[] }
  | { type: "RESET_EVENTS"; events: ToolEvent[] }
  | { type: "SSE_STATUS"; status: SseStatus }
  | { type: "CANCEL_REQUESTED" }
  | { type: "CANCEL_FAILED" }
  | { type: "CLEAR" };

export function runReducer(state: RunStoreState, action: Action): RunStoreState {
  switch (action.type) {
    case "APPEND": {
      const next = action.events.filter((event) => event.id > state.lastEventId);
      if (!next.length) return { ...state, sseStatus: "connected" };
      const merged = [...state.events, ...next];
      const trimmed = merged.length > MAX_CLIENT_EVENTS ? merged.slice(-MAX_CLIENT_EVENTS) : merged;
      return {
        ...state,
        events: trimmed,
        lastEventId: next[next.length - 1].id,
        sseStatus: "connected",
      };
    }
    case "RESET_EVENTS": {
      const sorted = [...action.events].sort((a, b) => a.id - b.id);
      const trimmed = sorted.length > MAX_CLIENT_EVENTS ? sorted.slice(-MAX_CLIENT_EVENTS) : sorted;
      return {
        ...state,
        events: trimmed,
        // An empty reset must clear the baseline too: otherwise replayed
        // events with ids <= the old lastEventId would be filtered out.
        lastEventId: trimmed.length ? trimmed[trimmed.length - 1].id : 0,
        sseStatus: "connected",
      };
    }
    case "SSE_STATUS":
      return { ...state, sseStatus: action.status };
    case "CANCEL_REQUESTED":
      return { ...state, cancelling: true };
    case "CANCEL_FAILED":
      return { ...state, cancelling: false };
    case "CLEAR":
      return initialState;
  }
}

export interface RunStore {
  runId: string | null;
  snapshot?: RunSnapshot;
  snapshotLoading: boolean;
  events: ToolEvent[];
  sseStatus: SseStatus;
  cancelling: boolean;
  startRun: (request: { workspace: string; task: string; profileId: string | null }) => Promise<RunSnapshot>;
  cancelRun: () => Promise<void>;
  clear: () => void;
  refetchSnapshot: () => void;
}

const RunStoreContext = createContext<RunStore | null>(null);

export function RunStoreProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [state, dispatch] = useReducer(runReducer, initialState);
  const runIdRef = useRef<string | null>(null);
  const initializedRunIdRef = useRef<string | null>(null);

  // Bootstrap: session token (armed inside api.bootstrap) + profile baseline.
  const bootstrapQuery = useQuery({
    queryKey: ["bootstrap"],
    queryFn: api.bootstrap,
    staleTime: 30_000,
    retry: 1,
  });

  const bootstrapRunId = bootstrapQuery.data?.run?.run_id ?? null;
  // The active run is the bootstrapped one until a new run starts.
  if (runIdRef.current === null && bootstrapRunId) runIdRef.current = bootstrapRunId;
  const runId = runIdRef.current;

  const runQuery = useQuery({
    queryKey: ["run", runId],
    queryFn: () => api.getRun(runId as string),
    enabled: Boolean(runId),
    refetchInterval: (query) => {
      const snap = query.state.data;
      return snap?.state === "running" ? 4000 : false;
    },
    retry: 1,
  });

  const snapshot = runQuery.data;
  const snapshotLoading = runQuery.isLoading;

  // Initialize the event list from the server snapshot exactly once per run
  // (refresh / bootstrap restore / new run). Later snapshots must not
  // overwrite events that SSE already delivered; reconnect recovery is
  // handled by the SSE `reset` contract instead.
  useEffect(() => {
    if (!runId || initializedRunIdRef.current === runId) return;
    if (!snapshot?.events?.length) return;
    dispatch({ type: "RESET_EVENTS", events: snapshot.events });
    initializedRunIdRef.current = runId;
  }, [runId, snapshot]);

  const startRunMutation = useMutation({
    mutationFn: api.startRun,
    onSuccess: (snap) => {
      runIdRef.current = snap.run_id;
      initializedRunIdRef.current = null;
      dispatch({ type: "CLEAR" });
      queryClient.setQueryData(["run", snap.run_id], snap);
      queryClient.invalidateQueries({ queryKey: ["bootstrap"] });
    },
  });

  const cancelRunMutation = useMutation({
    mutationFn: () => api.cancelRun(runId as string),
    onMutate: () => dispatch({ type: "CANCEL_REQUESTED" }),
    onSuccess: (snap) => {
      queryClient.setQueryData(["run", snap.run_id], snap);
    },
    onError: () => dispatch({ type: "CANCEL_FAILED" }),
  });

  // SSE subscription lifecycle. Reconnects always resync from the server's
  // bounded tail (the server sends `reset` with the full tail when the client
  // is behind), so no client-side event-id bookkeeping is required across
  // reconnects.
  useEffect(() => {
    if (!runId) return;
    const signalState = snapshot?.state ?? "idle";
    if (signalState === "terminal" || signalState === "idle") return;

    const controller = new AbortController();
    let reconnectTimer: ReturnType<typeof setTimeout> | undefined;
    let disposed = false;

    const connect = () => {
      if (disposed) return;
      // Subscribe from the current baseline so a reconnect resumes after the
      // last event the client actually has.
      subscribeToRunEvents(runId, {
        lastEventId: state.lastEventId || null,
        signal: controller.signal,
        onEvent: (message) => {
          if (message.event === "hello") {
            dispatch({ type: "SSE_STATUS", status: "connected" });
            return;
          }
          if (message.event === "reset") {
            // Server retained tail is the new truth: clear the baseline and
            // refetch the snapshot so the reducer reinitializes from it, then
            // the replayed events that follow are appended by id.
            initializedRunIdRef.current = null;
            dispatch({ type: "RESET_EVENTS", events: [] });
            queryClient.invalidateQueries({ queryKey: ["run", runId] });
            return;
          }
          if (message.event === "end") {
            dispatch({ type: "SSE_STATUS", status: "idle" });
            queryClient.invalidateQueries({ queryKey: ["run", runId] });
            return;
          }
          if (message.id === undefined) return;
          const event = message.data as ToolEvent;
          if (!event || typeof event.id !== "number") return;
          dispatch({ type: "APPEND", events: [event] });
        },
        onError: () => {
          if (disposed) return;
          dispatch({ type: "SSE_STATUS", status: "disconnected" });
          reconnectTimer = setTimeout(connect, 1500);
        },
      }).catch(() => {
        if (disposed) return;
        dispatch({ type: "SSE_STATUS", status: "disconnected" });
        reconnectTimer = setTimeout(connect, 1500);
      });
    };
    connect();

    return () => {
      disposed = true;
      controller.abort();
      if (reconnectTimer) clearTimeout(reconnectTimer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId, snapshot?.state]);

  const startRun = useCallback(
    async (request: { workspace: string; task: string; profileId: string | null }) => {
      const snap = await startRunMutation.mutateAsync({
        workspace: request.workspace,
        task: request.task,
        profile_id: request.profileId,
      });
      return snap;
    },
    [startRunMutation]
  );

  const cancelRun = useCallback(async () => {
    if (!runId) return;
    await cancelRunMutation.mutateAsync();
  }, [cancelRunMutation, runId]);

  const clear = useCallback(() => {
    runIdRef.current = null;
    initializedRunIdRef.current = null;
    dispatch({ type: "CLEAR" });
    queryClient.invalidateQueries({ queryKey: ["bootstrap"] });
  }, [queryClient]);

  const refetchSnapshot = useCallback(() => {
    if (runId) queryClient.invalidateQueries({ queryKey: ["run", runId] });
  }, [queryClient, runId]);

  const value = useMemo<RunStore>(
    () => ({
      runId,
      snapshot,
      snapshotLoading,
      events: state.events,
      sseStatus: state.sseStatus,
      cancelling: state.cancelling,
      startRun,
      cancelRun,
      clear,
      refetchSnapshot,
    }),
    [runId, snapshot, snapshotLoading, state.events, state.sseStatus, state.cancelling, startRun, cancelRun, clear, refetchSnapshot]
  );

  return <RunStoreContext.Provider value={value}>{children}</RunStoreContext.Provider>;
}

export function useRunStore(): RunStore {
  const ctx = useContext(RunStoreContext);
  if (!ctx) throw new Error("useRunStore must be used inside RunStoreProvider");
  return ctx;
}
