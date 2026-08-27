import { useState } from "react";
import type { ReactNode } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { PanelLeft, PanelRightClose, PanelRightOpen } from "lucide-react";
import { useI18n } from "@/lib/i18n";
import { TopBar } from "./AppShellTopBar";
import { Sidebar } from "./AppShellSidebar";

export type ViewName = "new" | "current" | "settings" | "about";

export interface AppShellProps {
  view: ViewName;
  onNavigate: (view: ViewName) => void;
  workspace: string;
  profileLabel: string | null;
  /** Right-hand inspector content; on narrow screens it becomes a drawer. */
  inspector: ReactNode;
  children: ReactNode;
  badge?: string | null;
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
  children,
  badge,
}: AppShellProps) {
  const { t } = useI18n();
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <TopBar
        workspace={workspace}
        profileLabel={profileLabel}
        onOpenSettings={() => onNavigate("settings")}
        sidebarCollapsed={sidebarCollapsed}
        onToggleSidebar={() => setSidebarCollapsed((value) => !value)}
      />
      <div className="flex min-h-0 flex-1">
        <Sidebar view={view} collapsed={sidebarCollapsed} onNavigate={onNavigate} badge={badge} />
        <main className="relative flex min-w-0 flex-1 flex-col" aria-live="polite">
          {children}
        </main>
        <aside className="hidden w-72 shrink-0 overflow-hidden border-l border-border bg-bg p-3 lg:block">
          {inspector}
        </aside>

        {/* Narrow screens: inspector becomes a drawer (Escape closes it, never cancels runs). */}
        <Dialog.Root open={drawerOpen} onOpenChange={setDrawerOpen} modal={false}>
          <Dialog.Portal>
            <Dialog.Overlay
              className="fixed inset-0 z-40 bg-overlay lg:hidden"
              data-state={drawerOpen ? "open" : "closed"}
            />
            <Dialog.Content
              className="fixed inset-y-0 right-0 z-50 w-[min(22rem,90vw)] overflow-y-auto border-l border-border bg-surface p-4 shadow-md outline-none lg:hidden"
              aria-label={t("inspector.title")}
            >
              <div className="mb-3 flex items-center justify-between">
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
      </div>

      {/* Floating controls: sidebar toggle (narrow) + inspector (narrow). */}
      <div className="pointer-events-none fixed bottom-4 right-4 z-30 flex gap-2 lg:hidden" aria-hidden>
        <button
          type="button"
          className="btn-secondary pointer-events-auto shadow-md"
          onClick={() => setSidebarCollapsed((value) => !value)}
          aria-label={sidebarCollapsed ? t("shell.expandSidebar") : t("shell.collapseSidebar")}
        >
          <PanelLeft aria-hidden size={16} />
        </button>
        <button
          type="button"
          className="btn-secondary pointer-events-auto shadow-md"
          onClick={() => setDrawerOpen(true)}
          aria-label={t("shell.openInspector")}
        >
          <PanelRightOpen aria-hidden size={16} />
        </button>
      </div>
    </div>
  );
}
