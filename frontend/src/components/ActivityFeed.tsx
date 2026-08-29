import { memo, useEffect, useRef, useState } from "react";
import * as Collapsible from "@radix-ui/react-collapsible";
import {
  ArrowDown,
  CheckCircle2,
  ChevronDown,
  CircleStop,
  CircleX,
  ListRestart,
  Loader2,
  Search,
  Terminal,
  TriangleAlert,
  Wrench,
} from "lucide-react";
import { useI18n } from "@/lib/i18n";
import type { StreamCheckpoint, ToolEvent } from "@/api/client";
import { StreamingTranscript } from "./StreamingTranscript";
import { MarkdownText } from "./MarkdownText";
import {
  actionTarget,
  TranscriptProjector,
  type ToolItem,
  type TranscriptItem,
} from "@/lib/toolgroups";
import { cx } from "@/lib/format";

export type FeedPhase =
  | "empty"
  | "streaming"
  | "paused-autoscroll"
  | "terminal"
  | "disconnected";

export interface ActivityFeedProps {
  retainedEvents: ToolEvent[];
  eventBatch: ToolEvent[];
  resetVersion: number;
  eventVersion: number;
  task: string | null;
  state: "idle" | "running" | "terminal";
  sseStatus: "idle" | "connected" | "disconnected";
  terminalText?: string | null;
  verificationStatus?: string | null;
  streamSnapshot?: StreamCheckpoint[];
  defaultThinkOpen?: boolean;
  embedded?: boolean;
}

const INITIAL_VISIBLE_ITEMS = 300;
const LOAD_OLDER_ITEMS = 200;

interface WindowState {
  resetVersion: number;
  visibleCount: number;
}

/**
 * A continuous transcript that incrementally projects SSE deltas. The
 * retained event list can grow to 2,000, while the DOM mounts only the newest
 * 300 items until the user explicitly asks for earlier activity.
 */
export function ActivityFeed({
  retainedEvents,
  eventBatch,
  resetVersion,
  eventVersion,
  task,
  state,
  sseStatus,
  terminalText,
  verificationStatus,
  streamSnapshot,
  defaultThinkOpen = false,
  embedded = false,
}: ActivityFeedProps) {
  const { t } = useI18n();
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const projectorRef = useRef(new TranscriptProjector());
  const appliedResetVersion = useRef<number | null>(null);
  const [atBottom, setAtBottom] = useState(true);
  const [windowState, setWindowState] = useState<WindowState>(() => ({
    resetVersion,
    visibleCount: INITIAL_VISIBLE_ITEMS,
  }));

  const projector = projectorRef.current;
  if (appliedResetVersion.current !== resetVersion) {
    projector.reset(retainedEvents, task ?? "");
    appliedResetVersion.current = resetVersion;
  } else {
    projector.append(eventBatch, task ?? "");
  }
  // A reset/new run must satisfy the initial 300-row budget on its very first
  // committed frame. The effect below only synchronizes the stored state; it
  // is not relied on to repair an oversized transient render.
  const visibleCount = windowState.resetVersion === resetVersion
    ? windowState.visibleCount
    : INITIAL_VISIBLE_ITEMS;
  const itemCount = projector.itemCount;
  const start = Math.max(0, itemCount - visibleCount);
  const visibleItems = projector.visibleItems(visibleCount);
  const lastIncomingId = eventBatch.at(-1)?.id ?? retainedEvents.at(-1)?.id ?? 0;

  const phase: FeedPhase =
    sseStatus === "disconnected"
      ? "disconnected"
      : state === "terminal"
        ? "terminal"
        : itemCount === 0
          ? "empty"
          : atBottom
            ? "streaming"
            : "paused-autoscroll";

  const hasActivity = itemCount > 0;

  useEffect(() => {
    if (embedded || !atBottom || !hasActivity) return;
    bottomRef.current?.scrollIntoView({ block: "end" });
  }, [eventVersion, lastIncomingId, hasActivity, atBottom, phase, embedded]);

  useEffect(() => {
    setWindowState((current) =>
      current.resetVersion === resetVersion
        ? current
        : { resetVersion, visibleCount: INITIAL_VISIBLE_ITEMS }
    );
  }, [resetVersion]);

  const handleScroll = () => {
    const element = scrollRef.current;
    if (!element) return;
    setAtBottom(element.scrollHeight - element.scrollTop - element.clientHeight < 40);
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
    <div className={cx("relative flex min-h-0 flex-col", !embedded && "h-full")}>
      <div
        ref={scrollRef}
        onScroll={handleScroll}
        className={cx(
          "min-h-0 flex-1 px-4 pt-5",
          embedded ? "pb-3" : "overflow-y-auto pb-28"
        )}
        data-testid="activity-scroll"
      >
        <div className="mx-auto max-w-[54rem] space-y-2">
          {phase === "disconnected" ? (
            <div className="flex items-center gap-2 border-l-2 border-warning bg-warning-muted px-3 py-2 text-sm text-warning" role="status">
              <TriangleAlert aria-hidden size={14} />
              {t("feed.disconnected")}
            </div>
          ) : null}
          {phase === "streaming" ? (
            <p className="sr-only" role="status">
              {t("feed.following")}
            </p>
          ) : null}

          {start > 0 ? (
            <div className="flex justify-center pb-2">
              <button
                type="button"
                className="btn-secondary"
                onClick={() =>
                  setWindowState((current) => {
                    const count = current.resetVersion === resetVersion
                      ? current.visibleCount
                      : INITIAL_VISIBLE_ITEMS;
                    return {
                      resetVersion,
                      visibleCount: Math.min(itemCount, count + LOAD_OLDER_ITEMS),
                    };
                  })
                }
                data-testid="load-older-activity"
              >
                {t("feed.loadOlder")}
              </button>
            </div>
          ) : null}

          {visibleItems.map((item) =>
            item.kind === "stream_attempt" ? (
              <StreamingTranscript
                key={item.id}
                events={retainedEvents}
                snapshot={streamSnapshot}
                terminalText={terminalText}
                defaultThinkOpen={defaultThinkOpen}
                showAssistantText={state !== "terminal" || !terminalText}
                attempt={item.attempt}
              />
            ) : (
              <TranscriptRow key={item.id} item={item} />
            )
          )}

          {state === "terminal" && terminalText ? (
            <section className="border-t border-border pt-5" data-testid="final-answer">
              <h3 className="mb-1.5 text-sm font-semibold">{t("feed.finalAnswer")}</h3>
              <div className="text-sm">
                <MarkdownText text={terminalText} />
              </div>
              {verificationStatus ? (
                <p className="mt-2 text-xs font-medium text-muted">
                  {t("inspector.verification")}: {verificationLabel(verificationStatus, t)}
                </p>
              ) : null}
            </section>
          ) : null}
          <div ref={bottomRef} className="h-px" />
        </div>
      </div>

      {!embedded && (phase === "paused-autoscroll" || phase === "disconnected") ? (
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

const TranscriptRow = memo(function TranscriptRow({ item }: { item: TranscriptItem }) {
  const { t } = useI18n();
  switch (item.kind) {
    case "user_message":
      return (
        <article className="border-l-2 border-accent bg-accent-muted/60 px-3 py-2.5" data-testid="user-message">
          <p className="mb-1 text-xs font-medium text-accent">{t("feed.taskCard")}</p>
          <p className="whitespace-pre-wrap break-words text-sm">{item.text}</p>
        </article>
      );
    case "action_row":
      return <ToolActionRow action={item.action} />;
    case "status_notice":
      return <p className="px-1 text-xs text-warning">{t("feed.modelRetryShort")}</p>;
    case "verification_notice":
      return <p className="px-1 text-xs text-muted">{t("feed.verificationPending")}</p>;
    case "terminal_notice":
      return (
        <p className="px-1 text-xs text-muted">
          {item.status === "INTERRUPTED" ? t("feed.runInterrupted") : t("feed.runFinished")}
        </p>
      );
    default:
      return null;
  }
});

const STATUS_ICON = {
  running: Loader2,
  success: CheckCircle2,
  error: CircleX,
  aborted: CircleStop,
} as const;

const STATUS_COLOR = {
  running: "text-info",
  success: "text-success",
  error: "text-danger",
  aborted: "text-warning",
} as const;

const TOOL_ICON: Record<string, typeof Wrench> = {
  glob: Search,
  grep: Search,
  run_command: Terminal,
};

const TOOL_VERB: Record<string, string> = {
  glob: "feed.action.searchWorkspace",
  grep: "feed.action.search",
  read_file: "feed.action.read",
  write_file: "feed.action.write",
  edit_file: "feed.action.edit",
  run_command: "feed.action.run",
};

const ToolActionRow = memo(function ToolActionRow({ action }: { action: ToolItem }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const StatusIcon = STATUS_ICON[action.status];
  const ToolIcon = TOOL_ICON[action.name] ?? Wrench;
  const target = actionTarget(action);
  const verb = t(TOOL_VERB[action.name] ?? "feed.action.useTool");
  const statusLabel = t(`feed.tool.${action.status === "error" ? "failed" : action.status}`);

  return (
    <Collapsible.Root open={open} onOpenChange={setOpen} data-tool={action.name} data-tool-status={action.status}>
      <Collapsible.Trigger asChild>
        <button
          type="button"
          className="flex w-full items-center gap-2 rounded-md bg-surface-2/70 px-3 py-2 text-left text-sm transition-colors hover:bg-surface-2"
          aria-label={t("feed.action.disclosure", { action: `${verb}${target ? ` ${target}` : ""}` })}
        >
          <ToolIcon aria-hidden size={14} className="shrink-0 text-muted" />
          <span className="min-w-0 flex-1 truncate">
            {verb}
            {target ? <span className="mono text-muted"> {target}</span> : null}
          </span>
          <span className={cx("flex shrink-0 items-center gap-1 text-xs font-medium", STATUS_COLOR[action.status])}>
            <StatusIcon aria-hidden size={13} className={action.status === "running" ? "animate-spin" : ""} />
            {statusLabel}
          </span>
          <ChevronDown aria-hidden size={14} className={cx("shrink-0 text-faint transition-transform", open && "rotate-180")} />
        </button>
      </Collapsible.Trigger>
      <Collapsible.Content className="border-x border-b border-border bg-surface px-3 py-2 text-xs">
        <p className="text-faint">{t("feed.tool.arguments")}</p>
        <pre className="mono mt-1 max-h-40 overflow-auto whitespace-pre-wrap break-all rounded-sm bg-bg px-2 py-1.5 text-muted">
          {action.argsSummary || "{}"}
        </pre>
        {action.summary ? (
          <>
            <p className="mt-2 text-faint">{t("feed.tool.result")}</p>
            <pre className="mono mt-1 max-h-40 overflow-auto whitespace-pre-wrap break-all rounded-sm bg-bg px-2 py-1.5 text-muted">
              {action.summary}
            </pre>
          </>
        ) : null}
        {action.status === "error" ? <p className="mt-2 text-danger">{t("feed.action.failureRecovery")}</p> : null}
        {action.status === "aborted" ? <p className="mt-2 text-warning">{t("feed.action.abortedRecovery")}</p> : null}
      </Collapsible.Content>
    </Collapsible.Root>
  );
});

function verificationLabel(status: string, t: (key: string) => string): string {
  switch (status) {
    case "VERIFIED":
      return t("verification.verified");
    case "FAILED":
      return t("verification.failed");
    case "NOT_RUN":
      return t("verification.notRun");
    default:
      return t("verification.notApplicable");
  }
}
