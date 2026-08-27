import { useEffect, useRef, useState } from "react";
import { CheckCircle2, FolderOpen, Loader2, XCircle } from "lucide-react";
import { api } from "@/api/client";
import { useI18n } from "@/lib/i18n";
import { apiErrorText, errorCodeText } from "@/lib/errorText";
import { cx } from "@/lib/format";

export type WorkspaceState = "empty" | "checking" | "valid" | "invalid";

export interface WorkspaceFieldProps {
  value: string;
  onChange: (value: string) => void;
  onValidated?: (resolvedPath: string | null) => void;
  id?: string;
}

/**
 * Path input with debounced server-side validation and an OS-native
 * "browse" button (the local server hosts the dialog — browsers cannot
 * reveal absolute local paths on their own).
 * Contract states: empty / checking / valid / invalid.
 */
export function WorkspaceField({ value, onChange, onValidated, id }: WorkspaceFieldProps) {
  const { t } = useI18n();
  const [state, setState] = useState<WorkspaceState>(value ? "checking" : "empty");
  const [error, setError] = useState<string | null>(null);
  const [picking, setPicking] = useState(false);
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
          setError(
            result.error?.code
              ? errorCodeText(result.error.code, t)
              : t("workspace.invalid")
          );
          onValidated?.(null);
        }
      } catch (err) {
        if (seq !== requestSeq.current) return;
        setState("invalid");
        setError(apiErrorText(err, t, "workspace.invalid"));
        onValidated?.(null);
      }
    }, 400);
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [value, t, onValidated]);

  const handleBrowse = async () => {
    if (picking) return;
    setPicking(true);
    setError(null);
    try {
      const result = await api.pickWorkspace();
      if (result.cancelled) return;
      if (result.error) {
        setError(errorCodeText(result.error.code, t));
        return;
      }
      if (result.path) onChange(result.path);
    } catch (err) {
      setError(apiErrorText(err, t, "workspace.invalid"));
    } finally {
      setPicking(false);
    }
  };

  const Icon =
    state === "checking"
      ? Loader2
      : state === "valid"
        ? CheckCircle2
        : state === "invalid"
          ? XCircle
          : FolderOpen;

  const hintText =
    state === "valid"
      ? t("workspace.valid")
      : state === "invalid" || error
        ? (error ?? t("workspace.invalid"))
        : t("workspace.help");

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
            onChange={(event) => {
              onChange(event.target.value);
            }}
            aria-invalid={state === "invalid" || error !== null}
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
        <button
          type="button"
          className="btn-secondary shrink-0"
          onClick={handleBrowse}
          disabled={picking}
          aria-label={t("workspace.browse")}
          data-testid="workspace-browse"
        >
          {picking ? (
            <Loader2 aria-hidden size={14} className="animate-spin" />
          ) : (
            <FolderOpen aria-hidden size={14} />
          )}
          {t("workspace.browse")}
        </button>
      </div>
      <p
        id={id ? `${id}-hint` : "workspace-hint"}
        className={cx(
          "mt-1.5 text-xs",
          state === "valid" ? "text-success" : error ? "text-danger" : "text-muted"
        )}
      >
        {hintText}
      </p>
      {state === "invalid" ? <p className="sr-only">{t("workspace.invalid")}</p> : null}
    </div>
  );
}
