import { ArrowUp, CircleStop } from "lucide-react";
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
      <div className="composer-shell p-2.5">
        <label htmlFor="task-input" className="sr-only">
          {t("composer.label")}
        </label>
        <textarea
          id="task-input"
          className="min-h-[104px] w-full resize-y bg-transparent px-2 py-1.5 text-[15px] leading-6 text-text placeholder:text-faint"
          placeholder={t("composer.placeholder")}
          value={task}
          onChange={(event) => onTaskChange(event.target.value)}
          onKeyDown={handleKeyDown}
          aria-describedby="task-hint"
        />
        <div className="flex items-center justify-between gap-3 px-1 pt-1.5">
          <p id="task-hint" className="min-w-0 text-xs text-faint" aria-live="polite">
            {running ? t("composer.runningHint") : t("composer.shortcut")}
            {!running && startDisabledReason ? ` · ${startDisabledReason}` : ""}
          </p>
          {running ? (
            <button
              type="button"
              className="composer-submit composer-stop"
              onClick={onCancel}
              disabled={state === "cancelling"}
              aria-label={t("composer.cancel")}
              title={state === "cancelling" ? t("composer.cancelling") : t("composer.cancel")}
            >
              <CircleStop aria-hidden size={17} />
              <span className="sr-only">{state === "cancelling" ? t("composer.cancelling") : t("composer.cancel")}</span>
            </button>
          ) : (
            <button
              type="button"
              className="composer-submit"
              onClick={onStart}
              disabled={!canStart}
              aria-label={state === "starting" ? t("composer.starting") : t("composer.start")}
              title={state === "starting" ? t("composer.starting") : t("composer.start")}
            >
              <ArrowUp aria-hidden size={18} className={state === "starting" ? "animate-pulse" : ""} />
              <span className="sr-only">{state === "starting" ? t("composer.starting") : t("composer.start")}</span>
            </button>
          )}
        </div>
      </div>
    </section>
  );
}
