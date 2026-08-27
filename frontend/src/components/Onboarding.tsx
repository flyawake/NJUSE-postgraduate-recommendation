import { useState } from "react";
import { ArrowRight, Sparkles, X } from "lucide-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import type { Profile } from "@/api/client";
import { useI18n } from "@/lib/i18n";
import { cx } from "@/lib/format";
import { ProfileForm } from "./ProfileForm";
import { CredentialField } from "./CredentialField";

export interface OnboardingProps {
  onDone: () => void;
}

/**
 * First-use onboarding when no profile exists yet:
 * provider -> URL/model -> credential -> save, dismissible at any time and
 * resumable from the settings page.
 */
export function Onboarding({ onDone }: OnboardingProps) {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const [createdId, setCreatedId] = useState<string | null>(null);
  const profilesQuery = useQuery({ queryKey: ["profiles"], queryFn: api.listProfiles, staleTime: 5_000 });
  const bootstrap = useQuery({ queryKey: ["bootstrap"], queryFn: api.bootstrap, staleTime: 30_000 });

  const created: Profile | undefined = (profilesQuery.data ?? []).find((p) => p.id === createdId);
  const presets = bootstrap.data?.provider_presets ?? [];
  const step = createdId ? 2 : 1;
  const stepLabels = [t("onboarding.step1"), t("onboarding.step2")];

  return (
    <div className="fixed inset-0 z-30 flex items-center justify-center bg-bg p-4">
      <div className="w-full max-w-2xl rounded-lg border border-border bg-surface p-6 shadow-md">
        <div className="mb-4 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Sparkles aria-hidden size={18} className="text-accent" />
            <h1 className="text-base font-semibold">{t("onboarding.title")}</h1>
          </div>
          <button type="button" className="btn-icon" onClick={onDone} aria-label={t("settings.cancel")}>
            <X aria-hidden size={16} />
          </button>
        </div>

        <ol className="mb-5 flex flex-wrap items-center gap-2 text-xs">
          {stepLabels.map((label, index) => {
            const done = step > index + 1;
            const active = step === index + 1;
            return (
              <li key={label} className="flex items-center gap-2">
                <span
                  className={cx(
                    "rounded-sm px-2 py-1 font-medium",
                    done && "bg-success-muted text-success",
                    active && "bg-accent-muted text-accent",
                    !done && !active && "text-faint"
                  )}
                  aria-current={active ? "step" : undefined}
                >
                  {label}
                </span>
                {index < stepLabels.length - 1 ? <ArrowRight aria-hidden size={12} className="text-faint" /> : null}
              </li>
            );
          })}
        </ol>

        {created ? (
          <div className="space-y-4">
            {created.credential_ref ? (
              <div className="rounded-md border border-border bg-surface-2 p-4">
                <CredentialField profile={created} />
              </div>
            ) : (
              <p className="text-sm text-muted" role="status">
                {t("onboarding.noCredentialRef")}
              </p>
            )}
            <div className="flex items-center justify-between">
              <p className="text-xs text-faint">{t("onboarding.resume")}</p>
              <button type="button" className="btn-primary" onClick={onDone} data-testid="onboarding-done">
                {t("onboarding.finish")}
              </button>
            </div>
          </div>
        ) : presets.length === 0 ? (
          <p className="text-sm text-muted" role="status">
            {t("settings.loading")}
          </p>
        ) : (
          <ProfileForm
            mode="create"
            presets={presets}
            onSaved={(profileId) => {
              setCreatedId(profileId);
              queryClient.invalidateQueries({ queryKey: ["profiles"] });
              queryClient.invalidateQueries({ queryKey: ["bootstrap"] });
            }}
            onCancel={onDone}
          />
        )}
        <p className="mt-4 text-xs text-muted">{t("onboarding.needSetup")}</p>
      </div>
    </div>
  );
}
