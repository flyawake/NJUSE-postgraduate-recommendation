import { useEffect, useRef, useState } from "react";
import { CheckCircle2, FolderOpen, Loader2, XCircle } from "lucide-react";
import { api, ApiError } from "@/api/client";
import { useI18n } from "@/lib/i18n";
import { cx } from "@/lib/format";

export type WorkspaceState = "empty" | "checking" | "valid" | "invalid";

export interface WorkspaceFieldProps {
  value: string;
  onChange: (value: string) => void;
  onValidated?: (resolvedPath: string | null) => void;
  id?: string;
}

/**
 * Path input with debounced server-side validation.
 * Contract states: empty / checking / valid / invalid.
 */
export function WorkspaceField({ value, onChange, onValidated, id }: WorkspaceFieldProps) {
  const { t } = useI18n();
  const [state, setState] = useState<WorkspaceState>(value ? "checking" : "empty");
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const requestSeq = useRef(0);

  useEffect(() => {
    if (!value.trim()) {
      setState("empty");
      setError(null);
      onValidated?.(null);
      return;
    }
    setState("checking");
    setError(null);
    const seq = ++requestSeq.current;
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(async () => {
      try {
        const result = await api.validateWorkspace(value.trim());
        if (seq !== requestSeq.current) return;
        if (result.valid) {
          setState("valid");
          onValidated?.(result.resolved_path ?? null);
        } else {
          setState("invalid");
          setError(result.error?.message ?? t("workspace.invalid"));
          onValidated?.(null);
        }
      } catch (err) {
        if (seq !== requestSeq.current) return;
        setState("invalid");
        setError(err instanceof ApiError ? err.message : t("workspace.invalid"));
        onValidated?.(null);
      }
    }, 400);
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [value, t, onValidated]);

  const Icon =
    state === "checking"
      ? Loader2
      : state === "valid"
        ? CheckCircle2
        : state === "invalid"
          ? XCircle
          : FolderOpen;

  return (
    <div>
      <div className="flex items-end gap-2">
        <label htmlFor={id ?? "workspace"} className="sr-only">
          {t("workspace.label")}
        </label>
        <div className="relative flex-1">
          <input
            id={id ?? "workspace"}
            type="text"
            className={cx("input pr-9", state === "invalid" && "border-danger")}
            placeholder={t("workspace.placeholder")}
            value={value}
            onChange={(event) => onChange(event.target.value)}
            aria-invalid={state === "invalid"}
            aria-describedby={id ? `${id}-hint` : "workspace-hint"}
            autoComplete="off"
            spellCheck={false}
          />
          <span
            className={cx(
              "absolute right-2.5 top-1/2 -translate-y-1/2",
              state === "valid" && "text-success",
              state === "invalid" && "text-danger",
              state === "checking" && "text-faint"
            )}
            aria-hidden
          >
            <Icon size={16} className={state === "checking" ? "animate-spin" : ""} />
          </span>
        </div>
      </div>
      <p id={id ? `${id}-hint` : "workspace-hint"} className="mt-1.5 text-xs text-muted">
        {state === "valid"
          ? t("workspace.valid")
          : state === "invalid"
            ? (error ?? t("workspace.invalid"))
            : t("workspace.help")}
      </p>
      {state === "invalid" ? <p className="sr-only">{t("workspace.invalid")}</p> : null}
    </div>
  );
}
