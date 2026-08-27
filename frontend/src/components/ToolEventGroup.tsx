import { useState } from "react";
import * as Collapsible from "@radix-ui/react-collapsible";
import {
  CheckCircle2,
  ChevronDown,
  Loader2,
  ShieldX,
  Square,
  Wrench,
} from "lucide-react";
import { useI18n } from "@/lib/i18n";
import { cx } from "@/lib/format";
import type { ToolGroup, ToolItem } from "@/lib/toolgroups";

const STATUS_ICON = {
  running: Loader2,
  success: CheckCircle2,
  error: ShieldX,
  aborted: Square,
} as const;

const STATUS_COLOR = {
  running: "text-info",
  success: "text-success",
  error: "text-danger",
  aborted: "text-warning",
} as const;

/** One tool call: name, argument summary, bounded result, status. */
export function ToolCard({ item }: { item: ToolItem }) {
  const { t } = useI18n();
  const [expanded, setExpanded] = useState(
    item.status === "running" || item.status === "error" || item.status === "aborted"
  );
  const Icon = STATUS_ICON[item.status];
  const statusLabel = t(
    item.status === "success"
      ? "feed.tool.success"
      : item.status === "error"
        ? "feed.tool.failed"
        : item.status === "aborted"
          ? "feed.tool.aborted"
          : "feed.tool.running"
  );
  return (
    <div
      className="rounded-md border border-border bg-surface-2/60 px-3 py-2.5"
      data-tool={item.name}
      data-tool-status={item.status}
    >
      <div className="flex items-center gap-2">
        <Icon
          aria-hidden
          size={14}
          className={cx(STATUS_COLOR[item.status], item.status === "running" && "animate-spin")}
        />
        <span className="mono text-sm font-medium">{item.name}</span>
        <span className="mono min-w-0 flex-1 truncate text-xs text-muted" title={item.argsSummary}>
          {item.argsSummary}
        </span>
        <span className={cx("shrink-0 text-xs font-medium", STATUS_COLOR[item.status])}>
          {statusLabel}
        </span>
        <button
          type="button"
          className="btn-icon h-7 w-7"
          onClick={() => setExpanded((value) => !value)}
          aria-expanded={expanded}
          aria-label={expanded ? t("feed.tool.hideDetails") : t("feed.tool.details")}
        >
          <ChevronDown
            aria-hidden
            size={14}
            className={cx("transition-transform duration-fast", expanded && "rotate-180")}
          />
        </button>
      </div>
      {expanded ? (
        <div className="mt-2 space-y-2 border-t border-border pt-2 text-xs">
          <div>
            <p className="text-faint">{t("feed.tool.arguments")}</p>
            <pre className="mono mt-1 max-h-40 overflow-auto whitespace-pre-wrap break-all rounded-sm bg-bg px-2 py-1.5 text-muted">
              {item.argsSummary || "{}"}
            </pre>
          </div>
          {item.summary ? (
            <div>
              <p className="text-faint">{t("feed.tool.result")}</p>
              <pre className="mono mt-1 max-h-40 overflow-auto whitespace-pre-wrap break-all rounded-sm bg-bg px-2 py-1.5 text-muted">
                {item.summary}
              </pre>
            </div>
          ) : null}
          {item.errorCode ? (
            <p className="text-danger">
              {t("feed.tool.errorCode")}: <span className="mono">{item.errorCode}</span>
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

export interface ToolEventGroupProps {
  group: ToolGroup;
}

/**
 * A run of consecutive tool calls. While the run streams, only the current
 * running group and groups containing errors/aborted items stay expanded;
 * completed success groups collapse into one summary row immediately. In the
 * terminal state every group can still be inspected by the user.
 */
export function ToolEventGroup({ group }: ToolEventGroupProps) {
  const { t } = useI18n();
  const forceOpen = group.running || group.containsError;
  const [userOpen, setUserOpen] = useState<boolean | null>(null);
  const isOpen = forceOpen || (userOpen ?? false);
  const hasError = group.containsError;
  const Icon = hasError ? ShieldX : group.aborted ? Square : group.running ? Loader2 : Wrench;

  return (
    <Collapsible.Root
      open={isOpen}
      onOpenChange={(next) => setUserOpen(next)}
      data-tool-group
      data-collapsed={String(!isOpen)}
    >
      <Collapsible.Trigger asChild>
        <button
          type="button"
          className="flex w-full items-center gap-2 rounded-lg border border-border bg-surface px-3 py-2 text-sm transition-colors duration-fast hover:bg-surface-2"
          aria-expanded={isOpen}
        >
          <ChevronDown
            aria-hidden
            size={14}
            className={cx("text-faint transition-transform duration-fast", !isOpen && "-rotate-90")}
          />
          <Icon
            aria-hidden
            size={14}
            className={cx(
              hasError ? "text-danger" : group.running ? "text-info" : "text-muted",
              group.running && "animate-spin"
            )}
          />
          <span className="font-medium">
            {group.running
              ? t("feed.toolGroup.running", { count: group.items.length })
              : t("feed.toolGroup.completed", { count: group.items.length })}
          </span>
        </button>
      </Collapsible.Trigger>
      <Collapsible.Content className="mt-1.5 space-y-1.5">
        {group.items.map((item) => (
          <ToolCard key={item.callId} item={item} />
        ))}
      </Collapsible.Content>
    </Collapsible.Root>
  );
}
