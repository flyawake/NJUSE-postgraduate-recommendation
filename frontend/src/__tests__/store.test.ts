import { describe, expect, it } from "vitest";
import { deriveLiveSnapshot, runReducer, type RunStoreState } from "@/lib/store";
import type { RunSnapshot, ToolEvent } from "@/api/client";

function state(overrides: Partial<RunStoreState> = {}): RunStoreState {
  return {
    events: [],
    appendedEvents: [],
    eventResetVersion: 0,
    eventVersion: 0,
    lastEventId: 0,
    sseStatus: "idle",
    cancelling: false,
    ...overrides,
  };
}

function event(id: number): ToolEvent {
  return {
    id,
    kind: "step_started",
    step: 1,
    phase: "REQUESTING_MODEL",
    payload: { char_count: 10, budget: 120000 },
  };
}

describe("runReducer", () => {
  it("APPEND dedupes by event id and advances lastEventId", () => {
    const base = state({ events: [event(1), event(2)], lastEventId: 2 });
    const next = runReducer(base, { type: "APPEND", events: [event(2), event(3)] });
    expect(next.events.map((e) => e.id)).toEqual([1, 2, 3]);
    expect(next.lastEventId).toBe(3);
    expect(next.sseStatus).toBe("connected");
    expect(next.appendedEvents.map((e) => e.id)).toEqual([3]);
    expect(next.eventVersion).toBe(1);
  });

  it("accumulates all SSE callbacks batched before the feed acknowledges them", () => {
    const afterFirst = runReducer(state(), { type: "APPEND", events: [event(1)] });
    const afterSecond = runReducer(afterFirst, { type: "APPEND", events: [event(2)] });
    expect(afterSecond.appendedEvents.map((item) => item.id)).toEqual([1, 2]);
    expect(runReducer(afterSecond, { type: "ACK_APPEND", version: 1 }).appendedEvents).toHaveLength(2);
    expect(runReducer(afterSecond, { type: "ACK_APPEND", version: 2 }).appendedEvents).toEqual([]);
  });

  it("RESET_EVENTS replaces with the sorted server tail", () => {
    const base = state({ events: [event(1), event(2)], lastEventId: 2 });
    const next = runReducer(base, {
      type: "RESET_EVENTS",
      events: [event(5), event(3), event(4)],
    });
    expect(next.events.map((e) => e.id)).toEqual([3, 4, 5]);
    expect(next.lastEventId).toBe(5);
    expect(next.appendedEvents).toEqual([]);
    expect(next.eventResetVersion).toBe(1);
  });

  it("RESET_EVENTS clears stale events and the baseline on server tail reset", () => {
    const base = state({ events: [event(1)], lastEventId: 1 });
    const next = runReducer(base, { type: "RESET_EVENTS", events: [] });
    expect(next.events).toEqual([]);
    expect(next.lastEventId).toBe(0);
  });

  it("CANCEL_FAILED restores the cancelling flag so cancel can be retried", () => {
    const base = state({ cancelling: true });
    const next = runReducer(base, { type: "CANCEL_FAILED" });
    expect(next.cancelling).toBe(false);
  });

  it("client event list stays bounded at MAX_CLIENT_EVENTS", () => {
    const many = Array.from({ length: 2050 }, (_, index) => event(index + 1));
    const base = state();
    const next = runReducer(base, { type: "RESET_EVENTS", events: many });
    expect(next.events.length).toBe(2000);
    expect(next.events[0].id).toBe(51);
  });
});

describe("deriveLiveSnapshot", () => {
  it("advances inspector facts to the newest SSE event over a stale snapshot", () => {
    const snapshot: RunSnapshot = {
      run_id: "r1",
      state: "running",
      phase: "EXECUTING_TOOLS",
      step_count: 1,
      provider_attempt_count: 1,
      tool_call_count: 1,
      events_total: 4,
      events_retained_from: 1,
      events: [
        { id: 4, kind: "tool_finished", step: 1, phase: "EXECUTING_TOOLS", payload: {} },
      ],
    };
    const events: ToolEvent[] = [
      { id: 1, kind: "run_started", step: 0, phase: "READY", payload: {} },
      { id: 2, kind: "step_started", step: 1, phase: "READY", payload: {} },
      { id: 3, kind: "tool_started", step: 1, phase: "EXECUTING_TOOLS", payload: {} },
      { id: 4, kind: "tool_finished", step: 1, phase: "EXECUTING_TOOLS", payload: {} },
      { id: 5, kind: "step_started", step: 2, phase: "READY", payload: {} },
    ];

    const live = deriveLiveSnapshot(snapshot, events);
    expect(live?.step_count).toBe(2);
    expect(live?.provider_attempt_count).toBe(2);
    expect(live?.tool_call_count).toBe(1);
    expect(live?.phase).toBe("REQUESTING_MODEL");
  });

  it("never moves server counters backwards when the retained tail is partial", () => {
    const snapshot: RunSnapshot = {
      run_id: "r1",
      state: "running",
      phase: "REQUESTING_MODEL",
      step_count: 8,
      provider_attempt_count: 10,
      tool_call_count: 7,
      events_total: 40,
      events_retained_from: 39,
      events: [],
    };
    const live = deriveLiveSnapshot(snapshot, [event(40)]);
    expect(live?.step_count).toBe(8);
    expect(live?.provider_attempt_count).toBe(10);
    expect(live?.tool_call_count).toBe(7);
  });
});
