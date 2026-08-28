import { useState } from "react";
import { CheckCircle2, FolderOpen, Loader2, RotateCcw, XCircle } from "lucide-react";
import { api } from "@/api/client";
import { useI18n } from "@/lib/i18n";
import { apiErrorText, errorCodeText } from "@/lib/errorText";
import { cx } from "@/lib/format";
import { useWorkspaceValidation } from "@/lib/workspaceValidation";

export type WorkspaceState = "empty" | "debouncing" | "checking" | "valid" | "invalid" | "error";

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
  const validation = useWorkspaceValidation(value, onValidated);
  const [pickerError, setPickerError] = useState<string | null>(null);
  const [picking, setPicking] = useState(false);
  const state = validation.status;
  const error = pickerError ?? (validation.errorCode ? errorCodeText(validation.errorCode, t) : null);

  const handleBrowse = async () => {
    if (picking) return;
    setPicking(true);
    setPickerError(null);
    try {
      const result = await api.pickWorkspace();
      if (result.cancelled) return;
      if (result.error) {
        setPickerError(errorCodeText(result.error.code, t));
        return;
      }
      if (result.path) onChange(result.path);
    } catch (err) {
      setPickerError(apiErrorText(err, t, "workspace.invalid"));
    } finally {
      setPicking(false);
    }
  };

  const Icon =
    state === "checking" || state === "debouncing"
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
            className={cx("input pr-9", (state === "invalid" || state === "error") && "border-danger")}
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
              (state === "invalid" || state === "error") && "text-danger",
              (state === "checking" || state === "debouncing") && "text-faint"
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
      {state === "error" ? (
        <button
          type="button"
          className="btn-secondary mt-2"
          onClick={validation.retry}
          data-testid="workspace-retry"
        >
          <RotateCcw aria-hidden size={14} />
          {t("workspace.retry")}
        </button>
      ) : null}
    </div>
  );
}
