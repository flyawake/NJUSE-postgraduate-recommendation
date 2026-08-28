import { memo, useMemo, useState } from "react";
import * as Collapsible from "@radix-ui/react-collapsible";
import { Brain, ChevronDown, CircleStop } from "lucide-react";
import type { StreamCheckpoint, ToolEvent } from "@/api/client";
import { useI18n } from "@/lib/i18n";
import { cx } from "@/lib/format";
import { useThrottledValue } from "@/lib/useThrottledValue";

export interface StreamingTranscriptProps {
  events: ToolEvent[];
  snapshot?: StreamCheckpoint[];
  terminalText?: string | null;
  defaultThinkOpen?: boolean;
  showAssistantText?: boolean;
  attempt?: number;
}

interface StreamBlock {
  attempt: number;
  text: string;
  thinking: string;
  status: "streaming" | "done" | "abandoned";
  hasThinking: boolean;
  elapsedMs?: number;
}

function enrich(events: ToolEvent[], snapshot?: StreamCheckpoint[]): StreamBlock[] {
  type Draft = StreamBlock & { textParts: string[]; thinkingParts: string[] };
  const blocks = new Map<number, Draft>();
  const order: number[] = [];
  const checkpointCursor = new Map<string, number>();
  const define = (attempt: number): Draft => {
    let block = blocks.get(attempt);
    if (!block) {
      block = {
        attempt,
        text: "",
        thinking: "",
        textParts: [],
        thinkingParts: [],
        status: "streaming",
        hasThinking: false,
      };
      blocks.set(attempt, block);
      order.push(attempt);
    }
    return block;
  };
  for (const checkpoint of snapshot ?? []) {
    const block = define(checkpoint.attempt);
    checkpointCursor.set(
      `${checkpoint.attempt}:${checkpoint.channel}`,
      checkpoint.event_seq
    );
    if (checkpoint.channel === "text") block.textParts.push(checkpoint.text);
    if (checkpoint.channel === "reasoning" || checkpoint.channel === "summary") {
      block.thinkingParts.push(checkpoint.text);
      block.hasThinking = true;
    }
  }
  for (const event of events) {
    const rawAttempt = event.payload.attempt;
    const attempt = typeof rawAttempt === "number" ? rawAttempt : null;
    switch (event.kind) {
      case "model_stream_started":
        if (attempt !== null) define(attempt);
        break;
      case "assistant_text_delta": {
        if (attempt === null) break;
        if (event.id <= (checkpointCursor.get(`${attempt}:text`) ?? 0)) break;
        const block = define(attempt);
        block.textParts.push(String(event.payload.delta ?? ""));
        break;
      }
      case "reasoning_summary_delta": {
        if (attempt === null) break;
        const channel = "summary";
        if (event.id <= (checkpointCursor.get(`${attempt}:${channel}`) ?? 0)) break;
        const block = define(attempt);
        block.thinkingParts.push(String(event.payload.delta ?? ""));
        block.hasThinking = true;
        break;
      }
      case "reasoning_delta": {
        if (attempt === null) break;
        if (event.id <= (checkpointCursor.get(`${attempt}:reasoning`) ?? 0)) break;
        const block = define(attempt);
        block.thinkingParts.push(String(event.payload.delta ?? ""));
        block.hasThinking = true;
        break;
      }
      case "assistant_received": {
        if (attempt === null) break;
        const block = define(attempt);
        block.status = "done";
        if (typeof event.payload.elapsed_ms === "number") {
          block.elapsedMs = event.payload.elapsed_ms;
        }
        break;
      }
      case "stream_attempt_abandoned": {
        if (attempt === null) break;
        const block = define(attempt);
        block.status = "abandoned";
        break;
      }
      case "run_finished": {
        const block = order.length ? blocks.get(order[order.length - 1]) : undefined;
        if (block) block.status = "done";
        break;
      }
      default:
        break;
    }
  }
  return order.map((attempt) => {
    const draft = blocks.get(attempt)!;
    return {
      attempt: draft.attempt,
      text: draft.textParts.join(""),
      thinking: draft.thinkingParts.join(""),
      status: draft.status,
      hasThinking: draft.hasThinking,
      elapsedMs: draft.elapsedMs,
    };
  });
}

/**
 * Derives assistant text and provider-visible Think blocks from the same
 * public event feed. It never invents reasoning: an empty thinking renderer
 * is omitted entirely.
 */
export const StreamingTranscript = memo(function StreamingTranscript({
  events,
  snapshot,
  terminalText,
  defaultThinkOpen = false,
  showAssistantText = true,
  attempt,
}: StreamingTranscriptProps) {
  const terminal = events.some((event) => event.kind === "run_finished");
  const throttledEvents = useThrottledValue(events, 50, terminal);
  const blocks = useMemo(() => {
    const projected = enrich(throttledEvents, snapshot);
    return attempt === undefined
      ? projected
      : projected.filter((block) => block.attempt === attempt);
  }, [throttledEvents, snapshot, attempt]);
  if (blocks.length === 0) return null;

  return (
    <div className="space-y-2">
      {blocks.map((block) => (
        <StreamBlockView
          key={`attempt-${block.attempt}`}
          block={block}
          terminalText={block.status === "done" ? terminalText : null}
          defaultThinkOpen={defaultThinkOpen}
          showAssistantText={showAssistantText}
        />
      ))}
    </div>
  );
});

const StreamBlockView = memo(function StreamBlockView({
  block,
  terminalText,
  defaultThinkOpen,
  showAssistantText,
}: {
  block: StreamBlock;
  terminalText?: string | null;
  defaultThinkOpen: boolean;
  showAssistantText: boolean;
}) {
  const { t } = useI18n();
  const [open, setOpen] = useState(defaultThinkOpen);
  const hasText = Boolean(block.text.trim());
  return (
    <section className="space-y-1" data-attempt={block.attempt}>
      {block.hasThinking ? (
        <Collapsible.Root open={open} onOpenChange={setOpen} data-testid="think-block">
          <Collapsible.Trigger asChild>
            <button
              type="button"
              className="flex w-full items-center gap-2 rounded-md bg-surface-2/70 px-3 py-1.5 text-left text-xs text-muted transition-colors hover:bg-surface-2"
              aria-expanded={open}
              aria-label={t("thinking.toggle")}
            >
              <Brain aria-hidden size={13} className="shrink-0 text-accent" />
              <span className="flex-1 truncate">
                {block.status === "abandoned"
                  ? t("thinking.abandoned")
                  : block.status === "done" && !block.thinking.trim()
                    ? t("thinking.summary")
                    : block.status === "done"
                      ? block.elapsedMs === undefined
                        ? t("thinking.done")
                        : t("thinking.doneDuration", {
                            seconds: (block.elapsedMs / 1000).toFixed(1),
                          })
                      : t("thinking.streaming")}
              </span>
              {block.status === "abandoned" ? (
                <CircleStop aria-hidden size={13} className="shrink-0 text-warning" />
              ) : null}
              <ChevronDown
                aria-hidden
                size={13}
                className={cx("shrink-0 text-faint transition-transform", open && "rotate-180")}
              />
            </button>
          </Collapsible.Trigger>
          <Collapsible.Content className="border-l-2 border-accent/40 pl-3">
            <div className="whitespace-pre-wrap break-words py-1 text-xs leading-relaxed text-muted">
              {block.thinking}
            </div>
          </Collapsible.Content>
        </Collapsible.Root>
      ) : null}
      {showAssistantText && (hasText || terminalText) ? (
        <div
          className="whitespace-pre-wrap break-words text-sm leading-relaxed"
          data-testid="streaming-assistant-text"
        >
          {block.text || terminalText || ""}
        </div>
      ) : null}
    </section>
  );
});
