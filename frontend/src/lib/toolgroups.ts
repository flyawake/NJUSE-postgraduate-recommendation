import type { ToolEvent } from "@/api/client";

export type ToolStatus = "running" | "success" | "error" | "aborted";

export interface ToolItem {
  callId: string;
  name: string;
  argsSummary: string;
  target: string | null;
  status: ToolStatus;
  errorCode?: string | null;
  summary: string;
  step: number;
  phase: string;
  planStep?: string;
}

export type PlanStepStatus = "pending" | "in_progress" | "completed" | "blocked";

export interface PlanItem {
  revision: number;
  state: string;
  explanation: string;
  steps: Array<{ step: string; status: PlanStepStatus }>;
}

export type TranscriptItem =
  | { id: string; kind: "user_message"; text: string }
  | { id: string; kind: "action_row"; action: ToolItem }
  | { id: string; kind: "plan_card"; plan: PlanItem }
  | { id: string; kind: "stream_attempt"; attempt: number }
  | { id: string; kind: "status_notice"; status: "retry" }
  | {
      id: string;
      kind: "budget_notice";
      status: "extended" | "finalizing";
      from?: number;
      to?: number;
      hardLimit?: number;
    }
  | { id: string; kind: "verification_notice"; status: string | null }
  | { id: string; kind: "terminal_notice"; status: string | null };

/**
 * Deterministic, incremental event-to-product projection. It deliberately
 * exposes user requests, tool actions and public lifecycle facts only: model
 * reasoning and protocol counters never become transcript content.
 */
export class TranscriptProjector {
  private readonly actionByCallId = new Map<string, ToolItem>();
  private readonly rowIndexByCallId = new Map<string, number>();
  private readonly streamAttempts = new Set<number>();
  private planRowIndex: number | null = null;
  private planRevision = 0;
  private items: TranscriptItem[] = [];
  private userMessage: Extract<TranscriptItem, { kind: "user_message" }> | null = null;
  private head = 0;
  private _lastProjectedEventId = 0;
  private _processedEvents = 0;

  get lastProjectedEventId(): number {
    return this._lastProjectedEventId;
  }

  get processedEvents(): number {
    return this._processedEvents;
  }

  get snapshot(): readonly TranscriptItem[] {
    const activeItems = this.items.slice(this.head);
    return this.userMessage ? [this.userMessage, ...activeItems] : activeItems;
  }

  get itemCount(): number {
    return this.items.length - this.head + (this.userMessage ? 1 : 0);
  }

  get trackedActionCount(): number {
    return this.actionByCallId.size;
  }

  visibleItems(visibleCount: number): readonly TranscriptItem[] {
    const activityCount = this.items.length - this.head;
    const includeUser = this.userMessage !== null && visibleCount > activityCount;
    const activityLimit = Math.max(0, visibleCount - (includeUser ? 1 : 0));
    const activityItems = this.items.slice(
      Math.max(this.head, this.items.length - activityLimit)
    );
    return includeUser && this.userMessage
      ? [this.userMessage, ...activityItems]
      : activityItems;
  }

  reset(events: ToolEvent[], task: string, initialPlan?: PlanItem | null): readonly TranscriptItem[] {
    this.actionByCallId.clear();
    this.rowIndexByCallId.clear();
    this.streamAttempts.clear();
    this.planRowIndex = null;
    this.planRevision = 0;
    this.items = [];
    this.userMessage = task.trim()
      ? { id: "user-task", kind: "user_message", text: task }
      : null;
    this.head = 0;
    this._lastProjectedEventId = 0;
    this._processedEvents = 0;
    if (initialPlan) this.projectPlan(initialPlan);
    this.append(events, task);
    return this.snapshot;
  }

  append(events: ToolEvent[], task: string): void {
    for (const event of events) {
      if (event.id <= this._lastProjectedEventId) continue;
      this.project(event, task);
      this._lastProjectedEventId = event.id;
      this._processedEvents += 1;
    }
  }

  private project(event: ToolEvent, task: string): void {
    switch (event.kind) {
      case "run_started":
        if (!this.userMessage && task.trim()) {
          this.userMessage = { id: "user-task", kind: "user_message", text: task };
          this.prune();
        }
        return;
      case "model_stream_started":
      case "assistant_text_delta":
      case "reasoning_delta":
      case "reasoning_summary_delta": {
        const attempt = event.payload.attempt;
        if (typeof attempt !== "number" || this.streamAttempts.has(attempt)) return;
        this.streamAttempts.add(attempt);
        this.items.push({
          id: `stream-attempt-${attempt}`,
          kind: "stream_attempt",
          attempt,
        });
        this.prune();
        return;
      }
      case "tool_started": {
        if (event.payload.name === "update_plan") return;
        const callId = String(event.payload.call_id ?? `event-${event.id}`);
        if (this.actionByCallId.has(callId)) return;
        const action: ToolItem = {
          callId,
          name: String(event.payload.name ?? "tool"),
          argsSummary: String(event.payload.arguments ?? ""),
          target: typeof event.target === "string" ? event.target : null,
          status: "running",
          summary: "",
          step: event.step,
          phase: event.phase,
          planStep: typeof event.payload.plan_step === "string" ? event.payload.plan_step : undefined,
        };
        const row: Extract<TranscriptItem, { kind: "action_row" }> = {
          id: `action-${callId}`,
          kind: "action_row",
          action,
        };
        this.actionByCallId.set(callId, action);
        this.rowIndexByCallId.set(callId, this.items.length);
        this.items.push(row);
        this.prune();
        return;
      }
      case "plan_updated": {
        const rawSteps = event.payload.steps;
        if (!Array.isArray(rawSteps)) return;
        const statuses = new Set<PlanStepStatus>(["pending", "in_progress", "completed", "blocked"]);
        const steps = rawSteps.flatMap((raw) => {
          if (!raw || typeof raw !== "object") return [];
          const candidate = raw as Record<string, unknown>;
          if (typeof candidate.step !== "string" || !statuses.has(candidate.status as PlanStepStatus)) return [];
          return [{ step: candidate.step, status: candidate.status as PlanStepStatus }];
        });
        if (steps.length === 0) return;
        const plan: PlanItem = {
          revision: typeof event.payload.revision === "number" ? event.payload.revision : 0,
          state: typeof event.payload.state === "string" ? event.payload.state : "active",
          explanation: typeof event.payload.explanation === "string" ? event.payload.explanation : "",
          steps,
        };
        this.projectPlan(plan);
        return;
      }
      case "tool_finished": {
        const callId = String(event.payload.call_id ?? "");
        const action = this.actionByCallId.get(callId);
        const rowIndex = this.rowIndexByCallId.get(callId);
        if (!action || rowIndex === undefined) return;
        const errorCode = typeof event.payload.error_code === "string" ? event.payload.error_code : null;
        const nextAction: ToolItem = {
          ...action,
          status: event.payload.ok === true
            ? "success"
            : errorCode === "TOOL_ABORTED"
              ? "aborted"
              : "error",
          errorCode,
          summary: String(event.payload.summary ?? ""),
          phase: event.phase,
        };
        this.actionByCallId.set(callId, nextAction);
        // Replace only this row object. Memoised siblings keep their identity,
        // while React receives a new prop for the completed action.
        this.items[rowIndex] = { id: `action-${callId}`, kind: "action_row", action: nextAction };
        return;
      }
      case "model_retry":
        this.items.push({ id: `retry-${event.id}`, kind: "status_notice", status: "retry" });
        this.prune();
        return;
      case "step_budget_extended":
        this.items.push({
          id: `budget-${event.id}`,
          kind: "budget_notice",
          status: "extended",
          from: typeof event.payload.from === "number" ? event.payload.from : undefined,
          to: typeof event.payload.to === "number" ? event.payload.to : undefined,
          hardLimit: typeof event.payload.hard_limit === "number" ? event.payload.hard_limit : undefined,
        });
        this.prune();
        return;
      case "step_budget_finalizing":
        this.items.push({
          id: `budget-${event.id}`,
          kind: "budget_notice",
          status: "finalizing",
          to: typeof event.payload.work_step_limit === "number" ? event.payload.work_step_limit : undefined,
          hardLimit: typeof event.payload.hard_limit === "number" ? event.payload.hard_limit : undefined,
        });
        this.prune();
        return;
      case "completion_deferred":
        this.items.push({
          id: `verification-${event.id}`,
          kind: "verification_notice",
          status: typeof event.payload.verification_status === "string" ? event.payload.verification_status : null,
        });
        this.prune();
        return;
      case "run_finished":
        if (
          this.planRowIndex !== null
          && this.planRowIndex >= this.head
          && this.items[this.planRowIndex]?.kind === "plan_card"
          && typeof event.payload.plan_state === "string"
        ) {
          const current = this.items[this.planRowIndex];
          if (current.kind === "plan_card") {
            this.items[this.planRowIndex] = {
              ...current,
              plan: { ...current.plan, state: event.payload.plan_state },
            };
          }
        }
        this.items.push({
          id: `terminal-${event.id}`,
          kind: "terminal_notice",
          status: typeof event.payload.status === "string" ? event.payload.status : null,
        });
        this.prune();
        return;
      default:
        return;
    }
  }

  private projectPlan(plan: PlanItem): void {
    if (plan.revision <= this.planRevision) return;
    this.planRevision = plan.revision;
    const row: Extract<TranscriptItem, { kind: "plan_card" }> = {
      id: "active-plan",
      kind: "plan_card",
      plan,
    };
    if (this.planRowIndex !== null && this.planRowIndex >= this.head) {
      this.items[this.planRowIndex] = row;
    } else {
      this.planRowIndex = this.items.length;
      this.items.push(row);
      this.prune();
    }
  }

  private prune(): void {
    const maxItems = 2000;
    const maxActivityItems = maxItems - (this.userMessage ? 1 : 0);
    while (this.items.length - this.head > maxActivityItems) {
      const droppedIndex = this.head;
      const dropped = this.items[droppedIndex];
      if (dropped?.kind === "action_row") {
        const callId = dropped.action.callId;
        if (this.rowIndexByCallId.get(callId) === droppedIndex) {
          this.rowIndexByCallId.delete(callId);
          this.actionByCallId.delete(callId);
        }
      } else if (dropped?.kind === "plan_card" && this.planRowIndex === droppedIndex) {
        this.planRowIndex = null;
      }
      this.head += 1;
    }
    // Compact only after a full retained window has been discarded. This
    // keeps memory bounded (at most two windows) without an O(history) map
    // rewrite on every high-frequency append.
    if (this.head < maxItems) return;
    const offset = this.head;
    this.items = this.items.slice(offset);
    for (const [callId, index] of this.rowIndexByCallId) {
      this.rowIndexByCallId.set(callId, index - offset);
    }
    if (this.planRowIndex !== null) this.planRowIndex -= offset;
    this.head = 0;
  }
}

export function actionTarget(action: ToolItem): string {
  return action.target ?? "";
}
