import { useEffect, useState } from "react";
import { Eye, EyeOff, KeyRound, Trash2 } from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "@/api/client";
import type { Profile } from "@/api/client";
import { useI18n } from "@/lib/i18n";
import { cx } from "@/lib/format";
import { InlineError } from "./InlineError";

export type CredentialState = "absent" | "local-writable" | "env-readonly";

export interface CredentialFieldProps {
  profile: Profile;
}

/**
 * Write-only credential editor: set/replace/unset; the read side only ever
 * shows configured/source/writable. Env-provided refs are read-only.
 */
export function CredentialField({ profile }: CredentialFieldProps) {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const [secret, setSecret] = useState("");
  const [show, setShow] = useState(false);
  const [feedback, setFeedback] = useState<{ kind: "error" | "ok"; text: string } | null>(null);

  const credentialQuery = useQuery({
    queryKey: ["credential", profile.id],
    queryFn: () => api.getCredential(profile.id),
    staleTime: 5_000,
  });

  const info = credentialQuery.data;
  const state: CredentialState = !info
    ? "absent"
    : info.source === "env"
      ? "env-readonly"
      : info.configured
        ? "local-writable"
        : "absent";

  useEffect(() => {
    if (feedback) {
      const timer = setTimeout(() => setFeedback(null), 4000);
      return () => clearTimeout(timer);
    }
  }, [feedback]);

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["credential", profile.id] });
    queryClient.invalidateQueries({ queryKey: ["profiles"] });
    queryClient.invalidateQueries({ queryKey: ["bootstrap"] });
  };

  const setMutation = useMutation({
    mutationFn: (value: string) => api.setCredential(profile.id, value),
    onSuccess: () => {
      setSecret("");
      setFeedback({ kind: "ok", text: t("settings.credential.saved") });
      refresh();
    },
    onError: (error: unknown) => {
      setFeedback({
        kind: "error",
        text: error instanceof ApiError ? error.message : t("error.validation"),
      });
    },
  });

  const clearMutation = useMutation({
    mutationFn: () => api.unsetCredential(profile.id),
    onSuccess: () => {
      setFeedback({ kind: "ok", text: t("settings.credential.cleared") });
      refresh();
    },
    onError: (error: unknown) => {
      setFeedback({
        kind: "error",
        text: error instanceof ApiError ? error.message : t("error.validation"),
      });
    },
  });

  const busy = setMutation.isPending || clearMutation.isPending;
  const envReadonly = state === "env-readonly";

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <KeyRound aria-hidden size={14} className="text-faint" />
        <span className="text-sm font-medium">{t("settings.credential.title")}</span>
        <span
          className={cx(
            "rounded-sm px-1.5 py-0.5 text-[11px] font-medium",
            envReadonly ? "bg-warning-muted text-warning" : info?.configured ? "bg-success-muted text-success" : "bg-surface-2 text-muted"
          )}
        >
          {envReadonly
            ? t("settings.credential.envProvided")
            : info?.configured
              ? t("settings.credential.replace")
              : t("settings.credential.set")}
        </span>
      </div>

      {envReadonly ? (
        <p className="text-xs text-muted">
          {t("settings.credential.envHint", { var: "environment" })}
        </p>
      ) : null}

      <div className="flex items-center gap-2" data-testid={`credential-input-${profile.id}`}>
        <div className="relative flex-1">
          <label htmlFor={`credential-${profile.id}`} className="sr-only">
            {t("settings.credential.placeholder")}
          </label>
          <input
            id={`credential-${profile.id}`}
            type={show ? "text" : "password"}
            className={cx("input pr-20", envReadonly && "cursor-not-allowed opacity-60")}
            placeholder={t("settings.credential.placeholder")}
            value={secret}
            onChange={(event) => setSecret(event.target.value)}
            disabled={envReadonly || busy}
            autoComplete="new-password"
            spellCheck={false}
          />
          <button
            type="button"
            className="btn-icon absolute right-1 top-1/2 -translate-y-1/2"
            onClick={() => setShow((value) => !value)}
            disabled={envReadonly}
            aria-label={show ? "Hide" : "Show"}
          >
            {show ? <EyeOff aria-hidden size={14} /> : <Eye aria-hidden size={14} />}
          </button>
        </div>
        <button
          type="button"
          className="btn-secondary shrink-0"
          onClick={() => setMutation.mutate(secret.trim())}
          disabled={envReadonly || busy || !secret.trim()}
        >
          {info?.configured ? t("settings.credential.replace") : t("settings.credential.set")}
        </button>
        <button
          type="button"
          className="btn-icon shrink-0"
          onClick={() => clearMutation.mutate()}
          disabled={envReadonly || busy || !info?.configured}
          aria-label={t("settings.credential.clear")}
          title={t("settings.credential.clear")}
        >
          <Trash2 aria-hidden size={14} />
        </button>
      </div>

      <p className="text-xs text-faint">{t("settings.credential.help")}</p>
      {feedback ? (
        feedback.kind === "error" ? (
          <InlineError kind="validation" message={feedback.text} />
        ) : (
          <p className="text-xs font-medium text-success" role="status">
            {feedback.text}
          </p>
        )
      ) : null}
    </div>
  );
}
