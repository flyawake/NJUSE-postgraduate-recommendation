import { useCallback, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, ApiError } from "@/api/client";
import { useI18n } from "@/lib/i18n";
import { useRunStore } from "@/lib/store";
import { ActivityFeed } from "@/components/ActivityFeed";
import { InlineError } from "@/components/InlineError";
import type { InlineErrorKind } from "@/components/InlineError";
import { ProfileSelector } from "@/components/ProfileSelector";
import { TaskComposer } from "@/components/TaskComposer";
import { WorkspaceField } from "@/components/WorkspaceField";

interface StartError {
  kind: InlineErrorKind;
  message: string;
  field?: string | null;
}

export interface MainPageProps {
  workspace: string;
  workspaceValid: boolean | null;
  onWorkspaceChange: (value: string) => void;
  onWorkspaceValidated: (valid: boolean) => void;
  task: string;
  onTaskChange: (value: string) => void;
  profileId: string | null;
  onProfileChange: (value: string | null) => void;
}

export function MainPage({
  workspace,
  workspaceValid,
  onWorkspaceChange,
  onWorkspaceValidated,
  task,
  onTaskChange,
  profileId,
  onProfileChange,
}: MainPageProps) {
  const { t } = useI18n();
  const store = useRunStore();

  const profilesQuery = useQuery({ queryKey: ["profiles"], queryFn: api.listProfiles, staleTime: 5_000 });
  const bootstrap = useQuery({ queryKey: ["bootstrap"], queryFn: api.bootstrap, staleTime: 30_000 });
  const profiles = profilesQuery.data ?? [];
  const activeProfileId = bootstrap.data?.active_profile_id ?? null;

  const [startError, setStartError] = useState<StartError | null>(null);

  const selectedProfile = profiles.find((profile) => profile.id === (profileId ?? activeProfileId)) ?? null;
  const profileReady = Boolean(selectedProfile?.credential?.configured);

  const running = store.snapshot?.state === "running";
  const cancelling = store.cancelling;

  const canStart = workspaceValid === true && profileReady && !running && !cancelling;
  const startDisabledReason = workspaceValid !== true
    ? t("workspace.invalid")
    : !profileReady
      ? selectedProfile
        ? t("composer.needCredential")
        : t("composer.needSetup")
      : null;

  const handleStart = useCallback(async () => {
    setStartError(null);
    try {
      await store.startRun({ workspace: workspace.trim(), task, profileId: profileId ?? null });
      onTaskChange("");
    } catch (error) {
      if (error instanceof ApiError) {
        const kind: InlineErrorKind =
          error.code === "invalid_config" ||
          error.code === "config_corrupt" ||
          error.code === "config_io_error" ||
          error.code === "credential_not_configured" ||
          error.code === "credential_env_readonly" ||
          error.code === "invalid_workspace" ||
          error.code === "invalid_task" ||
          error.code === "run_already_active"
            ? "validation"
            : "run_failure";
        setStartError({ kind, message: error.message, field: error.field });
      } else {
        setStartError({ kind: "run_failure", message: t("error.runFailure") });
      }
    }
  }, [store, workspace, task, profileId, onTaskChange, t]);

  const handleCancel = useCallback(async () => {
    setStartError(null);
    try {
      await store.cancelRun();
    } catch (error) {
      setStartError({
        kind: "validation",
        message: error instanceof ApiError ? error.message : t("error.runFailure"),
      });
    }
  }, [store, t]);

  return (
    <div className="flex h-full min-h-0 flex-col" data-testid="main-page">
      <div className="min-h-0 flex-1">
        <ActivityFeed
          events={store.events}
          task={store.snapshot?.task ?? null}
          state={
            store.snapshot?.state === "running" || store.snapshot?.state === "terminal"
              ? store.snapshot.state
              : "idle"
          }
          sseStatus={store.sseStatus}
          terminalText={store.snapshot?.final_text}
          verificationStatus={store.snapshot?.verification_status}
        />
      </div>
      <div className="shrink-0 border-t border-border bg-surface/95 px-4 pb-3 pt-3">
        <div className="mx-auto max-w-3xl space-y-2.5">
          <div className="grid grid-cols-1 gap-2.5 md:grid-cols-2">
            <div>
              <p className="mb-1 flex items-center gap-2 text-xs font-medium text-muted">
                {t("workspace.label")}
              </p>
              <WorkspaceField
                value={workspace}
                onChange={(value) => {
                  onWorkspaceChange(value);
                  onWorkspaceValidated(false);
                }}
                onValidated={(resolved) => onWorkspaceValidated(resolved !== null)}
              />
            </div>
            <div>
              <p className="mb-1 text-xs font-medium text-muted">{t("profile.label")}</p>
              <ProfileSelector
                profiles={profiles}
                activeProfileId={activeProfileId}
                value={profileId}
                onChange={onProfileChange}
              />
            </div>
          </div>
          {startError ? (
            <InlineError
              kind={startError.kind}
              message={startError.message}
              field={startError.field}
              onRetry={handleStart}
            />
          ) : null}
          <TaskComposer
            task={task}
            onTaskChange={onTaskChange}
            state={
              running
                ? cancelling
                  ? "cancelling"
                  : "running"
                : canStart
                  ? task.trim()
                    ? "ready"
                    : "idle"
                  : "invalid"
            }
            error={null}
            onStart={handleStart}
            onCancel={handleCancel}
            startDisabledReason={startDisabledReason}
          />
        </div>
      </div>
    </div>
  );
}
