import { CircleStop, Play } from "lucide-react";
import { useI18n } from "@/lib/i18n";
import { cx } from "@/lib/format";
import { InlineError } from "./InlineError";
import type { InlineErrorKind } from "./InlineError";

export type ComposerState = "idle" | "invalid" | "ready" | "running" | "cancelling";

export interface TaskComposerProps {
  task: string;
  onTaskChange: (value: string) => void;
  state: ComposerState;
  /** Extra inline error shown above the composer when setup/validation fails. */
  error?: { kind: InlineErrorKind; message: string; field?: string | null } | null;
  onStart: () => void;
  onCancel: () => void;
  startDisabledReason?: string | null;
}

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
    <section className="card space-y-3">
      {error ? (
        <InlineError kind={error.kind} message={error.message} field={error.field} />
      ) : null}
      <div>
        <label htmlFor="task-input" className="mb-1.5 block text-sm font-medium">
          {t("composer.label")}
        </label>
        <textarea
          id="task-input"
          className="min-h-[96px] w-full resize-y rounded-md border border-border bg-surface px-3 py-2.5 text-sm text-text placeholder:text-faint"
          placeholder={t("composer.placeholder")}
          value={task}
          onChange={(event) => onTaskChange(event.target.value)}
          onKeyDown={handleKeyDown}
          disabled={running}
          aria-describedby="task-hint"
        />
        <p id="task-hint" className="mt-1 text-xs text-faint">
          {t("composer.shortcut")}
          {startDisabledReason ? ` · ${startDisabledReason}` : ""}
        </p>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          className="btn-primary"
          onClick={onStart}
          disabled={!canStart}
          aria-label={t("composer.start")}
        >
          <Play aria-hidden size={16} />
          {t("composer.start")}
        </button>
        <button
          type="button"
          className={cx("btn-danger", state === "idle" && "opacity-0")}
          onClick={onCancel}
          disabled={state !== "running"}
          aria-label={t("composer.cancel")}
        >
          <CircleStop aria-hidden size={16} />
          {state === "cancelling" ? t("composer.cancelling") : t("composer.cancel")}
        </button>
        <span className="ml-auto hidden text-xs text-faint sm:inline" aria-hidden>
          {running && state !== "cancelling" ? t("feed.following") : ""}
        </span>
      </div>
    </section>
  );
}
