import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { PanelRightClose, PanelRightOpen } from "lucide-react";
import { useI18n } from "@/lib/i18n";
import { TopBar } from "./AppShellTopBar";
import { Sidebar } from "./AppShellSidebar";

export type ViewName = "conversations" | "memories" | "settings" | "about";

export interface AppShellProps {
  view: ViewName;
  onNavigate: (view: ViewName) => void;
  workspace: string;
  profileLabel: string | null;
  /** Right-hand inspector content; on narrow screens it becomes a drawer.
   *  Pass null to render a true two-column shell (no blank right pane). */
  inspector: ReactNode | null;
  /** Conversation navigation rendered inside the left sidebar. */
  conversationSidebar?: ReactNode;
  children: ReactNode;
}

/**
 * Desktop layout: top bar / left sidebar / central feed / right inspector.
 * Under `lg` the inspector becomes a non-modal drawer; the sidebar collapses.
 */
export function AppShell({
  view,
  onNavigate,
  workspace,
  profileLabel,
  inspector,
  conversationSidebar,
  children,
}: AppShellProps) {
  const { t } = useI18n();
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => window.innerWidth < 640);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [sidebarDrawerOpen, setSidebarDrawerOpen] = useState(false);

  useEffect(() => {
    const collapseForNarrowViewport = () => {
      if (window.innerWidth < 640) setSidebarCollapsed(true);
    };
    window.addEventListener("resize", collapseForNarrowViewport);
    return () => window.removeEventListener("resize", collapseForNarrowViewport);
  }, []);

  useEffect(() => {
    if (inspector && window.innerWidth < 1024) setDrawerOpen(true);
    if (!inspector) setDrawerOpen(false);
  }, [inspector]);

  return (
    <div className="app-backdrop flex h-full min-h-0 flex-col">
      <TopBar
        workspace={workspace}
        profileLabel={profileLabel}
        onOpenSettings={() => onNavigate("settings")}
        sidebarCollapsed={sidebarCollapsed}
        onToggleSidebar={() => {
          if (window.innerWidth < 640) setSidebarDrawerOpen(true);
          else setSidebarCollapsed((value) => !value);
        }}
      />
      <div className="flex min-h-0 flex-1">
        <div className="hidden shrink-0 sm:flex">
          <Sidebar view={view} collapsed={sidebarCollapsed} onNavigate={onNavigate}>
            {conversationSidebar}
          </Sidebar>
        </div>
        <main className="workspace-surface relative flex min-h-0 min-w-0 flex-1 flex-col overflow-y-auto sm:rounded-tl-[18px] sm:border-l sm:border-t sm:border-border/70">
          {children}
        </main>
        {inspector ? (
          <>
            <aside className="glass-panel hidden w-[min(40rem,42vw)] shrink-0 overflow-hidden border-l border-t border-border/70 lg:block">
              {inspector}
            </aside>

            {/* Narrow screens: inspector becomes a drawer (Escape closes it, never cancels runs). */}
            <Dialog.Root open={drawerOpen} onOpenChange={setDrawerOpen}>
              <Dialog.Portal>
                <Dialog.Overlay
                  className="fixed inset-0 z-40 bg-overlay lg:hidden"
                  data-state={drawerOpen ? "open" : "closed"}
                />
                <Dialog.Content
                  className="glass-panel fixed inset-y-0 right-0 z-50 w-[min(36rem,94vw)] overflow-y-auto border-l border-border shadow-md outline-none lg:hidden"
                  aria-label={t("inspector.title")}
                >
                  <div className="sr-only">
                    <Dialog.Title className="text-sm font-semibold">{t("inspector.title")}</Dialog.Title>
                    <Dialog.Close asChild>
                      <button type="button" className="btn-icon" aria-label={t("shell.closeInspector")}>
                        <PanelRightClose aria-hidden size={16} />
                      </button>
                    </Dialog.Close>
                  </div>
                  {inspector}
                </Dialog.Content>
              </Dialog.Portal>
            </Dialog.Root>
          </>
        ) : null}
      </div>

      <Dialog.Root open={sidebarDrawerOpen} onOpenChange={setSidebarDrawerOpen}>
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 z-40 bg-overlay sm:hidden" />
          <Dialog.Content className="glass-chrome fixed inset-y-0 left-0 z-50 w-[min(19rem,90vw)] border-r border-border shadow-md outline-none sm:hidden">
            <Dialog.Title className="sr-only">{t("nav.conversations")}</Dialog.Title>
            <Sidebar
              view={view}
              collapsed={false}
              onNavigate={(nextView) => {
                onNavigate(nextView);
                setSidebarDrawerOpen(false);
              }}
              drawer
            >
              {conversationSidebar}
            </Sidebar>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>

      {/* The top bar already owns the sidebar toggle. Only the contextual
          inspector needs a floating narrow-screen control. */}
      {inspector ? (
        <div className="pointer-events-none fixed bottom-4 right-4 z-30 flex gap-2 lg:hidden">
          <button
            type="button"
            className="btn-secondary pointer-events-auto shadow-md"
            onClick={() => setDrawerOpen(true)}
            aria-label={t("shell.openInspector")}
          >
            <PanelRightOpen aria-hidden size={16} />
          </button>
        </div>
      ) : null}
    </div>
  );
}
