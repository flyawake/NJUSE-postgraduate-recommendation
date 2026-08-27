import { Activity, Info, Play, Settings2 } from "lucide-react";
import { useI18n } from "@/lib/i18n";
import { cx } from "@/lib/format";
import type { ViewName } from "./AppShell";

export interface SidebarProps {
  view: ViewName;
  collapsed: boolean;
  onNavigate: (view: ViewName) => void;
  badge?: string | null;
}

const NAV_ITEMS: Array<{ view: ViewName; labelKey: string; icon: typeof Play }> = [
  { view: "new", labelKey: "nav.newTask", icon: Play },
  { view: "current", labelKey: "nav.currentRun", icon: Activity },
  { view: "settings", labelKey: "nav.settings", icon: Settings2 },
  { view: "about", labelKey: "nav.about", icon: Info },
];

export function Sidebar({ view, collapsed, onNavigate, badge }: SidebarProps) {
  const { t } = useI18n();
  return (
    <nav
      className={cx(
        "flex shrink-0 flex-col gap-1 border-r border-border bg-surface p-2 transition-[width] duration-fast",
        collapsed ? "w-12" : "w-48"
      )}
      aria-label={t("shell.collapseSidebar")}
      data-testid="sidebar"
    >
      {NAV_ITEMS.map(({ view: itemView, labelKey, icon: Icon }) => {
        const active = view === itemView;
        return (
          <button
            key={itemView}
            type="button"
            onClick={() => onNavigate(itemView)}
            className={cx(
              "flex h-9 items-center gap-2 rounded-md px-2 text-sm transition-colors duration-fast",
              active ? "bg-accent-muted font-medium text-accent" : "text-muted hover:bg-surface-2 hover:text-text",
              collapsed && "justify-center px-0"
            )}
            aria-current={active ? "page" : undefined}
            title={collapsed ? t(labelKey) : undefined}
            data-testid={`nav-${itemView}`}
          >
            <Icon aria-hidden size={16} className="shrink-0" />
            {!collapsed ? <span>{t(labelKey)}</span> : null}
            {!collapsed && itemView === "current" && badge ? (
              <span className="ml-auto h-2 w-2 rounded-full bg-accent" aria-label={badge} />
            ) : null}
          </button>
        );
      })}
      {!collapsed ? <p className="mt-2 px-2 text-xs text-faint">{t("nav.settingsHint")}</p> : null}
    </nav>
  );
}
