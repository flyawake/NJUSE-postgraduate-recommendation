import { describe, expect, it } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { I18nProvider } from "@/lib/i18n";
import { ActivityFeed } from "@/components/ActivityFeed";
import type { ToolEvent } from "@/api/client";

function event(id: number, kind: string, payload: Record<string, unknown>): ToolEvent {
  const { target, ...safePayload } = payload;
  return {
    id,
    kind,
    step: 1,
    phase: "EXECUTING_TOOLS",
    target: typeof target === "string" ? target : null,
    payload: safePayload,
  };
}

function feedElement(events: ToolEvent[], resetVersion = 0, task = "检查项目") {
  return (
    <I18nProvider>
      <div className="h-[640px]">
        <ActivityFeed
          retainedEvents={events}
          eventBatch={[]}
          resetVersion={resetVersion}
          eventVersion={0}
          task={task}
          state="running"
          sseStatus="connected"
        />
      </div>
    </I18nProvider>
  );
}

function renderFeed(events: ToolEvent[]) {
  return render(feedElement(events));
}

describe("ActivityFeed", () => {
  it("renders a continuous action flow with a bounded 2,000-event DOM window", () => {
    const events: ToolEvent[] = [event(1, "run_started", {})];
    for (let index = 0; index < 999; index += 1) {
      const callId = `call-${index}`;
      events.push(event(index * 2 + 2, "tool_started", {
        call_id: callId,
        name: "read_file",
        arguments: JSON.stringify({ path: `src/${index}.py` }),
        target: `src/${index}.py`,
      }));
      events.push(event(index * 2 + 3, "tool_finished", {
        call_id: callId,
        ok: true,
        error_code: null,
        summary: "read ok",
      }));
    }

    const { container } = renderFeed(events);
    expect(container.querySelectorAll("[data-tool]")).toHaveLength(300);
    expect(screen.getByTestId("load-older-activity")).toBeInTheDocument();
    expect(screen.getAllByText("读取了").at(-1)?.parentElement).toHaveTextContent("src/998.py");

    fireEvent.click(screen.getByTestId("load-older-activity"));
    expect(container.querySelectorAll("[data-tool]")).toHaveLength(500);
    expect(screen.getAllByText("读取了").at(0)?.parentElement).toHaveTextContent("src/499.py");
  });

  it("uses a single shallow action row whose details are keyboard-accessible", async () => {
    const { container } = renderFeed([
      event(1, "run_started", {}),
      event(2, "tool_started", { call_id: "one", name: "grep", arguments: '{"pattern":"TODO"}', target: "TODO" }),
      event(3, "tool_finished", { call_id: "one", ok: false, error_code: "INVALID_ARGUMENT", summary: "bad pattern" }),
    ]);

    const row = container.querySelector('[data-tool="grep"] button');
    expect(row).toHaveAttribute("aria-expanded", "false");
    const user = userEvent.setup();
    (row as HTMLButtonElement).focus();
    await user.keyboard("{Enter}");
    expect(screen.getByText("参数摘要")).toBeInTheDocument();
    expect(screen.getByText("操作未成功；请查看详情并根据结果调整任务后重试。")).toBeInTheDocument();
  });

  it("resets an expanded history window to 300 rows on the first reset frame", () => {
    const events: ToolEvent[] = [event(1, "run_started", {})];
    for (let index = 0; index < 999; index += 1) {
      events.push(event(index * 2 + 2, "tool_started", {
        call_id: `old-${index}`,
        name: "read_file",
        arguments: "{}",
        target: `old/${index}.py`,
      }));
      events.push(event(index * 2 + 3, "tool_finished", {
        call_id: `old-${index}`,
        ok: true,
        summary: "ok",
      }));
    }
    const { container, rerender } = render(feedElement(events));
    fireEvent.click(screen.getByTestId("load-older-activity"));
    expect(container.querySelectorAll("[data-tool]")).toHaveLength(500);

    const nextEvents = events.map((item) => ({
      ...item,
      payload: item.kind === "tool_started"
        ? { ...item.payload, call_id: `new-${String(item.payload.call_id ?? "")}` }
        : item.kind === "tool_finished"
          ? { ...item.payload, call_id: `new-${String(item.payload.call_id ?? "")}` }
          : item.payload,
    }));
    rerender(feedElement(nextEvents, 1, "新的任务"));
    expect(container.querySelectorAll("[data-tool]")).toHaveLength(300);
  });
});
