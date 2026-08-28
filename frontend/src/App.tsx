import { useCallback, useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";
import type { Profile } from "@/api/client";
import { useI18n } from "@/lib/i18n";
import { useRunCommands, useRunMeta } from "@/lib/store";
import { AppShell } from "@/components/AppShell";
import type { ViewName } from "@/components/AppShell";
import { RunInspector } from "@/components/RunInspector";
import { Onboarding } from "@/components/Onboarding";
import { SettingsPage } from "@/components/SettingsPage";
import { AboutSecurityPage } from "@/components/AboutSecurityPage";
import { MainPage } from "@/pages/MainPage";

const WS_STORAGE_KEY = "coding-agent-ui-workspace";
const PROFILE_STORAGE_KEY = "coding-agent-ui-profile";
const ONBOARDING_DISMISSED_KEY = "coding-agent-ui-onboarding-dismissed";

export function App() {
  const { t } = useI18n();
  const { snapshot } = useRunMeta();
  const { clear } = useRunCommands();
  const [view, setView] = useState<ViewName>("new");
  const [workspace, setWorkspace] = useState<string>(() => {
    try {
      return localStorage.getItem(WS_STORAGE_KEY) ?? "";
    } catch {
      return "";
    }
  });
  const [workspaceValid, setWorkspaceValid] = useState<boolean | null>(null);
  const [profileId, setProfileId] = useState<string | null>(() => {
    try {
      return localStorage.getItem(PROFILE_STORAGE_KEY);
    } catch {
      return null;
    }
  });
  const [onboardingDismissed, setOnboardingDismissed] = useState<boolean>(() => {
    try {
      return sessionStorage.getItem(ONBOARDING_DISMISSED_KEY) === "1";
    } catch {
      return false;
    }
  });

  useEffect(() => {
    try {
      localStorage.setItem(WS_STORAGE_KEY, workspace);
    } catch {
      /* best effort */
    }
  }, [workspace]);

  useEffect(() => {
    try {
      if (profileId) localStorage.setItem(PROFILE_STORAGE_KEY, profileId);
      else localStorage.removeItem(PROFILE_STORAGE_KEY);
    } catch {
      /* best effort */
    }
  }, [profileId]);

  const profilesQuery = useQuery({ queryKey: ["profiles"], queryFn: api.listProfiles, staleTime: 5_000 });
  const bootstrap = useQuery({ queryKey: ["bootstrap"], queryFn: api.bootstrap, staleTime: 30_000 });
  const profiles = profilesQuery.data ?? [];
  const activeProfileId = bootstrap.data?.active_profile_id ?? null;

  const selectedProfile: Profile | undefined = profiles.find(
    (profile) => profile.id === (profileId ?? activeProfileId)
  );
  const profileLabel = selectedProfile
    ? `${selectedProfile.display_name} · ${selectedProfile.model}`
    : null;

  const showOnboarding =
    profilesQuery.isSuccess && profiles.length === 0 && !onboardingDismissed;

  const handleDismissOnboarding = useCallback(() => {
    setOnboardingDismissed(true);
    try {
      sessionStorage.setItem(ONBOARDING_DISMISSED_KEY, "1");
    } catch {
      /* best effort */
    }
  }, []);

  const inspector = (
    <RunInspector
      snapshot={snapshot}
      onNewTask={() => {
        clear();
        setView("new");
      }}
    />
  );

  return (
    <div className="h-full">
      {showOnboarding ? <Onboarding onDone={handleDismissOnboarding} /> : null}
      <AppShell
        view={view}
        onNavigate={setView}
        workspace={workspace}
        profileLabel={profileLabel}
        inspector={inspector}
        badge={snapshot?.state === "running" ? t("status.running") : null}
      >
        {view === "settings" ? (
          <SettingsPage />
        ) : view === "about" ? (
          <AboutSecurityPage />
        ) : (
          <MainPage
            workspace={workspace}
            workspaceValid={workspaceValid}
            onWorkspaceChange={setWorkspace}
            onWorkspaceValidated={setWorkspaceValid}
            profileId={profileId}
            onProfileChange={setProfileId}
          />
        )}
      </AppShell>
    </div>
  );
}
