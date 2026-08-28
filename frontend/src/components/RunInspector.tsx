import { useI18n } from "@/lib/i18n";
import * as Collapsible from "@radix-ui/react-collapsible";
import { ChevronDown } from "lucide-react";
import { errorCodeText } from "@/lib/errorText";
import type { RunSnapshot } from "@/api/client";
import { formatElapsed } from "@/lib/format";
import { RunStatusBadge, runStatusKind } from "./RunStatusBadge";
import { VerificationBadge, verificationKind } from "./VerificationBadge";
import { InlineError } from "./InlineError";
import type { InlineErrorKind } from "./InlineError";

export type InspectorMode = "no-run" | "active" | "terminal";

export interface RunInspectorProps {
  snapshot?: RunSnapshot;
  onNewTask: () => void;
}

function errorKind(code: string | undefined): InlineErrorKind {
  if (!code) return "run_failure";
  if (code === "model_error") return "configuration";
  return "run_failure";
}

/**
 * Right-hand run inspector: no-run / active / terminal.
 * Shows status, counters, verification, changed files and error/recovery.
 */
export function RunInspector({ snapshot, onNewTask }: RunInspectorProps) {
  const { t } = useI18n();

  if (!snapshot) {
    return (
      <aside className="card h-full" aria-label={t("inspector.title")}>
        <h2 className="mb-1 text-sm font-semibold">{t("inspector.title")}</h2>
        <p className="text-sm text-muted">{t("inspector.noRun")}</p>
        <p className="mt-1 text-xs text-faint">{t("inspector.noRunHint")}</p>
      </aside>
    );
  }

  const mode: InspectorMode =
    snapshot.state === "running" ? "active" : "terminal";
  const mutatedPaths = snapshot.mutated_paths ?? [];

  const statusKind = runStatusKind(snapshot);
  const verification = verificationKind(snapshot.verification_status);
  const error = snapshot.error;

  return (
    <aside className="card h-full overflow-y-auto" aria-label={t("inspector.title")} data-testid="run-inspector">
      <div className="mb-3 flex items-center justify-between gap-2">
        <h2 className="text-sm font-semibold">{t("inspector.title")}</h2>
        <RunStatusBadge status={statusKind} compact />
      </div>

      <div className="space-y-1.5 text-sm">
        <div className="flex items-center justify-between gap-2">
          <span className="text-muted">{t("inspector.elapsed")}</span>
          <span className="mono text-xs">{formatElapsed(snapshot.elapsed_ms)}</span>
        </div>
        <div className="flex items-center justify-between gap-2">
          <span className="text-muted">{t("inspector.verification")}</span>
          <VerificationBadge status={verification} />
        </div>
      </div>

      <div className="mt-3 border-t border-border pt-3">
        <h3 className="mb-1.5 text-sm font-medium">{t("inspector.changedFiles")}</h3>
        {mutatedPaths.length === 0 ? (
          <p className="text-xs text-faint">{t("inspector.changedEmpty")}</p>
        ) : (
          <ul className="space-y-1" data-testid="changed-files">
            {mutatedPaths.map((path) => (
              <li key={path} className="mono break-all text-xs text-text">
                <span className="mr-1 text-success">M</span> {path}
              </li>
            ))}
          </ul>
        )}
        <p className="mt-1.5 text-xs text-faint">{t("inspector.mutatedHint")}</p>
      </div>

      {error ? (
        <div className="mt-3">
          <InlineError
            kind={errorKind(error.code)}
            title={t("inspector.error")}
            message={errorCodeText(error.code, t)}
          />
        </div>
      ) : null}

      <Collapsible.Root className="mt-3 border-t border-border pt-3">
        <Collapsible.Trigger className="flex w-full items-center justify-between text-sm text-muted hover:text-text">
          {t("inspector.advanced")}
          <ChevronDown aria-hidden size={14} />
        </Collapsible.Trigger>
        <Collapsible.Content className="mt-2 space-y-3 text-sm">
          <dl className="grid grid-cols-2 gap-x-3 gap-y-2">
            <InspectorField label={t("inspector.stepCount")} value={String(snapshot.step_count ?? 0)} />
            <InspectorField label={t("inspector.attemptCount")} value={String(snapshot.provider_attempt_count ?? 0)} />
            <InspectorField label={t("inspector.toolCount")} value={String(snapshot.tool_call_count ?? 0)} />
          </dl>
          <div className="space-y-1.5">
            <div className="flex items-center justify-between gap-2">
              <span className="text-muted">{t("inspector.phase")}</span>
              <span className="mono text-xs">{snapshot.phase ? t(`phase.${snapshot.phase}`) : "–"}</span>
            </div>
            {snapshot.stop_reason ? (
              <div className="flex items-center justify-between gap-2">
                <span className="text-muted">{t("inspector.stopReason")}</span>
                <span className="text-xs">{t(`stopReason.${snapshot.stop_reason}`)}</span>
              </div>
            ) : null}
            {snapshot.last_verification?.command ? (
              <div className="text-xs">
                <span className="text-muted">{t("inspector.command")}</span>
                <p className="mono mt-0.5 break-all rounded-sm bg-surface-2 px-2 py-1">
                  {snapshot.last_verification.command}
                  <span className="ml-1 text-faint">= {snapshot.last_verification.exit_code}</span>
                </p>
              </div>
            ) : null}
          </div>
        </Collapsible.Content>
      </Collapsible.Root>

      {mode === "terminal" ? (
        <button type="button" className="btn-primary mt-4 w-full" onClick={onNewTask}>
          {t("inspector.newTask")}
        </button>
      ) : null}
    </aside>
  );
}

function InspectorField({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs text-muted">{label}</dt>
      <dd className="mono mt-0.5 text-sm font-medium" data-testid={`count-${label}`}>
        {value}
      </dd>
    </div>
  );
}
