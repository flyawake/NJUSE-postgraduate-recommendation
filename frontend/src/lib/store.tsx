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
  appendedEvents: ToolEvent[];
  eventResetVersion: number;
  eventVersion: number;
  lastEventId: number;
  sseStatus: SseStatus;
  cancelling: boolean;
}

const MAX_CLIENT_EVENTS = 2000;

const LIVE_PHASE_AFTER_EVENT: Record<string, string> = {
  run_started: "READY",
  step_started: "REQUESTING_MODEL",
  model_retry: "REQUESTING_MODEL",
  assistant_received: "HANDLING_RESPONSE",
  tool_started: "EXECUTING_TOOLS",
  tool_finished: "EXECUTING_TOOLS",
  plan_updated: "EXECUTING_TOOLS",
  plan_completion_deferred: "READY",
  step_budget_extended: "READY",
  step_budget_finalizing: "READY",
  completion_deferred: "READY",
  run_finished: "TERMINAL",
};

const initialState: RunStoreState = {
  events: [],
  appendedEvents: [],
  eventResetVersion: 0,
  eventVersion: 0,
  lastEventId: 0,
  sseStatus: "idle",
  cancelling: false,
};

type Action =
  | { type: "APPEND"; events: ToolEvent[] }
  | { type: "ACK_APPEND"; version: number }
  | { type: "RESET_EVENTS"; events: ToolEvent[] }
  | { type: "SSE_STATUS"; status: SseStatus }
  | { type: "CANCEL_REQUESTED" }
  | { type: "CANCEL_FAILED" }
  | { type: "CLEAR" };

export function runReducer(state: RunStoreState, action: Action): RunStoreState {
  switch (action.type) {
    case "APPEND": {
      const next: ToolEvent[] = [];
      for (const event of action.events) {
        if (event.id > state.lastEventId) next.push(event);
      }
      if (!next.length) return { ...state, sseStatus: "connected" };
      const merged = [...state.events, ...next];
      const trimmed = merged.length > MAX_CLIENT_EVENTS ? merged.slice(-MAX_CLIENT_EVENTS) : merged;
      return {
        ...state,
        events: trimmed,
        // React may batch several SSE callbacks into one commit. Retain all
        // unseen callback batches until the feed has rendered this version;
        // keeping only `next` here silently loses intermediate tool events.
        appendedEvents: [...state.appendedEvents, ...next],
        eventVersion: state.eventVersion + 1,
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
        appendedEvents: [],
        eventResetVersion: state.eventResetVersion + 1,
        eventVersion: state.eventVersion + 1,
        // An empty reset must clear the baseline too: otherwise replayed
        // events with ids <= the old lastEventId would be filtered out.
        lastEventId: trimmed.length ? trimmed[trimmed.length - 1].id : 0,
        sseStatus: "connected",
      };
    }
    case "ACK_APPEND":
      // An acknowledgement from an older rendered frame must not clear an
      // event that arrived after that frame was committed.
      if (action.version !== state.eventVersion || state.appendedEvents.length === 0) return state;
      return { ...state, appendedEvents: [] };
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

/**
 * Reconcile the latest SSE facts over a possibly older polling snapshot.
 * Counts use max(snapshot, retained events), so a bounded client tail never
 * moves a counter backwards. Phase follows an event only when that event is
 * at least as new as the snapshot's own retained tail.
 */
export function deriveLiveSnapshot(
  snapshot: RunSnapshot | undefined,
  events: ToolEvent[]
): RunSnapshot | undefined {
  if (!snapshot || snapshot.state !== "running" || events.length === 0) return snapshot;

  const stepCount = events.reduce(
    (count, event) => event.kind === "step_started" ? Math.max(count, event.step) : count,
    0
  );
  // events_total is the server's monotonic sequence baseline. Only events
  // newer than that snapshot should be added to its aggregate counters;
  // counting a retained tail from zero undercounts after reset and double
  // counting the whole tail overcounts after polling.
  const newerEvents = events.filter((event) => event.id > snapshot.events_total);
  const providerAttempts = snapshot.provider_attempt_count + newerEvents.reduce(
    (count, event) => count + (event.kind === "step_started" || event.kind === "model_retry" ? 1 : 0),
    0
  );
  const toolCalls = snapshot.tool_call_count + newerEvents.reduce(
    (count, event) => count + (event.kind === "tool_started" ? 1 : 0),
    0
  );
  const lastEvent = events[events.length - 1];
  const eventPhase = lastEvent.id > snapshot.events_total
    ? LIVE_PHASE_AFTER_EVENT[lastEvent.kind]
    : undefined;

  return {
    ...snapshot,
    step_count: Math.max(snapshot.step_count, stepCount),
    provider_attempt_count: Math.max(snapshot.provider_attempt_count, providerAttempts),
    tool_call_count: Math.max(snapshot.tool_call_count, toolCalls),
    phase: eventPhase ?? snapshot.phase,
  };
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

export interface RunCommands {
  startRun: RunStore["startRun"];
  cancelRun: RunStore["cancelRun"];
  clear: RunStore["clear"];
  refetchSnapshot: RunStore["refetchSnapshot"];
}

export interface RunMeta {
  runId: string | null;
  snapshot?: RunSnapshot;
  snapshotLoading: boolean;
  sseStatus: SseStatus;
  cancelling: boolean;
}

/**
 * High-frequency event boundary. `appendedEvents` is the exact new SSE
 * batch; retainedEvents is consulted only after a reset/recovery.
 */
export interface RunEventFeed {
  retainedEvents: ToolEvent[];
  appendedEvents: ToolEvent[];
  resetVersion: number;
  version: number;
}

const RunCommandsContext = createContext<RunCommands | null>(null);
const RunMetaContext = createContext<RunMeta | null>(null);
const RunEventsContext = createContext<RunEventFeed | null>(null);
const RunStoreContext = createContext<RunStore | null>(null);

export function RunStoreProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [state, dispatch] = useReducer(runReducer, initialState);
  const runIdRef = useRef<string | null>(null);
  const initializedRunIdRef = useRef<string | null>(null);
  const lastEventIdRef = useRef(0);

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
    if (!snapshot) return;
    const snapshotEvents = snapshot.events ?? [];
    lastEventIdRef.current = snapshotEvents.length
      ? snapshotEvents[snapshotEvents.length - 1].id
      : 0;
    dispatch({ type: "RESET_EVENTS", events: snapshotEvents });
    initializedRunIdRef.current = runId;
  }, [runId, snapshot]);

  const startRunMutation = useMutation({
    mutationFn: api.startRun,
    onSuccess: (snap) => {
      runIdRef.current = snap.run_id;
      initializedRunIdRef.current = null;
      lastEventIdRef.current = 0;
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
  const startMutationRef = useRef(startRunMutation.mutateAsync);
  const cancelMutationRef = useRef(cancelRunMutation.mutateAsync);
  startMutationRef.current = startRunMutation.mutateAsync;
  cancelMutationRef.current = cancelRunMutation.mutateAsync;

  // SSE subscription lifecycle. A synchronous ref tracks the newest accepted
  // id so a reconnect cannot capture an old reducer render in its closure.
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
        lastEventId: lastEventIdRef.current || null,
        signal: controller.signal,
        onEvent: (message) => {
          if (message.event === "hello") {
            dispatch({ type: "SSE_STATUS", status: "connected" });
            return;
          }
          if (message.event === "reset") {
            // Replay on this stream is the event truth. The snapshot refetch
            // refreshes metadata only; keeping this run initialized prevents
            // a slower HTTP response from overwriting newer replayed events.
            initializedRunIdRef.current = runId;
            lastEventIdRef.current = 0;
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
          if (event.id > lastEventIdRef.current) lastEventIdRef.current = event.id;
          dispatch({ type: "APPEND", events: [event] });
          if (event.kind === "run_finished") {
            queryClient.invalidateQueries({ queryKey: ["run", runId] });
          }
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
      const snap = await startMutationRef.current({
        workspace: request.workspace,
        task: request.task,
        profile_id: request.profileId,
      });
      return snap;
    },
    []
  );

  const cancelRun = useCallback(async () => {
    if (!runId) return;
    await cancelMutationRef.current();
  }, [runId]);

  const clear = useCallback(() => {
    runIdRef.current = null;
    initializedRunIdRef.current = null;
    lastEventIdRef.current = 0;
    dispatch({ type: "CLEAR" });
    queryClient.invalidateQueries({ queryKey: ["bootstrap"] });
  }, [queryClient]);

  const refetchSnapshot = useCallback(() => {
    if (runId) queryClient.invalidateQueries({ queryKey: ["run", runId] });
  }, [queryClient, runId]);

  const commands = useMemo<RunCommands>(
    () => ({ startRun, cancelRun, clear, refetchSnapshot }),
    [startRun, cancelRun, clear, refetchSnapshot]
  );

  const meta = useMemo<RunMeta>(
    () => ({
      runId,
      // Snapshot polling is intentionally a low-frequency boundary. Event
      // consumers receive the append-only tail through RunEventsContext;
      // their updates must not redraw the workspace, profile or composer.
      snapshot,
      snapshotLoading,
      sseStatus: state.sseStatus,
      cancelling: state.cancelling,
    }),
    [runId, snapshot, snapshotLoading, state.sseStatus, state.cancelling]
  );

  const value = useMemo<RunStore>(
    () => ({
      ...meta,
      // The legacy aggregate store deliberately exposes the polling snapshot
      // as-is. Hot SSE updates are consumed through RunEventFeed below, so
      // this provider never scans a 2,000-event tail on each append.
      snapshot,
      events: state.events,
      ...commands,
    }),
    [meta, snapshot, state.events, commands]
  );

  const eventFeed = useMemo<RunEventFeed>(
    () => ({
      retainedEvents: state.events,
      appendedEvents: state.appendedEvents,
      resetVersion: state.eventResetVersion,
      version: state.eventVersion,
    }),
    [state.events, state.appendedEvents, state.eventResetVersion, state.eventVersion]
  );

  // Clear a delivered batch only after React has committed it to the event
  // consumer. This keeps the hot path O(new batch) even when the browser
  // batches several EventSource messages into one render.
  useEffect(() => {
    if (!state.appendedEvents.length) return;
    dispatch({ type: "ACK_APPEND", version: state.eventVersion });
  }, [state.appendedEvents, state.eventVersion]);

  return (
    <RunCommandsContext.Provider value={commands}>
      <RunMetaContext.Provider value={meta}>
        <RunEventsContext.Provider value={eventFeed}>
          <RunStoreContext.Provider value={value}>{children}</RunStoreContext.Provider>
        </RunEventsContext.Provider>
      </RunMetaContext.Provider>
    </RunCommandsContext.Provider>
  );
}

export function useRunStore(): RunStore {
  const ctx = useContext(RunStoreContext);
  if (!ctx) throw new Error("useRunStore must be used inside RunStoreProvider");
  return ctx;
}

export function useRunCommands(): RunCommands {
  const ctx = useContext(RunCommandsContext);
  if (!ctx) throw new Error("useRunCommands must be used inside RunStoreProvider");
  return ctx;
}

export function useRunMeta(): RunMeta {
  const ctx = useContext(RunMetaContext);
  if (!ctx) throw new Error("useRunMeta must be used inside RunStoreProvider");
  return ctx;
}

export function useRunEvents(): ToolEvent[] {
  const ctx = useContext(RunEventsContext);
  if (!ctx) throw new Error("useRunEvents must be used inside RunStoreProvider");
  return ctx.retainedEvents;
}

export function useRunEventFeed(): RunEventFeed {
  const ctx = useContext(RunEventsContext);
  if (!ctx) throw new Error("useRunEventFeed must be used inside RunStoreProvider");
  return ctx;
}
