import { describe, expect, it } from "vitest";
import { actionTarget, TranscriptProjector } from "@/lib/toolgroups";
import type { ToolEvent } from "@/api/client";

function event(
  id: number,
  kind: string,
  payload: Record<string, unknown>,
  step = 1
): ToolEvent {
  return { id, kind, step, phase: "EXECUTING_TOOLS", payload };
}

describe("TranscriptProjector", () => {
  it("projects only public user, action and lifecycle information", () => {
    const projector = new TranscriptProjector();
    const items = projector.reset([
      event(1, "run_started", {}, 0),
      event(2, "step_started", { char_count: 10, budget: 120000 }),
      event(3, "assistant_received", { text_chars: 1000, tool_call_count: 1 }),
      event(4, "tool_started", { call_id: "a", name: "read_file", arguments: '{"path":"src/a.py"}' }),
      event(5, "model_retry", { attempt: 1, reason: "busy" }),
      event(6, "completion_deferred", { verification_status: "NOT_RUN" }),
    ], "read the file");

    expect(items.map((item) => item.kind)).toEqual([
      "user_message",
      "action_row",
      "status_notice",
      "verification_notice",
    ]);
    expect(items[1].kind === "action_row" && items[1].action.name).toBe("read_file");
  });

  it("projects only appended events and updates a finished tool action in place", () => {
    const projector = new TranscriptProjector();
    projector.reset([
      event(1, "run_started", {}, 0),
      event(2, "tool_started", { call_id: "a", name: "read_file", arguments: '{"path":"src/a.py"}' }),
    ], "read the file");
    expect(projector.processedEvents).toBe(2);
    const initialRow = projector.snapshot.find((item) => item.kind === "action_row");
    expect(initialRow?.kind === "action_row" && initialRow.action.status).toBe("running");

    projector.append([
      event(2, "tool_started", { call_id: "a", name: "read_file", arguments: "{}" }),
      event(3, "tool_finished", { call_id: "a", ok: true, error_code: null, summary: "read ok" }),
    ], "read the file");

    expect(projector.processedEvents).toBe(3);
    const finishedRow = projector.snapshot.find((item) => item.kind === "action_row");
    expect(finishedRow?.kind === "action_row" && finishedRow.action.status).toBe("success");
    expect(finishedRow?.kind === "action_row" && finishedRow.action.summary).toBe("read ok");
    expect(finishedRow).not.toBe(initialRow);
    expect(finishedRow?.id).toBe(initialRow?.id);
  });

  it("keeps a bounded view possible for a 2,000-event history without dropping order", () => {
    const events: ToolEvent[] = [event(1, "run_started", {}, 0)];
    for (let index = 0; index < 999; index += 1) {
      const callId = `call-${index}`;
      events.push(event(index * 2 + 2, "tool_started", { call_id: callId, name: "glob", arguments: "{}" }));
      events.push(event(index * 2 + 3, "tool_finished", { call_id: callId, ok: true, error_code: null, summary: "ok" }));
    }
    const projector = new TranscriptProjector();
    const items = projector.reset(events, "inspect");
    expect(projector.processedEvents).toBe(1999);
    expect(items).toHaveLength(1000);
    expect(items.slice(-300)).toHaveLength(300);
    const actionIds = items
      .filter((item) => item.kind === "action_row")
      .map((item) => item.id);
    expect(actionIds.at(0)).toBe("action-call-0");
    expect(actionIds.at(-1)).toBe("action-call-998");
  });

  it("projects only the incoming batch after a 2,000-event baseline and bounds sustained history", () => {
    const baseline: ToolEvent[] = [event(1, "run_started", {}, 0)];
    for (let index = 0; index < 999; index += 1) {
      const callId = `base-${index}`;
      baseline.push(event(index * 2 + 2, "tool_started", { call_id: callId, name: "glob", arguments: "{}" }));
      baseline.push(event(index * 2 + 3, "tool_finished", { call_id: callId, ok: true, error_code: null, summary: "ok" }));
    }
    baseline.push(event(2000, "assistant_received", { text_chars: 0, tool_call_count: 0 }));
    expect(baseline).toHaveLength(2000);
    const projector = new TranscriptProjector();
    projector.reset(baseline, "inspect");
    const processedBefore = projector.processedEvents;
    const batch = Array.from({ length: 50 }, (_, index) =>
      event(2001 + index, "model_retry", { attempt: index + 1 })
    );
    projector.append(batch, "inspect");
    expect(projector.processedEvents - processedBefore).toBe(50);

    const sustained = Array.from({ length: 4100 }, (_, index) =>
      event(index + 1, "tool_started", {
        call_id: `sustained-${index}`,
        name: "read_file",
        arguments: "{}",
      })
    );
    projector.reset(sustained, "inspect");
    expect(projector.itemCount).toBeLessThanOrEqual(2000);
    expect(projector.trackedActionCount).toBeLessThanOrEqual(2000);
    expect(projector.snapshot.at(0)?.id).toBe("user-task");
    expect(projector.snapshot.at(1)?.id).toBe("action-sustained-2101");

    projector.append([event(4101, "run_started", {}, 0)], "inspect");
    expect(projector.snapshot.filter((item) => item.kind === "user_message")).toHaveLength(1);
  });

  it("recovers one user task when the retained tail no longer contains run_started", () => {
    const projector = new TranscriptProjector();
    const retainedTail = Array.from({ length: 2000 }, (_, index) =>
      event(5000 + index, "tool_started", {
        call_id: `tail-${index}`,
        name: "read_file",
        arguments: "{}",
      })
    );

    projector.reset(retainedTail, "恢复这个长任务");
    expect(projector.itemCount).toBe(2000);
    expect(projector.snapshot.filter((item) => item.kind === "user_message")).toEqual([
      { id: "user-task", kind: "user_message", text: "恢复这个长任务" },
    ]);
    expect(projector.snapshot.at(1)?.id).toBe("action-tail-1");

    projector.append([event(7000, "run_started", {}, 0)], "恢复这个长任务");
    expect(projector.snapshot.filter((item) => item.kind === "user_message")).toHaveLength(1);
  });

  it("uses the structured target when the redacted argument summary is truncated", () => {
    const projector = new TranscriptProjector();
    projector.reset([
      {
        ...event(1, "tool_started", {
          call_id: "long",
          name: "write_file",
          arguments: '{"content":"***","path":"src/' + "x".repeat(160) + "…",
        }),
        target: "src/precise-target.py",
      },
    ], "write");
    const row = projector.snapshot.find((item) => item.kind === "action_row");
    expect(row?.kind).toBe("action_row");
    if (row?.kind !== "action_row") throw new Error("missing action row");
    expect(actionTarget(row.action)).toBe("src/precise-target.py");
    expect(row.action.argsSummary).toContain("***");
  });

  it("shows only one revisioned plan card and hides update_plan as a tool row", () => {
    const projector = new TranscriptProjector();
    projector.reset([
      event(1, "tool_started", { call_id: "plan-1", name: "update_plan", arguments: "{}" }),
      event(2, "plan_updated", {
        revision: 1,
        state: "active",
        explanation: "multiple dependent outcomes",
        steps: [
          { step: "Inspect", status: "in_progress" },
          { step: "Verify", status: "pending" },
        ],
      }),
      event(3, "tool_finished", { call_id: "plan-1", name: "update_plan", ok: true }),
      event(4, "tool_started", {
        call_id: "read-1",
        name: "read_file",
        arguments: "{}",
        plan_step: "Inspect",
      }),
    ], "complex task");
    projector.append([
      event(5, "plan_updated", {
        revision: 2,
        state: "active",
        explanation: "progress",
        steps: [
          { step: "Inspect", status: "completed" },
          { step: "Verify", status: "in_progress" },
        ],
      }),
      event(6, "run_finished", { status: "SUCCESS", plan_state: "incomplete" }),
    ], "complex task");

    const plans = projector.snapshot.filter((item) => item.kind === "plan_card");
    const actions = projector.snapshot.filter((item) => item.kind === "action_row");
    expect(plans).toHaveLength(1);
    expect(plans[0].kind === "plan_card" && plans[0].plan.revision).toBe(2);
    expect(plans[0].kind === "plan_card" && plans[0].plan.state).toBe("incomplete");
    expect(actions).toHaveLength(1);
    expect(actions[0].kind === "action_row" && actions[0].action.name).toBe("read_file");
    expect(actions[0].kind === "action_row" && actions[0].action.planStep).toBe("Inspect");
  });
});
