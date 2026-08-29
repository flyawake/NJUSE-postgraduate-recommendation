import { describe, expect, it } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { InboxSnapshot } from "@/api/client";
import { QueueDock } from "@/components/QueueDock";
import { I18nProvider } from "@/lib/i18n";

function snapshotWith(count: number): InboxSnapshot {
  return {
    queue_version: 1,
    recent_events: [],
    items: Array.from({ length: count }, (_, index) => ({
      id: `item-${index}`,
      conversation_id: "conversation-1",
      content: `队列项 ${index + 1}`,
      requested_mode: "queue",
      state: "queued",
      position: index + 1,
      bound_turn_id: null,
      claimed_turn_id: null,
      idempotency_key: null,
      reasoning_effort: null,
      version: 1,
      last_error_code: null,
      created_at: "2026-08-29T00:00:00+00:00",
      updated_at: "2026-08-29T00:00:00+00:00",
      claimed_at: null,
      delivered_at: null,
    })),
  };
}

function renderDock(snapshot: InboxSnapshot) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <I18nProvider>
        <QueueDock conversationId="conversation-1" snapshot={snapshot} />
      </I18nProvider>
    </QueryClientProvider>
  );
}

describe("QueueDock long queue window", () => {
  it("keeps the collapsed and expanded DOM bounded while every item stays reachable", async () => {
    const user = userEvent.setup();
    renderDock(snapshotWith(100));

    expect(screen.getAllByTestId(/^queue-item-/)).toHaveLength(3);
    await user.click(screen.getByRole("button", { name: "队列（100）" }));
    expect(screen.getAllByTestId(/^queue-item-/)).toHaveLength(50);
    expect(screen.getByText("队列项 1")).toBeInTheDocument();
    expect(screen.queryByText("队列项 51")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "显示下一组" }));
    expect(screen.getAllByTestId(/^queue-item-/)).toHaveLength(50);
    expect(screen.queryByText("队列项 1")).not.toBeInTheDocument();
    expect(screen.getByText("队列项 51")).toBeInTheDocument();
  });
});
