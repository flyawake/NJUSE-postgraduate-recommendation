import { describe, expect, it } from "vitest";
import type { ToolEvent } from "@/api/client";
import { mergeEventTail } from "@/components/ConversationView";

function item(id: number): ToolEvent {
  return {
    id,
    kind: "assistant_text_delta",
    step: 1,
    phase: "REQUESTING_MODEL",
    payload: { attempt: 1, delta: String(id) },
  };
}

describe("conversation event merge", () => {
  it("does not drop a polling batch that resolves after a newer SSE event", () => {
    const sseFirst = [item(501)];
    const delayedPoll = Array.from({ length: 500 }, (_, index) => item(index + 1));
    const merged = mergeEventTail(sseFirst, delayedPoll);
    expect(merged).toHaveLength(501);
    expect(merged[0].id).toBe(1);
    expect(merged.at(-1)?.id).toBe(501);
  });

  it("deduplicates reconnect replay and enforces the retained tail bound", () => {
    const original = Array.from({ length: 2_100 }, (_, index) => item(index + 1));
    const replay = [item(1_999), item(2_000), item(2_101)];
    let merged = original;
    for (let reconnect = 0; reconnect < 3; reconnect += 1) {
      merged = mergeEventTail(merged, replay);
    }
    expect(merged).toHaveLength(2_000);
    expect(merged[0].id).toBe(102);
    expect(merged.at(-1)?.id).toBe(2_101);
    expect(new Set(merged.map((event) => event.id)).size).toBe(2_000);
  });
});
