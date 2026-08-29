import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { StreamingTranscript } from "@/components/StreamingTranscript";
import { I18nProvider } from "@/lib/i18n";
import type { StreamCheckpoint, ToolEvent } from "@/api/client";

function event(
  id: number,
  kind: string,
  payload: Record<string, unknown>
): ToolEvent {
  return { id, kind, step: 1, phase: "REQUESTING_MODEL", payload };
}

function renderTranscript(events: ToolEvent[]) {
  return render(
    <I18nProvider>
      <StreamingTranscript events={events} />
    </I18nProvider>
  );
}

describe("StreamingTranscript", () => {
  it("renders a Think block from reasoning deltas", () => {
    renderTranscript([
      event(1, "model_stream_started", { attempt: 1 }),
      event(2, "reasoning_delta", { attempt: 1, delta: "visible thought" }),
      event(3, "assistant_text_delta", { attempt: 1, delta: "answer" }),
    ]);
    const block = screen.getByTestId("think-block");
    expect(block).toBeVisible();
    fireEvent.click(block.querySelector("button") as HTMLElement);
    expect(block).toHaveTextContent("visible thought");
    expect(screen.getByTestId("thinking-scroll-frame")).toHaveClass("bounded-detail");
    expect(screen.getByTestId("streaming-assistant-text")).toHaveTextContent("answer");
  });

  it("keeps DOM bounded for 2,000 small text deltas", () => {
    const events = Array.from({ length: 2_000 }, (_, index) =>
      event(index + 1, "assistant_text_delta", { attempt: 1, delta: "x" })
    );
    renderTranscript(events);
    expect(screen.getAllByTestId("streaming-assistant-text")).toHaveLength(1);
    expect(screen.getByTestId("streaming-assistant-text")).toHaveTextContent(
      "x".repeat(2_000)
    );
  });

  it("omits the Think shell when reasoning is absent", () => {
    renderTranscript([
      event(1, "model_stream_started", { attempt: 1 }),
      event(2, "assistant_text_delta", { attempt: 1, delta: "plain" }),
    ]);
    expect(screen.queryByTestId("think-block")).not.toBeInTheDocument();
    expect(screen.getByTestId("streaming-assistant-text")).toHaveTextContent("plain");
  });

  it("replays only events newer than each checkpoint cursor", () => {
    const snapshot: StreamCheckpoint[] = [
      {
        run_id: "run",
        attempt: 1,
        channel: "text",
        text: "Hello",
        char_count: 5,
        event_seq: 2,
        updated_at: "2026-01-01T00:00:00Z",
      },
      {
        run_id: "run",
        attempt: 1,
        channel: "reasoning",
        text: "Think",
        char_count: 5,
        event_seq: 3,
        updated_at: "2026-01-01T00:00:00Z",
      },
    ];
    render(
      <I18nProvider>
        <StreamingTranscript
          snapshot={snapshot}
          events={[
            event(2, "assistant_text_delta", { attempt: 1, delta: "Hello" }),
            event(3, "reasoning_delta", { attempt: 1, delta: "Think" }),
            event(4, "assistant_text_delta", { attempt: 1, delta: " world" }),
            event(5, "reasoning_delta", { attempt: 1, delta: " more" }),
          ]}
          defaultThinkOpen
        />
      </I18nProvider>
    );
    expect(screen.getByTestId("streaming-assistant-text")).toHaveTextContent("Hello world");
    expect(screen.getByTestId("think-block")).toHaveTextContent("Think more");
  });

  it("keeps attempts isolated and displays completed duration", () => {
    renderTranscript([
      event(1, "model_stream_started", { attempt: 1 }),
      event(2, "reasoning_delta", { attempt: 1, delta: "old" }),
      event(3, "stream_attempt_abandoned", { attempt: 1 }),
      event(4, "model_stream_started", { attempt: 2 }),
      event(5, "reasoning_summary_delta", { attempt: 2, delta: "new" }),
      event(6, "assistant_received", { attempt: 2, elapsed_ms: 1250 }),
    ]);
    expect(screen.getAllByTestId("think-block")).toHaveLength(2);
    expect(screen.getByText("思考了 1.3 秒")).toBeVisible();
    expect(screen.getAllByText("该次流式尝试已放弃").length).toBeGreaterThan(0);
  });
});
