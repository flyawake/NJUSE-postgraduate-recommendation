import { AlertTriangle, RefreshCw, ServerCrash, ShieldAlert } from "lucide-react";
import { useI18n } from "@/lib/i18n";
import { cx } from "@/lib/format";

export type InlineErrorKind = "validation" | "configuration" | "transport" | "run_failure";

export interface InlineErrorProps {
  kind: InlineErrorKind;
  title?: string;
  message?: string;
  field?: string | null;
  onRetry?: () => void;
  className?: string;
}

function defaultTitle(kind: InlineErrorKind, t: (key: string) => string): string {
  if (kind === "validation") return t("error.validation");
  if (kind === "configuration") return t("error.configuration");
  if (kind === "transport") return t("error.transport");
  return t("error.runFailure");
}

function recoveryHint(kind: InlineErrorKind, t: (key: string) => string): string {
  if (kind === "validation") return t("error.recovery.hint");
  if (kind === "configuration") return t("error.recovery.config");
  if (kind === "transport") return t("error.recovery.transport");
  return "";
}

/** Inline, screen-reader discoverable error banner (never toast-only). */
export function InlineError({ kind, title, message, field, onRetry, className }: InlineErrorProps) {
  const { t } = useI18n();
  const Icon = kind === "transport" ? ServerCrash : kind === "configuration" ? ShieldAlert : AlertTriangle;
  return (
    <div
      role="alert"
      className={cx(
        "flex items-start gap-2.5 rounded-md border border-danger/40 bg-danger-muted px-3 py-2.5 text-sm text-text",
        className
      )}
    >
      <Icon aria-hidden size={16} className="mt-0.5 shrink-0 text-danger" />
      <div className="min-w-0 flex-1">
        <p className="font-medium text-danger">{title ?? defaultTitle(kind, (k) => t(k))}</p>
        {message ? <p className="mt-0.5 break-words">{message}</p> : null}
        {field ? <p className="mt-0.5 font-mono text-xs text-muted">{field}</p> : null}
        {recoveryHint(kind, (k) => t(k)) ? (
          <p className="mt-0.5 text-xs text-muted">{recoveryHint(kind, (k) => t(k))}</p>
        ) : null}
      </div>
      {onRetry ? (
        <button type="button" className="btn-icon ml-1" onClick={onRetry} aria-label={t("common.retry")}>
          <RefreshCw aria-hidden size={14} />
        </button>
      ) : null}
    </div>
  );
}

export function inlineErrorKind(code: string): InlineErrorKind {
  if (code === "transport_error") return "transport";
  if (code === "invalid_config" || code === "config_corrupt" || code === "config_io_error") {
    return "configuration";
  }
  if (
    code === "invalid_workspace" ||
    code === "invalid_task" ||
    code === "invalid_request" ||
    code === "invalid_profile" ||
    code === "credential_invalid" ||
    code === "credential_not_configured" ||
    code === "credential_env_readonly"
  ) {
    return "validation";
  }
  return "run_failure";
}
