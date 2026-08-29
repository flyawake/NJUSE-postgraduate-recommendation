import { useCallback, useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { MessageSquarePlus } from "lucide-react";
import { api } from "@/api/client";
import type { Conversation, FileChange, Profile, Turn } from "@/api/client";
import { AppShell } from "@/components/AppShell";
import type { ViewName } from "@/components/AppShell";
import { ArtifactPreviewPane } from "@/components/ArtifactPreviewPane";
import { ConversationSidebar } from "@/components/ConversationSidebar";
import { ConversationView } from "@/components/ConversationView";
import { CreateConversationDialog } from "@/components/CreateConversationDialog";
import { Onboarding } from "@/components/Onboarding";
import { SettingsPage } from "@/components/SettingsPage";
import { AboutSecurityPage } from "@/components/AboutSecurityPage";
import { MemoryPage } from "@/components/MemoryPage";
import { useI18n } from "@/lib/i18n";

const WS_STORAGE_KEY = "coding-agent-ui-workspace";
const PROFILE_STORAGE_KEY = "coding-agent-ui-profile";
const ONBOARDING_DISMISSED_KEY = "coding-agent-ui-onboarding-dismissed";

interface ArtifactSelection {
  conversationId: string;
  turn: Turn;
  file: FileChange;
  files: FileChange[];
  trigger: HTMLElement | null;
}

function initialConversationId(): string | null {
  try {
    return new URL(window.location.href).searchParams.get("conversation");
  } catch {
    return null;
  }
}

export function App() {
  const { t } = useI18n();
  const [view, setView] = useState<ViewName>("conversations");
  const [selectedConversationId, setSelectedConversationId] = useState<string | null>(initialConversationId);
  const [createOpen, setCreateOpen] = useState(false);
  const [artifact, setArtifact] = useState<ArtifactSelection | null>(null);
  const [workspace, setWorkspace] = useState(() => {
    try { return localStorage.getItem(WS_STORAGE_KEY) ?? ""; } catch { return ""; }
  });
  const [profileId, setProfileId] = useState<string | null>(() => {
    try { return localStorage.getItem(PROFILE_STORAGE_KEY); } catch { return null; }
  });
  const [onboardingDismissed, setOnboardingDismissed] = useState(() => {
    try { return sessionStorage.getItem(ONBOARDING_DISMISSED_KEY) === "1"; } catch { return false; }
  });

  useEffect(() => {
    try { localStorage.setItem(WS_STORAGE_KEY, workspace); } catch { /* best effort */ }
  }, [workspace]);
  useEffect(() => {
    try {
      if (profileId) localStorage.setItem(PROFILE_STORAGE_KEY, profileId);
      else localStorage.removeItem(PROFILE_STORAGE_KEY);
    } catch { /* best effort */ }
  }, [profileId]);

  const profilesQuery = useQuery({ queryKey: ["profiles"], queryFn: api.listProfiles, staleTime: 5_000 });
  const bootstrap = useQuery({ queryKey: ["bootstrap"], queryFn: api.bootstrap, staleTime: 30_000 });
  const profiles = profilesQuery.data ?? [];
  const activeProfileId = bootstrap.data?.active_profile_id ?? null;
  const selectedProfile: Profile | undefined = profiles.find((item) => item.id === (profileId ?? activeProfileId));
  const profileLabel = selectedProfile ? `${selectedProfile.display_name} · ${selectedProfile.model}` : null;

  const selectConversation = useCallback((conversation: Conversation | null) => {
    setArtifact(null);
    setSelectedConversationId(conversation?.id ?? null);
    setView("conversations");
    if (conversation) {
      setWorkspace(conversation.workspace_path);
      setProfileId(conversation.profile_id ?? null);
    }
    try {
      const url = new URL(window.location.href);
      if (conversation) url.searchParams.set("conversation", conversation.id);
      else url.searchParams.delete("conversation");
      window.history.replaceState(null, "", url);
    } catch { /* non-browser test environment */ }
  }, []);

  const openMemorySource = useCallback(async (conversationId: string, turnId?: string | null) => {
    let conversation: Conversation;
    try {
      conversation = await api.getConversation(conversationId);
    } catch {
      return;
    }
    selectConversation(conversation);
    if (turnId) {
      try {
        const url = new URL(window.location.href);
        url.searchParams.set("turn", turnId);
        window.history.replaceState(null, "", url);
      } catch { /* non-browser test environment */ }
      requestAnimationFrame(() => requestAnimationFrame(() => {
        const target = document.querySelector<HTMLElement>(`[data-testid="turn-${turnId}"]`);
        target?.scrollIntoView({ block: "center" });
        target?.focus({ preventScroll: true });
      }));
    }
  }, [selectConversation]);

  const closeArtifact = useCallback(() => {
    const trigger = artifact?.trigger;
    setArtifact(null);
    queueMicrotask(() => trigger?.focus());
  }, [artifact]);

  const showOnboarding = profilesQuery.isSuccess && profiles.length === 0 && !onboardingDismissed;
  const handleDismissOnboarding = useCallback(() => {
    setOnboardingDismissed(true);
    try { sessionStorage.setItem(ONBOARDING_DISMISSED_KEY, "1"); } catch { /* best effort */ }
  }, []);

  return (
    <div className="h-full">
      {showOnboarding ? <Onboarding onDone={handleDismissOnboarding} /> : null}
      <AppShell
        view={view}
        onNavigate={setView}
        workspace={workspace}
        profileLabel={profileLabel}
        inspector={artifact ? (
          <ArtifactPreviewPane
            conversationId={artifact.conversationId}
            turnId={artifact.turn.id}
            file={artifact.file}
            files={artifact.files}
            onSelectFile={(file) => setArtifact((current) => current ? { ...current, file } : current)}
            onClose={closeArtifact}
          />
        ) : null}
        conversationSidebar={
          <ConversationSidebar
            selectedId={selectedConversationId}
            onSelect={selectConversation}
            onCreate={() => setCreateOpen(true)}
            onDeleted={(id) => { if (id === selectedConversationId) selectConversation(null); }}
          />
        }
      >
        {view === "settings" ? <SettingsPage /> : view === "about" ? <AboutSecurityPage /> : view === "memories" ? <MemoryPage defaultWorkspace={workspace} onOpenSource={openMemorySource} /> : selectedConversationId ? (
          <ConversationView
            key={selectedConversationId}
            conversationId={selectedConversationId}
            onOpenArtifact={(turn, file, files) => setArtifact({
              conversationId: selectedConversationId,
              turn,
              file,
              files,
              trigger: document.activeElement instanceof HTMLElement ? document.activeElement : null,
            })}
            onOpenMemorySource={openMemorySource}
          />
        ) : (
          <div className="flex h-full flex-col items-center justify-center gap-3 px-6 text-center">
            <MessageSquarePlus aria-hidden size={30} className="text-accent" />
            <h1 className="text-lg font-semibold">{t("conversation.emptyTitle")}</h1>
            <p className="max-w-md text-sm text-muted">{t("conversation.emptyDescription")}</p>
            <button type="button" className="btn-primary" onClick={() => setCreateOpen(true)}>{t("nav.newConversation")}</button>
          </div>
        )}
      </AppShell>
      <CreateConversationDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        initialWorkspace={workspace}
        initialProfileId={profileId}
        activeProfileId={activeProfileId}
        profiles={profiles}
        onCreated={selectConversation}
      />
    </div>
  );
}
