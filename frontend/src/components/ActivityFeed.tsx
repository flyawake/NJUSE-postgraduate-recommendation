import { useEffect, useMemo, useRef, useState } from "react";
import { ArrowDown, ListRestart, TriangleAlert } from "lucide-react";
import { useI18n } from "@/lib/i18n";
import type { ToolEvent } from "@/api/client";
import { buildFeed } from "@/lib/toolgroups";
import type { FeedItem } from "@/lib/toolgroups";
import { ToolEventGroup } from "./ToolEventGroup";

export type FeedPhase =
  | "empty"
  | "streaming"
  | "paused-autoscroll"
  | "terminal"
  | "disconnected";

export interface ActivityFeedProps {
  events: ToolEvent[];
  task: string | null;
  state: "idle" | "running" | "terminal";
  sseStatus: "idle" | "connected" | "disconnected";
  terminalText?: string | null;
  verificationStatus?: string | null;
}

/**
 * Central activity feed with auto-follow: new content scrolls only when the
 * user is at the bottom; scrolling up pauses following and shows
 * "回到最新" without stealing focus.
 */
export function ActivityFeed({
  events,
  task,
  state,
  sseStatus,
  terminalText,
  verificationStatus,
}: ActivityFeedProps) {
  const { t } = useI18n();
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const [atBottom, setAtBottom] = useState(true);

  const grouping = useMemo(() => buildFeed(events, task ?? ""), [events, task]);

  const phase: FeedPhase =
    sseStatus === "disconnected"
      ? "disconnected"
      : state === "terminal"
        ? "terminal"
        : events.length === 0
          ? "empty"
          : atBottom
            ? "streaming"
            : "paused-autoscroll";

  const hasActivity = events.length > 0;

  // Auto-follow: only scroll when the user is (still) at the bottom.
  useEffect(() => {
    if (!atBottom || !hasActivity) return;
    bottomRef.current?.scrollIntoView({ block: "end" });
  }, [events.length, hasActivity, atBottom, phase]);

  const handleScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    setAtBottom(el.scrollHeight - el.scrollTop - el.clientHeight < 40);
  };

  if (!hasActivity && state !== "running") {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 px-6 text-center">
        <div className="flex h-12 w-12 items-center justify-center rounded-lg bg-accent-muted">
          <ListRestart aria-hidden size={22} className="text-accent" />
        </div>
        <h2 className="text-base font-medium">{t("feed.empty.title")}</h2>
        <p className="max-w-md text-sm text-muted">{t("feed.empty.hint")}</p>
        <ul className="mt-2 space-y-1 text-left text-sm text-muted">
          <li>• {t("composer.example1")}</li>
          <li>• {t("composer.example2")}</li>
          <li>• {t("composer.example3")}</li>
        </ul>
      </div>
    );
  }

  return (
    <div className="relative flex h-full min-h-0 flex-col">
      <div
        ref={scrollRef}
        onScroll={handleScroll}
        className="min-h-0 flex-1 overflow-y-auto px-4 pb-28 pt-4"
        data-testid="activity-scroll"
      >
        <div className="mx-auto max-w-3xl space-y-2.5">
          {phase === "disconnected" ? (
            <div className="flex items-center gap-2 rounded-md border border-warning/40 bg-warning-muted px-3 py-2 text-sm text-warning" role="status">
              <TriangleAlert aria-hidden size={14} />
              {t("feed.disconnected")}
            </div>
          ) : null}
          {phase === "streaming" ? (
            <p className="sr-only" role="status">
              {t("feed.following")}
            </p>
          ) : null}

          {grouping.items.map((item, index) => (
            <FeedItemView key={`${item.kind}-${index}`} item={item} />
          ))}

          {grouping.groups.length === 0 && events.length > 0 ? (
            <p className="text-center text-xs text-faint">{t("feed.noEvents")}</p>
          ) : null}
          {grouping.groups.map((group) => (
            <ToolEventGroup
              key={group.key}
              group={group}
              defaultOpen={group.running || group.containsError}
              streamOpen={state === "running"}
            />
          ))}

          {state === "terminal" && terminalText ? (
            <section
              className="rounded-lg border border-border bg-surface p-4 shadow-sm"
              data-testid="final-answer"
            >
              <h3 className="mb-1.5 text-sm font-semibold">{t("feed.finalAnswer")}</h3>
              <div className="whitespace-pre-wrap break-words text-sm leading-relaxed">
                {terminalText}
              </div>
              {verificationStatus ? (
                <p className="mt-2 text-xs font-medium text-muted">
                  {t("inspector.verification")}:{" "}
                  <span className="mono">{verificationStatus}</span>
                </p>
              ) : null}
            </section>
          ) : null}
          <div ref={bottomRef} className="h-px" />
        </div>
      </div>

      {phase === "paused-autoscroll" || phase === "disconnected" ? (
        <div className="pointer-events-none absolute inset-x-0 bottom-24 flex justify-center">
          <button
            type="button"
            className="btn-secondary pointer-events-auto shadow-md"
            onClick={() => {
              setAtBottom(true);
              bottomRef.current?.scrollIntoView({ block: "end", behavior: "smooth" });
            }}
            data-testid="back-to-latest"
          >
            <ArrowDown aria-hidden size={14} />
            {t("feed.backToLatest")}
          </button>
        </div>
      ) : null}
    </div>
  );
}

function FeedItemView({ item }: { item: FeedItem }) {
  const { t } = useI18n();
  switch (item.kind) {
    case "task":
      return (
        <div className="rounded-lg border border-border bg-accent-muted px-4 py-3">
          <p className="text-xs font-semibold text-accent">{t("feed.taskCard")}</p>
          <p className="mt-0.5 whitespace-pre-wrap break-words text-sm">{item.text}</p>
        </div>
      );
    case "step":
      return (
        <p className="px-1 text-xs text-faint">
          {t("feed.step", { step: item.step })}
          {item.charCount && item.budget ? ` · ${item.charCount}/${item.budget} chars` : ""}
        </p>
      );
    case "retry":
      return (
        <p className="px-1 text-xs text-warning">
          {t("feed.modelRetry", { attempt: item.attempt })}
          {item.reason ? ` · ${item.reason}` : ""}
        </p>
      );
    case "assistant":
      return null; // intermediate assistant text is not exposed by the kernel
    case "completion_deferred":
      return (
        <p className="px-1 text-xs text-muted">· {t("feed.completionDeferred")}</p>
      );
    case "terminal":
      return null; // terminal summary lives in the inspector + final answer card
    default:
      return null;
  }
}
