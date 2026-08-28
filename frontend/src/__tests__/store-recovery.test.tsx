import { act, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { createElement, type ComponentProps, type ReactNode } from "react";
import type { RunSnapshot, ToolEvent } from "@/api/client";
import type { SubscribeOptions } from "@/lib/sse";
import { I18nProvider } from "@/lib/i18n";
import { MainPage } from "@/pages/MainPage";

const mocks = vi.hoisted(() => ({
  bootstrap: vi.fn(),
  getRun: vi.fn(),
  startRun: vi.fn(),
  cancelRun: vi.fn(),
  listProfiles: vi.fn(),
  validateWorkspace: vi.fn(),
  subscribe: vi.fn(),
  renders: { workspace: 0, profile: 0, composer: 0, feed: 0 },
}));

vi.mock("@/api/client", () => ({
  ApiError: class ApiError extends Error {},
  api: {
    bootstrap: mocks.bootstrap,
    getRun: mocks.getRun,
    startRun: mocks.startRun,
    cancelRun: mocks.cancelRun,
    listProfiles: mocks.listProfiles,
    validateWorkspace: mocks.validateWorkspace,
  },
}));

vi.mock("@/lib/sse", () => ({ subscribeToRunEvents: mocks.subscribe }));

// Render counters are test-module decorators. Product component contracts
// stay free of instrumentation-only props and therefore ship no probe code.
vi.mock("@/components/WorkspaceField", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/components/WorkspaceField")>();
  return {
    ...actual,
    WorkspaceField: (props: ComponentProps<typeof actual.WorkspaceField>) => {
      mocks.renders.workspace += 1;
      return createElement(actual.WorkspaceField, props);
    },
  };
});

vi.mock("@/components/ProfileSelector", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/components/ProfileSelector")>();
  return {
    ...actual,
    ProfileSelector: (props: ComponentProps<typeof actual.ProfileSelector>) => {
      mocks.renders.profile += 1;
      return createElement(actual.ProfileSelector, props);
    },
  };
});

vi.mock("@/components/TaskComposer", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/components/TaskComposer")>();
  return {
    ...actual,
    TaskComposer: (props: ComponentProps<typeof actual.TaskComposer>) => {
      mocks.renders.composer += 1;
      return createElement(actual.TaskComposer, props);
    },
  };
});

vi.mock("@/components/ActivityFeed", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/components/ActivityFeed")>();
  return {
    ...actual,
    ActivityFeed: (props: ComponentProps<typeof actual.ActivityFeed>) => {
      mocks.renders.feed += 1;
      return createElement(actual.ActivityFeed, props);
    },
  };
});

import { RunStoreProvider, useRunCommands, useRunEvents, useRunMeta, useRunStore } from "@/lib/store";

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
      <I18nProvider>
        <RunStoreProvider>{children}</RunStoreProvider>
      </I18nProvider>
    </QueryClientProvider>
  );
}

describe("RunStore reset and reconnect monotonicity", () => {
  beforeEach(() => {
    vi.useRealTimers();
    vi.clearAllMocks();
    mocks.renders.workspace = 0;
    mocks.renders.profile = 0;
    mocks.renders.composer = 0;
    mocks.renders.feed = 0;
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
    expect(screen.getByTestId("facts")).toHaveTextContent("1/1/1/EXECUTING_TOOLS");

    await act(async () => staleRefetch.resolve(initial));
    expect(screen.getByTestId("ids")).toHaveTextContent("5");
    expect(screen.getByTestId("facts")).toHaveTextContent("1/1/1/EXECUTING_TOOLS");

    vi.useFakeTimers();
    act(() => streams[0].onError(new Error("connection lost")));
    await act(async () => vi.advanceTimersByTimeAsync(1500));
    expect(streams).toHaveLength(2);
    expect(streams[1].lastEventId).toBe(5);
    vi.useRealTimers();
  });

  it("isolates workspace/profile-style meta consumers from 50 event appends", async () => {
    const initial = snapshot([event(1, "run_started", 0)]);
    const streams: SubscribeOptions[] = [];
    let metaRenders = 0;
    let commandRenders = 0;
    let eventRenders = 0;
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
    mocks.getRun.mockResolvedValue(initial);
    mocks.subscribe.mockImplementation((_runId: string, options: SubscribeOptions) => {
      streams.push(options);
      return new Promise<void>(() => undefined);
    });

    function MetaProbe() {
      useRunMeta();
      metaRenders += 1;
      return null;
    }
    function CommandProbe() {
      useRunCommands();
      commandRenders += 1;
      return null;
    }
    function EventProbe() {
      useRunEvents();
      eventRenders += 1;
      return null;
    }

    render(
      <>
        <MetaProbe />
        <CommandProbe />
        <EventProbe />
      </>,
      { wrapper: Providers }
    );
    await waitFor(() => expect(streams).toHaveLength(1));
    act(() => streams[0].onEvent({ event: "hello", data: {} }));
    const metaBefore = metaRenders;
    const commandsBefore = commandRenders;
    const eventsBefore = eventRenders;

    for (let id = 2; id <= 51; id += 1) {
      act(() => streams[0].onEvent({ event: "step_started", id, data: event(id, "step_started", id) }));
    }

    expect(metaRenders - metaBefore).toBe(0);
    expect(commandRenders - commandsBefore).toBe(0);
    // Each delivered batch gets one subsequent acknowledgement render. The
    // high-frequency boundary still owns both updates; meta/commands do not.
    expect(eventRenders - eventsBefore).toBe(100);
  });

  it("keeps actual workspace, profile and composer components stable while the ActivityFeed consumes 50 SSE batches", async () => {
    const initial = snapshot([event(1, "run_started", 0)]);
    const streams: SubscribeOptions[] = [];
    mocks.bootstrap.mockResolvedValue({
      active_profile_id: "p1",
      capabilities: { char_budget: 120000, max_steps: 20, wire_apis: [] },
      profiles: [],
      provider_presets: [],
      run: initial,
      server_version: "test",
      session_token: "token",
      state: "running",
      ui: { locale: "zh-CN", theme: "system" },
    });
    mocks.listProfiles.mockResolvedValue([
      {
        id: "p1",
        provider_id: "openai",
        display_name: "测试 Profile",
        wire_api: "openai_chat_completions",
        base_url: "http://127.0.0.1",
        model: "fake",
        credential: { configured: true, source: "local_file", writable: true },
      },
    ]);
    mocks.getRun.mockResolvedValue(initial);
    mocks.subscribe.mockImplementation((_runId: string, options: SubscribeOptions) => {
      streams.push(options);
      return new Promise<void>(() => undefined);
    });

    render(
      <MainPage
        workspace=""
        workspaceValid={null}
        onWorkspaceChange={() => undefined}
        onWorkspaceValidated={() => undefined}
        profileId={null}
        onProfileChange={() => undefined}
      />,
      { wrapper: Providers }
    );
    await waitFor(() => expect(streams).toHaveLength(1));
    act(() => streams[0].onEvent({ event: "hello", data: {} }));
    await waitFor(() => expect(mocks.renders.feed).toBeGreaterThan(0));
    await waitFor(() => expect(screen.getByText("测试 Profile")).toBeInTheDocument());
    const before = { ...mocks.renders };

    for (let id = 2; id <= 51; id += 1) {
      act(() => streams[0].onEvent({ event: "step_started", id, data: event(id, "step_started", id) }));
    }

    expect(mocks.renders.workspace - before.workspace).toBe(0);
    expect(mocks.renders.profile - before.profile).toBe(0);
    expect(mocks.renders.composer - before.composer).toBe(0);
    // One additional empty render per delivery is permitted for the
    // acknowledged batch cleanup; the other product controls remain stable.
    expect(mocks.renders.feed - before.feed).toBeGreaterThanOrEqual(50);
  });
});
