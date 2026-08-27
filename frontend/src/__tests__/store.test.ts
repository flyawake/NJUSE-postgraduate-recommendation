import { describe, expect, it } from "vitest";
import { runReducer, type RunStoreState } from "@/lib/store";
import type { ToolEvent } from "@/api/client";

function state(overrides: Partial<RunStoreState> = {}): RunStoreState {
  return {
    events: [],
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
  });

  it("RESET_EVENTS replaces with the sorted server tail", () => {
    const base = state({ events: [event(1), event(2)], lastEventId: 2 });
    const next = runReducer(base, {
      type: "RESET_EVENTS",
      events: [event(5), event(3), event(4)],
    });
    expect(next.events.map((e) => e.id)).toEqual([3, 4, 5]);
    expect(next.lastEventId).toBe(5);
  });

  it("RESET_EVENTS clears stale events on server tail reset", () => {
    const base = state({ events: [event(1)], lastEventId: 1 });
    const next = runReducer(base, { type: "RESET_EVENTS", events: [] });
    expect(next.events).toEqual([]);
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
