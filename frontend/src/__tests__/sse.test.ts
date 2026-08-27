import { describe, expect, it, vi } from "vitest";
import { subscribeToRunEvents } from "@/lib/sse";

function streamResponse(text: string) {
  const encoded = new TextEncoder().encode(text);
  let delivered = false;
  return {
    ok: true,
    status: 200,
    body: {
      getReader: () => ({
        read: async () => {
          if (!delivered) {
            delivered = true;
            return { done: false, value: encoded };
          }
          return { done: true, value: undefined };
        },
        releaseLock: () => undefined,
      }),
    },
  } as Response;
}

describe("subscribeToRunEvents", () => {
  it("uses the supplied cursor and treats explicit end + EOF as terminal", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      streamResponse(
        'event: hello\ndata: {"state":"running"}\n\n' +
          'event: end\nid: 42\ndata: {"message":"run finished"}\n\n'
      )
    );
    const onEvent = vi.fn();
    const onError = vi.fn();

    await subscribeToRunEvents("run id", {
      lastEventId: 42,
      signal: new AbortController().signal,
      onEvent,
      onError,
    });

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/runs/run%20id/events?last_event_id=42",
      expect.any(Object)
    );
    expect(onEvent.mock.calls.map(([message]) => message.event)).toEqual(["hello", "end"]);
    expect(onError).not.toHaveBeenCalled();
  });

  it("reports a clean EOF as disconnected when no end event arrived", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      streamResponse('event: hello\ndata: {"state":"running"}\n\n')
    );
    const onError = vi.fn();

    await subscribeToRunEvents("r1", {
      signal: new AbortController().signal,
      onEvent: vi.fn(),
      onError,
    });

    expect(onError).toHaveBeenCalledOnce();
  });
});
