import type { ToolEvent } from "@/api/client";

export type ToolStatus = "running" | "success" | "error" | "aborted";

export interface ToolItem {
  callId: string;
  name: string;
  argsSummary: string;
  status: ToolStatus;
  errorCode?: string | null;
  summary: string;
  step: number;
  phase: string;
}

export interface ToolGroup {
  key: string;
  items: ToolItem[];
  /** True when the group contains an error or aborted item (stays expanded). */
  containsError: boolean;
  /** True while some item of the group has no finished event yet. */
  running: boolean;
  /** True when the group was cut off by cancellation. */
  aborted: boolean;
}

export type FeedItem =
  | { kind: "task"; text: string }
  | { kind: "step"; step: number; charCount?: number; budget?: number }
  | { kind: "retry"; step: number; attempt: number; reason?: string }
  | { kind: "assistant"; step: number; textChars: number; toolCallCount: number }
  | { kind: "completion_deferred"; step: number; verificationStatus?: string }
  | { kind: "terminal"; status?: string; stopReason?: string; verificationStatus?: string };

export type FeedEntry = FeedItem | { kind: "group"; group: ToolGroup };

export interface FeedGrouping {
  entries: FeedEntry[];
  groups: ToolGroup[];
}

/**
 * Build one ordered activity feed. Step/retry/completion entries and tool
 * groups are interleaved in their true event order: a tool group occupies
 * the exact position where its first tool started, instead of being rendered
 * after all non-tool events.
 */
export function buildFeed(events: ToolEvent[], task: string): FeedGrouping {
  const entries: FeedEntry[] = [];
  const groups: ToolGroup[] = [];
  const started: Map<string, ToolItem> = new Map();
  let currentGroup: ToolGroup | null = null;

  const pushTool = (tool: ToolItem) => {
    if (!currentGroup || currentGroup.running) {
      currentGroup = {
        key: `g${groups.length}-${tool.step}`,
        items: [],
        containsError: false,
        running: false,
        aborted: false,
      };
      groups.push(currentGroup);
      entries.push({ kind: "group", group: currentGroup });
    }
    currentGroup.items.push(tool);
    if (tool.status === "error" || tool.status === "aborted") {
      currentGroup.containsError = true;
    }
    if (tool.status === "running") currentGroup.running = true;
    if (tool.status === "aborted") currentGroup.aborted = true;
  };

  for (const event of events) {
    switch (event.kind) {
      case "run_started":
        entries.push({ kind: "task", text: task });
        break;
      case "step_started":
        entries.push({
          kind: "step",
          step: event.step,
          charCount: typeof event.payload.char_count === "number" ? event.payload.char_count : undefined,
          budget: typeof event.payload.budget === "number" ? event.payload.budget : undefined,
        });
        break;
      case "model_retry":
        entries.push({
          kind: "retry",
          step: event.step,
          attempt: typeof event.payload.attempt === "number" ? event.payload.attempt : 0,
          reason: typeof event.payload.reason === "string" ? event.payload.reason : undefined,
        });
        break;
      case "assistant_received":
        entries.push({
          kind: "assistant",
          step: event.step,
          textChars: typeof event.payload.text_chars === "number" ? event.payload.text_chars : 0,
          toolCallCount:
            typeof event.payload.tool_call_count === "number" ? event.payload.tool_call_count : 0,
        });
        break;
      case "tool_started": {
        const callId = String(event.payload.call_id ?? `step${event.step}`);
        if (started.has(callId)) break; // duplicate start — keep the first
        const tool: ToolItem = {
          callId,
          name: String(event.payload.name ?? "tool"),
          argsSummary: String(event.payload.arguments ?? ""),
          status: "running",
          summary: "",
          step: event.step,
          phase: event.phase,
        };
        started.set(callId, tool);
        pushTool(tool);
        break;
      }
      case "tool_finished": {
        const callId = String(event.payload.call_id ?? "");
        const tool = started.get(callId);
        if (!tool) break;
        const ok = event.payload.ok === true;
        const code =
          typeof event.payload.error_code === "string" ? event.payload.error_code : null;
        tool.status = ok ? "success" : code === "TOOL_ABORTED" ? "aborted" : "error";
        tool.errorCode = code;
        tool.summary = String(event.payload.summary ?? "");
        tool.phase = event.phase;
        const owningGroup = groups.find((group) =>
          group.items.some((item) => item.callId === callId)
        );
        if (owningGroup) {
          owningGroup.running = false;
          if (tool.status === "error" || tool.status === "aborted") {
            owningGroup.containsError = true;
          }
          if (tool.status === "aborted") owningGroup.aborted = true;
        }
        break;
      }
      case "completion_deferred":
        entries.push({
          kind: "completion_deferred",
          step: event.step,
          verificationStatus:
            typeof event.payload.verification_status === "string"
              ? event.payload.verification_status
              : undefined,
        });
        break;
      case "run_finished":
        entries.push({
          kind: "terminal",
          status: typeof event.payload.status === "string" ? event.payload.status : undefined,
          stopReason:
            typeof event.payload.stop_reason === "string" ? event.payload.stop_reason : undefined,
          verificationStatus:
            typeof event.payload.verification_status === "string"
              ? event.payload.verification_status
              : undefined,
        });
        break;
      default:
        break;
    }
  }

  return { entries, groups };
}
