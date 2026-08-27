// fetch-based SSE consumer for the run event stream.
// Uses fetch (not EventSource) so we can attach the session-independent
// read-only GET and pass a `last_event_id` baseline; the server protocol:
//   event: hello   (data: {run_id, state, last_event_id, client_last_event_id})
//   event: reset   (client must replace its event list with the events that
//                   follow, and refetch the snapshot)
//   event: <kind>  (data: ToolEventDTO; id: <sequence>)
//   event: end     (stream finished)
export type SseEvent = { event: string; id?: number; data: unknown };

export interface SubscribeOptions {
  lastEventId?: number | null;
  signal: AbortSignal;
  onEvent: (message: SseEvent) => void;
  onError: (error: Error) => void;
}

function parseBlock(block: string): SseEvent | null {
  let event = "message";
  let id: number | undefined;
  const dataLines: string[] = [];
  for (const line of block.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("id:")) {
      const parsed = Number(line.slice(3).trim());
      if (Number.isFinite(parsed)) id = parsed;
    } else if (line.startsWith("data:")) dataLines.push(line.slice(5).trimStart());
  }
  if (!dataLines.length) return null;
  let data: unknown = null;
  const raw = dataLines.join("\n");
  try {
    data = JSON.parse(raw);
  } catch {
    data = raw;
  }
  return { event, id, data };
}

export async function subscribeToRunEvents(
  runId: string,
  options: SubscribeOptions
): Promise<void> {
  const query = options.lastEventId
    ? `?last_event_id=${encodeURIComponent(String(options.lastEventId))}`
    : "";
  const response = await fetch(`/api/runs/${encodeURIComponent(runId)}/events${query}`, {
    headers: { Accept: "text/event-stream" },
    signal: options.signal,
  });
  if (!response.ok || !response.body) {
    throw new Error(`SSE connect failed: HTTP ${response.status}`);
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let receivedEnd = false;
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let index: number;
      while ((index = buffer.indexOf("\n\n")) !== -1) {
        const block = buffer.slice(0, index);
        buffer = buffer.slice(index + 2);
        if (!block.trim()) continue;
        const message = parseBlock(block);
        if (message) {
          if (message.event === "end") receivedEnd = true;
          options.onEvent(message);
        }
      }
    }
    // A clean EOF without the server's `end` event means the connection
    // dropped; let the caller enter the disconnected/reconnect state.
    if (!options.signal.aborted && !receivedEnd) {
      options.onError(new Error("SSE stream ended without terminal event"));
    }
  } finally {
    reader.releaseLock();
  }
}
