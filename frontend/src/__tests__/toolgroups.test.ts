import { describe, expect, it } from "vitest";
import { buildFeed } from "@/lib/toolgroups";
import type { ToolEvent } from "@/api/client";

function event(
  id: number,
  kind: string,
  payload: Record<string, unknown>,
  step = 1
): ToolEvent {
  return { id, kind, step, phase: "EXECUTING_TOOLS", payload };
}

describe("buildFeed tool grouping", () => {
  it("groups consecutive completed tools and collapses them", () => {
    const events = [
      event(1, "run_started", {}, 0),
      event(2, "tool_started", { call_id: "a", name: "glob", arguments: '{"pattern":"**/*.py"}' }),
      event(3, "tool_finished", { call_id: "a", name: "glob", ok: true, error_code: null, summary: "glob ok: path=." }),
      event(4, "tool_started", { call_id: "b", name: "read_file", arguments: '{"path":"src/app.py"}' }),
      event(5, "tool_finished", { call_id: "b", name: "read_file", ok: true, error_code: null, summary: "read_file ok: path=src/app.py" }),
      event(6, "run_finished", { status: "SUCCESS", stop_reason: "FINAL_ANSWER", verification_status: "VERIFIED" }),
    ];
    const { items, groups } = buildFeed(events, "fix it");
    expect(items.map((item) => item.kind)).toEqual(["task", "terminal"]);
    expect(groups).toHaveLength(1);
    expect(groups[0].items).toHaveLength(2);
    expect(groups[0].running).toBe(false);
    expect(groups[0].containsError).toBe(false);
    expect(groups[0].items[0].status).toBe("success");
  });

  it("keeps the running group expanded (still running)", () => {
    const events = [
      event(1, "tool_started", { call_id: "a", name: "run_command", arguments: "{}" }),
    ];
    const { groups } = buildFeed(events, "task");
    expect(groups[0].running).toBe(true);
  });

  it("keeps error groups expanded and marks containsError", () => {
    const events = [
      event(1, "tool_started", { call_id: "a", name: "grep", arguments: "{}" }),
      event(2, "tool_finished", { call_id: "a", name: "grep", ok: false, error_code: "INVALID_ARGUMENT", summary: "grep INVALID_ARGUMENT: bad" }),
    ];
    const { groups } = buildFeed(events, "task");
    expect(groups[0].containsError).toBe(true);
    expect(groups[0].items[0].status).toBe("error");
    expect(groups[0].items[0].errorCode).toBe("INVALID_ARGUMENT");
  });

  it("treats aborted tools as interrupted (realistic interleaving)", () => {
    // One tool is executing when the run is cancelled: it receives
    // tool_finished with TOOL_ABORTED; no further tools start.
    const events = [
      event(1, "tool_started", { call_id: "a", name: "run_command", arguments: "{}" }),
      event(2, "tool_finished", {
        call_id: "a",
        name: "run_command",
        ok: false,
        error_code: "TOOL_ABORTED",
        summary: "command aborted by user cancellation",
      }),
      event(3, "run_finished", { status: "INTERRUPTED", stop_reason: "INTERRUPTED", verification_status: "NOT_RUN" }),
    ];
    const { groups } = buildFeed(events, "task");
    expect(groups[0].aborted).toBe(true);
    expect(groups[0].containsError).toBe(true);
    expect(groups[0].items[0].status).toBe("aborted");
  });

  it("dedupes duplicate call ids (keep first)", () => {
    const events = [
      event(1, "tool_started", { call_id: "a", name: "glob", arguments: "{}" }),
      event(2, "tool_started", { call_id: "a", name: "glob", arguments: "{}" }),
      event(3, "tool_finished", { call_id: "a", name: "glob", ok: true, error_code: null, summary: "s" }),
    ];
    const { groups } = buildFeed(events, "task");
    expect(groups[0].items).toHaveLength(1);
  });
});
