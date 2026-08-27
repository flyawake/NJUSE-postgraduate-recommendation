import { act, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import type { RunSnapshot, ToolEvent } from "@/api/client";
import type { SubscribeOptions } from "@/lib/sse";

const mocks = vi.hoisted(() => ({
  bootstrap: vi.fn(),
  getRun: vi.fn(),
  startRun: vi.fn(),
  cancelRun: vi.fn(),
  subscribe: vi.fn(),
}));

vi.mock("@/api/client", () => ({
  api: {
    bootstrap: mocks.bootstrap,
    getRun: mocks.getRun,
    startRun: mocks.startRun,
    cancelRun: mocks.cancelRun,
  },
}));

vi.mock("@/lib/sse", () => ({ subscribeToRunEvents: mocks.subscribe }));

import { RunStoreProvider, useRunStore } from "@/lib/store";

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

function event(id: number, kind: string, step: number): ToolEvent {
  return { id, kind, step, phase: "READY", payload: {} };
}

function snapshot(events: ToolEvent[]): RunSnapshot {
  return {
    run_id: "r1",
    state: "running",
    phase: "EXECUTING_TOOLS",
    step_count: 1,
    provider_attempt_count: 1,
    tool_call_count: 1,
    events_total: events.at(-1)?.id ?? 0,
    events_retained_from: events.at(0)?.id ?? 0,
    events,
  };
}

function Probe() {
  const store = useRunStore();
  return (
    <div>
      <span data-testid="ids">{store.events.map((item) => item.id).join(",")}</span>
      <span data-testid="facts">
        {store.snapshot?.step_count}/{store.snapshot?.provider_attempt_count}/
        {store.snapshot?.tool_call_count}/{store.snapshot?.phase}
      </span>
    </div>
  );
}

function Providers({ children }: { children: ReactNode }) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return (
    <QueryClientProvider client={queryClient}>
      <RunStoreProvider>{children}</RunStoreProvider>
    </QueryClientProvider>
  );
}

describe("RunStore reset and reconnect monotonicity", () => {
  beforeEach(() => {
    vi.useRealTimers();
    vi.clearAllMocks();
  });

  it("keeps replayed SSE events over a slower snapshot and reconnects from the newest id", async () => {
    const initial = snapshot([
      event(1, "run_started", 0),
      event(2, "step_started", 1),
      event(3, "tool_started", 1),
      event(4, "tool_finished", 1),
    ]);
    const staleRefetch = deferred<RunSnapshot>();
    const streams: SubscribeOptions[] = [];

    mocks.bootstrap.mockResolvedValue({
      active_profile_id: null,
      capabilities: { char_budget: 120000, max_steps: 20, wire_apis: [] },
      profiles: [],
      provider_presets: [],
      run: initial,
      server_version: "test",
      session_token: "token",
      state: "running",
      ui: { locale: "zh-CN", theme: "system" },
    });
    mocks.getRun
      .mockResolvedValueOnce(initial)
      .mockImplementationOnce(() => staleRefetch.promise);
    mocks.subscribe.mockImplementation(
      (_runId: string, options: SubscribeOptions) => {
        streams.push(options);
        return new Promise<void>(() => undefined);
      }
    );

    render(<Probe />, { wrapper: Providers });
    await waitFor(() => expect(streams).toHaveLength(1));
    expect(streams[0].lastEventId).toBe(4);

    act(() => streams[0].onEvent({ event: "reset", data: {} }));
    await waitFor(() => expect(mocks.getRun).toHaveBeenCalledTimes(2));
    act(() => {
      streams[0].onEvent({
        event: "step_started",
        id: 5,
        data: event(5, "step_started", 2),
      });
    });
    expect(screen.getByTestId("ids")).toHaveTextContent("5");
    expect(screen.getByTestId("facts")).toHaveTextContent("2/2/1/REQUESTING_MODEL");

    await act(async () => staleRefetch.resolve(initial));
    expect(screen.getByTestId("ids")).toHaveTextContent("5");
    expect(screen.getByTestId("facts")).toHaveTextContent("2/2/1/REQUESTING_MODEL");

    vi.useFakeTimers();
    act(() => streams[0].onError(new Error("connection lost")));
    await act(async () => vi.advanceTimersByTimeAsync(1500));
    expect(streams).toHaveLength(2);
    expect(streams[1].lastEventId).toBe(5);
    vi.useRealTimers();
  });
});
