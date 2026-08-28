import { memo, useState } from "react";
import { ChevronDown, ChevronUp, FileDiff } from "lucide-react";
import type { ChangeSet, FileChange } from "@/api/client";
import { cx } from "@/lib/format";
import { useI18n } from "@/lib/i18n";

export interface TurnChangeSummaryProps {
  changeSet?: ChangeSet | null;
  onOpenFile?: (change: FileChange) => void;
  defaultExpanded?: boolean;
}

const STATUS_LABEL: Record<string, string> = {
  created: "A",
  modified: "M",
  deleted: "D",
  renamed: "R",
};

/**
 * Terminal turn change summary. It deliberately renders only the finalized
 * net change set; this component never computes diffs itself.
 */
export const TurnChangeSummary = memo(function TurnChangeSummary({
  changeSet,
  onOpenFile,
  defaultExpanded = false,
}: TurnChangeSummaryProps) {
  const { t } = useI18n();
  const [expanded, setExpanded] = useState(defaultExpanded);
  if (!changeSet || changeSet.file_count === 0) {
    return (
      <div data-testid="turn-change-summary-empty" className="text-xs text-faint">
        {t("changes.none")}
      </div>
    );
  }
  const files = changeSet.files ?? [];
  const visible = expanded ? files : files.slice(0, 5);
  const hiddenCount = files.length - visible.length;
  return (
    <section
      className="rounded-md border border-border bg-surface-2/50 p-3 text-sm"
      data-testid="turn-change-summary"
    >
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 font-medium">
          <FileDiff aria-hidden size={16} className="text-accent" />
          <span>
            {t("changes.summary", { count: changeSet.file_count, additions: changeSet.additions, deletions: changeSet.deletions })}
          </span>
        </div>
        {files.length > 5 ? (
          <button
            type="button"
            className="btn-ghost flex items-center gap-1 text-xs"
            onClick={() => setExpanded((value) => !value)}
            aria-expanded={expanded}
          >
            {expanded ? <ChevronUp aria-hidden size={14} /> : <ChevronDown aria-hidden size={14} />}
            {expanded ? t("changes.collapse") : t("changes.showMore", { count: hiddenCount })}
          </button>
        ) : null}
      </div>
      <ul className="mt-2 divide-y divide-border/60">
        {visible.map((file) => (
          <li key={file.id}>
            <button
              type="button"
              className="flex w-full items-center gap-2 rounded px-1 py-1 text-left hover:bg-surface-2"
              onClick={() => onOpenFile?.(file)}
              data-testid={`change-file-${file.relative_path}`}
            >
              <span
                className={cx(
                  "inline-flex h-5 w-5 items-center justify-center rounded text-xs font-semibold",
                  file.change_type === "created" && "bg-success/10 text-success",
                  file.change_type === "modified" && "bg-warning/10 text-warning",
                  file.change_type === "deleted" && "bg-danger/10 text-danger",
                  file.change_type === "renamed" && "bg-accent/10 text-accent"
                )}
              >
                {STATUS_LABEL[file.change_type] ?? "?"}
              </span>
              <span className="min-w-0 flex-1 truncate font-mono text-xs">
                {file.relative_path}
              </span>
              <span className="shrink-0 text-xs tabular-nums text-muted">
                +{file.additions} -{file.deletions}
              </span>
            </button>
          </li>
        ))}
      </ul>
      {changeSet.coverage !== "complete" ? (
        <p className="mt-2 text-xs text-warning">
          {t("changes.incomplete")}
        </p>
      ) : null}
    </section>
  );
});
