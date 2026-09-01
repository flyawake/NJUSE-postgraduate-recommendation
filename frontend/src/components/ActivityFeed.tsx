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
  type PlanItem,
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
  initialPlan?: PlanItem | null;
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
  initialPlan,
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
  const [workflowOpen, setWorkflowOpen] = useState(state !== "terminal");

  const projector = projectorRef.current;
  if (appliedResetVersion.current !== resetVersion) {
    projector.reset(retainedEvents, task ?? "", initialPlan);
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
  const userItems = visibleItems.filter((item) => item.kind === "user_message");
  const workflowItems = visibleItems.filter((item) => item.kind !== "user_message");
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
    setWorkflowOpen(true);
  }, [resetVersion]);

  useEffect(() => {
    if (state === "terminal") setWorkflowOpen(false);
  }, [state]);

  const handleScroll = () => {
    const element = scrollRef.current;
    if (!element) return;
    setAtBottom(element.scrollHeight - element.scrollTop - element.clientHeight < 40);
  };

  if (!hasActivity && state !== "running") {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 px-6 pb-[6vh] text-center">
        <div className="flex h-12 w-12 items-center justify-center rounded-lg bg-accent text-accent-fg shadow-sm">
          <ListRestart aria-hidden size={21} />
        </div>
        <h2 className="mt-1 text-xl font-medium tracking-[-0.025em]">{t("feed.empty.title")}</h2>
        <p className="max-w-md text-sm leading-7 text-muted">{t("feed.empty.hint")}</p>
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
        <div className="mx-auto max-w-[58rem] space-y-1 px-1 sm:px-2">
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

          {userItems.map((item) => <TranscriptRow key={item.id} item={item} />)}

          {workflowItems.length > 0 || start > 0 ? (
            <Collapsible.Root open={workflowOpen} onOpenChange={setWorkflowOpen} className="pt-2" data-testid="workflow-group">
              <Collapsible.Trigger asChild>
                <button
                  type="button"
                  className="group flex h-8 w-full items-center gap-2 rounded-md px-1.5 text-left text-[12px] font-medium text-muted transition-colors hover:bg-surface-2/70 hover:text-text"
                  aria-expanded={workflowOpen}
                  data-testid="workflow-toggle"
                >
                  <Wrench aria-hidden size={13} className="text-faint group-hover:text-muted" />
                  <span className="flex-1">
                    {t(state === "running" ? "feed.toolGroup.running" : "feed.toolGroup.completed", { count: workflowItems.length })}
                  </span>
                  <ChevronDown aria-hidden size={13} className={cx("text-faint transition-transform", workflowOpen && "rotate-180")} />
                </button>
              </Collapsible.Trigger>
              <Collapsible.Content className="space-y-1 pt-1">
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
                {workflowItems.map((item) =>
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
              </Collapsible.Content>
            </Collapsible.Root>
          ) : null}

          {state === "terminal" && terminalText ? (
            <section className="mt-3 border-t border-border pt-5" data-testid="final-answer">
              <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-[0.12em] text-faint">{t("feed.finalAnswer")}</h3>
              <div className="text-[15px] leading-7 sm:text-base">
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
        <div className="flex justify-end py-2" data-testid="user-message">
          <article className="max-w-[min(44rem,88%)] rounded-[20px] rounded-br-md bg-surface-2 px-4 py-2.5 shadow-sm">
            <p className="sr-only">{t("feed.taskCard")}</p>
            <p className="whitespace-pre-wrap break-words text-[15px] leading-7 text-text">{item.text}</p>
          </article>
        </div>
      );
    case "action_row":
      return <ToolActionRow action={item.action} />;
    case "plan_card":
      return <PlanCard plan={item.plan} />;
    case "status_notice":
      return <p className="px-1 text-xs text-warning">{t("feed.modelRetryShort")}</p>;
    case "budget_notice":
      return (
        <p className="px-1 text-xs text-muted" data-testid={`budget-${item.status}`}>
          {item.status === "extended"
            ? t("feed.budgetExtended", {
                from: item.from ?? "?",
                to: item.to ?? "?",
                hardLimit: item.hardLimit ?? "?",
              })
            : t("feed.budgetFinalizing", {
                limit: item.to ?? "?",
                hardLimit: item.hardLimit ?? "?",
              })}
        </p>
      );
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

const PlanCard = memo(function PlanCard({ plan }: { plan: PlanItem }) {
  const { t } = useI18n();
  return (
    <section className="my-2 rounded-lg border border-border bg-surface-2/45 px-3 py-3" data-testid="plan-card">
      <div className="flex items-center gap-2">
        <ListRestart aria-hidden size={14} className="text-info" />
        <h4 className="flex-1 text-xs font-semibold text-text">{t("feed.plan.title")}</h4>
        <span className="text-[10px] text-faint">
          {t(`feed.plan.state.${plan.state}`)} · v{plan.revision}
        </span>
      </div>
      {plan.explanation ? <p className="mt-1.5 text-[11px] leading-5 text-muted">{plan.explanation}</p> : null}
      <ol className="mt-2 space-y-1.5">
        {plan.steps.map((item, index) => (
          <li key={`${index}-${item.step}`} className="flex items-start gap-2 text-[12px] leading-5 text-muted">
            {item.status === "completed" ? (
              <CheckCircle2 aria-hidden size={13} className="mt-1 shrink-0 text-success" />
            ) : item.status === "in_progress" ? (
              <Loader2 aria-hidden size={13} className="mt-1 shrink-0 animate-spin text-info" />
            ) : item.status === "blocked" ? (
              <CircleStop aria-hidden size={13} className="mt-1 shrink-0 text-warning" />
            ) : (
              <span aria-hidden className="mt-[7px] h-2 w-2 shrink-0 rounded-full border border-faint" />
            )}
            <span className={item.status === "completed" ? "text-faint line-through" : ""}>{item.step}</span>
          </li>
        ))}
      </ol>
    </section>
  );
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
          className="group flex min-h-8 w-full items-center gap-2 rounded-md bg-transparent px-1.5 py-1 text-left text-[12px] text-muted transition-colors hover:bg-surface-2/70 hover:text-text"
          aria-label={t("feed.action.disclosure", { action: `${verb}${target ? ` ${target}` : ""}` })}
          aria-expanded={open}
        >
          <ToolIcon aria-hidden size={13} className="shrink-0 text-faint group-hover:text-muted" />
          <span className="min-w-0 flex-1 truncate">
            {verb}
            {target ? <span className="mono text-text/75"> {target}</span> : null}
          </span>
          <span className={cx("flex shrink-0 items-center gap-1 text-[11px] font-medium", STATUS_COLOR[action.status])}>
            <StatusIcon aria-hidden size={12} className={action.status === "running" ? "animate-spin" : ""} />
            {statusLabel}
          </span>
          <ChevronDown aria-hidden size={12} className={cx("shrink-0 text-faint transition-transform", open && "rotate-180")} />
        </button>
      </Collapsible.Trigger>
      <Collapsible.Content className="ml-2 pt-1">
        <div className="bounded-detail px-3 py-2.5 text-[11px]" tabIndex={0} data-testid="tool-detail-frame">
          <p className="text-faint">{t("feed.tool.arguments")}</p>
          <pre className="mono mt-1 overflow-x-auto whitespace-pre-wrap break-all rounded-[7px] bg-bg px-2 py-1.5 text-muted">
            {action.argsSummary || "{}"}
          </pre>
          {action.summary ? (
            <>
              <p className="mt-2 text-faint">{t("feed.tool.result")}</p>
              <pre className="mono mt-1 overflow-x-auto whitespace-pre-wrap break-all rounded-[7px] bg-bg px-2 py-1.5 text-muted">
                {action.summary}
              </pre>
            </>
          ) : null}
          {action.planStep ? (
            <p className="mt-2 text-faint">
              {t("feed.plan.currentStep")}: <span className="text-muted">{action.planStep}</span>
            </p>
          ) : null}
          {action.status === "error" ? <p className="mt-2 text-danger">{t("feed.action.failureRecovery")}</p> : null}
          {action.status === "aborted" ? <p className="mt-2 text-warning">{t("feed.action.abortedRecovery")}</p> : null}
        </div>
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
