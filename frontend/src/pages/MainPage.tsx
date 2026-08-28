import { memo, useCallback, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, ApiError } from "@/api/client";
import { useI18n } from "@/lib/i18n";
import { apiErrorText } from "@/lib/errorText";
import { useRunCommands, useRunEventFeed, useRunMeta } from "@/lib/store";
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
  profileId: string | null;
  onProfileChange: (value: string | null) => void;
}

/**
 * The task draft is intentionally local. Run events are consumed only by the
 * transcript child below, so typing and incoming SSE data never resubscribe
 * the workspace/profile controls to each other.
 */
export const MainPage = memo(function MainPage({
  workspace,
  workspaceValid,
  onWorkspaceChange,
  onWorkspaceValidated,
  profileId,
  onProfileChange,
}: MainPageProps) {
  const { t } = useI18n();
  const { snapshot, cancelling } = useRunMeta();
  const { startRun, cancelRun } = useRunCommands();
  const [task, setTask] = useState("");
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState<StartError | null>(null);
  const cancelRequested = useRef(false);
  const profilesQuery = useQuery({ queryKey: ["profiles"], queryFn: api.listProfiles, staleTime: 5_000 });
  const bootstrap = useQuery({ queryKey: ["bootstrap"], queryFn: api.bootstrap, staleTime: 30_000 });
  const profiles = profilesQuery.data ?? [];
  const activeProfileId = bootstrap.data?.active_profile_id ?? null;
  const selectedProfile = profiles.find((profile) => profile.id === (profileId ?? activeProfileId)) ?? null;
  const profileReady = Boolean(selectedProfile?.credential?.configured);
  const running = snapshot?.state === "running";
  const canStart = workspaceValid === true && profileReady && !running && !cancelling && !starting;
  const startDisabledReason = workspaceValid !== true
    ? t("workspace.invalid")
    : !profileReady
      ? selectedProfile
        ? t("composer.needCredential")
        : t("composer.needSetup")
      : null;

  const handleWorkspaceChange = useCallback(
    (value: string) => {
      onWorkspaceChange(value);
      onWorkspaceValidated(false);
    },
    [onWorkspaceChange, onWorkspaceValidated]
  );
  const handleWorkspaceValidated = useCallback(
    (resolvedPath: string | null) => onWorkspaceValidated(resolvedPath !== null),
    [onWorkspaceValidated]
  );

  const handleStart = useCallback(async () => {
    if (!canStart || !task.trim()) return;
    setStarting(true);
    cancelRequested.current = false;
    setStartError(null);
    try {
      await startRun({ workspace: workspace.trim(), task, profileId: profileId ?? null });
      setTask("");
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
        setStartError({ kind, message: apiErrorText(error, t), field: error.field });
      } else {
        setStartError({ kind: "run_failure", message: t("error.runFailure") });
      }
    } finally {
      setStarting(false);
    }
  }, [canStart, profileId, startRun, t, task, workspace]);

  const handleCancel = useCallback(async () => {
    if (cancelRequested.current) return;
    cancelRequested.current = true;
    setStartError(null);
    try {
      await cancelRun();
    } catch (error) {
      cancelRequested.current = false;
      setStartError({ kind: "validation", message: apiErrorText(error, t) });
    }
  }, [cancelRun, t]);

  return (
    <div className="flex h-full min-h-0 flex-col" data-testid="main-page">
      <div className="min-h-0 flex-1">
        <ActivityFeedSection />
      </div>
      <div className="shrink-0 border-t border-border bg-surface/95 px-4 pb-16 pt-3 sm:pb-3">
        <div className="mx-auto max-w-[54rem] space-y-2.5">
          <div className="grid grid-cols-1 gap-2 rounded-md bg-surface-2/60 p-2 sm:grid-cols-[minmax(0,1fr)_minmax(12rem,0.75fr)]">
            <WorkspaceField
              value={workspace}
              onChange={handleWorkspaceChange}
              onValidated={handleWorkspaceValidated}
            />
            <ProfileSelector
              profiles={profiles}
              activeProfileId={activeProfileId}
              value={profileId}
              onChange={onProfileChange}
            />
          </div>
          {startError ? <InlineError kind={startError.kind} message={startError.message} field={startError.field} /> : null}
          <TaskComposer
            task={task}
            onTaskChange={setTask}
            state={
              running
                ? cancelling
                  ? "cancelling"
                  : "running"
                : starting
                  ? "starting"
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
});

/** The sole high-frequency RunEventStore consumer in the main product path. */
const ActivityFeedSection = memo(function ActivityFeedSection() {
  const { retainedEvents, appendedEvents, resetVersion, version } = useRunEventFeed();
  const { snapshot, sseStatus } = useRunMeta();
  return (
    <ActivityFeed
      retainedEvents={retainedEvents}
      eventBatch={appendedEvents}
      resetVersion={resetVersion}
      eventVersion={version}
      task={snapshot?.task ?? null}
      state={snapshot?.state === "running" || snapshot?.state === "terminal" ? snapshot.state : "idle"}
      sseStatus={sseStatus}
      terminalText={snapshot?.final_text}
      verificationStatus={snapshot?.verification_status}
    />
  );
});
