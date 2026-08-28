import { CircleStop, Play } from "lucide-react";
import { useI18n } from "@/lib/i18n";
import { InlineError } from "./InlineError";
import type { InlineErrorKind } from "./InlineError";

export type ComposerState = "idle" | "invalid" | "ready" | "starting" | "running" | "cancelling";

export interface TaskComposerProps {
  task: string;
  onTaskChange: (value: string) => void;
  state: ComposerState;
  error?: { kind: InlineErrorKind; message: string; field?: string | null } | null;
  onStart: () => void;
  onCancel: () => void;
  startDisabledReason?: string | null;
}

/** One stable primary-control slot: Start while idle and Stop while running. */
export function TaskComposer({
  task,
  onTaskChange,
  state,
  error,
  onStart,
  onCancel,
  startDisabledReason,
}: TaskComposerProps) {
  const { t } = useI18n();
  const running = state === "running" || state === "cancelling";
  const canStart = state === "ready";

  const handleKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
      event.preventDefault();
      if (canStart) onStart();
    }
  };

  return (
    <section className="space-y-2">
      {error ? <InlineError kind={error.kind} message={error.message} field={error.field} /> : null}
      <div className="rounded-lg border border-border bg-surface p-2 shadow-sm">
        <label htmlFor="task-input" className="sr-only">
          {t("composer.label")}
        </label>
        <textarea
          id="task-input"
          className="min-h-[92px] w-full resize-y bg-transparent px-2 py-1.5 text-sm text-text placeholder:text-faint"
          placeholder={t("composer.placeholder")}
          value={task}
          onChange={(event) => onTaskChange(event.target.value)}
          onKeyDown={handleKeyDown}
          aria-describedby="task-hint"
        />
        <div className="flex items-center justify-between gap-3 border-t border-border px-1 pt-2">
          <p id="task-hint" className="min-w-0 text-xs text-faint" aria-live="polite">
            {running ? t("composer.runningHint") : t("composer.shortcut")}
            {!running && startDisabledReason ? ` · ${startDisabledReason}` : ""}
          </p>
          {running ? (
            <button
              type="button"
              className="btn-danger shrink-0"
              onClick={onCancel}
              disabled={state === "cancelling"}
              aria-label={t("composer.cancel")}
            >
              <CircleStop aria-hidden size={16} />
              {state === "cancelling" ? t("composer.cancelling") : t("composer.cancel")}
            </button>
          ) : (
            <button
              type="button"
              className="btn-primary shrink-0"
              onClick={onStart}
              disabled={!canStart}
              aria-label={state === "starting" ? t("composer.starting") : t("composer.start")}
            >
              <Play aria-hidden size={16} className={state === "starting" ? "animate-pulse" : ""} />
              {state === "starting" ? t("composer.starting") : t("composer.start")}
            </button>
          )}
        </div>
      </div>
    </section>
  );
}
