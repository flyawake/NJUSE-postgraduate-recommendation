import { CheckCircle2, CircleDot, Loader2, OctagonX, Square, XCircle } from "lucide-react";
import { useI18n } from "@/lib/i18n";
import { cx } from "@/lib/format";

export type RunStatusKind = "idle" | "running" | "completed" | "error" | "interrupted";

const STATUS_META: Record<
  RunStatusKind,
  { labelKey: string; className: string; icon: "idle" | "running" | "completed" | "error" | "interrupted" }
> = {
  idle: { labelKey: "status.idle", className: "text-muted", icon: "idle" },
  running: { labelKey: "status.running", className: "text-info", icon: "running" },
  completed: { labelKey: "status.completed", className: "text-success", icon: "completed" },
  error: { labelKey: "status.error", className: "text-danger", icon: "error" },
  interrupted: { labelKey: "status.interrupted", className: "text-warning", icon: "interrupted" },
};

/** Run status badge — always icon + text, never color alone. */
export function RunStatusBadge({ status, compact }: { status: RunStatusKind; compact?: boolean }) {
  const { t } = useI18n();
  const meta = STATUS_META[status];
  const Icon =
    meta.icon === "running"
      ? Loader2
      : meta.icon === "completed"
        ? CheckCircle2
        : meta.icon === "error"
          ? XCircle
          : meta.icon === "interrupted"
            ? OctagonX
            : meta.icon === "idle"
              ? CircleDot
              : Square;
  return (
    <span
      className={cx(
        "inline-flex items-center gap-1.5 rounded-md px-2 py-0.5 text-xs font-medium",
        compact ? "" : "border border-border",
        meta.className
      )}
      role="status"
    >
      <Icon aria-hidden size={compact ? 12 : 14} className={meta.icon === "running" ? "animate-spin" : ""} />
      {t(meta.labelKey)}
    </span>
  );
}

export function runStatusKind(snapshot?: {
  state?: string | null;
  status?: string | null;
}): RunStatusKind {
  if (!snapshot) return "idle";
  if (snapshot.state === "running") return "running";
  if (snapshot.state !== "terminal") return "idle";
  if (snapshot.status === "SUCCESS") return "completed";
  if (snapshot.status === "INTERRUPTED") return "interrupted";
  return "error";
}
