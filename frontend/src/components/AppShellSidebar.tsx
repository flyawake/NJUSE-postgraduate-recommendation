import type { ReactNode } from "react";
import { BookOpen, Info, Settings2 } from "lucide-react";
import { useI18n } from "@/lib/i18n";
import { cx } from "@/lib/format";
import type { ViewName } from "./AppShell";

export interface SidebarProps {
  view: ViewName;
  collapsed: boolean;
  onNavigate: (view: ViewName) => void;
  children?: ReactNode;
  drawer?: boolean;
}

const NAV_ITEMS: Array<{ view: ViewName; labelKey: string; icon: typeof Settings2 }> = [
  { view: "memories", labelKey: "nav.memories", icon: BookOpen },
  { view: "settings", labelKey: "nav.settings", icon: Settings2 },
  { view: "about", labelKey: "nav.about", icon: Info },
];

export function Sidebar({ view, collapsed, onNavigate, children, drawer = false }: SidebarProps) {
  const { t } = useI18n();
  const navigation = NAV_ITEMS.map(({ view: itemView, labelKey, icon: Icon }) => {
    const active = view === itemView;
    return (
      <button
        key={itemView}
        type="button"
        onClick={() => onNavigate(itemView)}
        className={cx(
          "flex h-8 min-w-0 items-center justify-center gap-1.5 rounded-[9px] px-2 text-[11px] transition-colors duration-fast",
          active ? "bg-surface/72 font-medium text-text shadow-sm" : "text-muted hover:bg-surface/55 hover:text-text",
          collapsed && "w-10 px-0"
        )}
        aria-current={active ? "page" : undefined}
        title={collapsed ? t(labelKey) : undefined}
        data-testid={`nav-${itemView}`}
      >
        <Icon aria-hidden size={14} className="shrink-0" />
        {!collapsed ? <span className="truncate">{t(labelKey)}</span> : null}
      </button>
    );
  });
  return (
    <nav
      className={cx(
        "glass-chrome flex h-full shrink-0 flex-col gap-0.5 border-r border-border/60 p-2 transition-[width] duration-fast",
        collapsed ? "w-14" : drawer ? "w-full" : "w-[17rem]"
      )}
      aria-label={t("shell.collapseSidebar")}
      data-testid="sidebar"
    >
      {!collapsed && children ? (
        <div className="min-h-0 flex-1 overflow-hidden">{children}</div>
      ) : null}
      <div className={cx(
        "mt-auto border-t border-border/45 pt-2",
        collapsed ? "flex flex-col items-center gap-1" : "grid grid-cols-3 gap-1"
      )}>
        {navigation}
      </div>
    </nav>
  );
}
