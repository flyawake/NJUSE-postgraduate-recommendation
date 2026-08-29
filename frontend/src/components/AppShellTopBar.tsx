import * as Select from "@radix-ui/react-select";
import { Moon, PanelLeft, Settings, SquareTerminal, Sun, SunMoon } from "lucide-react";
import { useI18n } from "@/lib/i18n";
import { LOCALES } from "@/lib/i18n";
import { useTheme } from "@/lib/theme";
import type { ThemePreference } from "@/lib/theme";

export interface TopBarProps {
  workspace: string;
  profileLabel: string | null;
  onOpenSettings: () => void;
  sidebarCollapsed: boolean;
  onToggleSidebar: () => void;
}

const THEME_ICON: Record<ThemePreference, typeof Sun> = {
  system: SunMoon,
  light: Sun,
  dark: Moon,
};

/** Top bar: product mark, workspace/profile summary, settings, theme + locale. */
export function TopBar({
  workspace,
  profileLabel,
  onOpenSettings,
  sidebarCollapsed,
  onToggleSidebar,
}: TopBarProps) {
  const { t, locale, setLocale } = useI18n();
  const { preference, setPreference } = useTheme();
  const ThemeIcon = THEME_ICON[preference];

  return (
    <header className="glass-chrome flex h-12 shrink-0 items-center gap-2.5 border-b px-2.5 sm:px-3">
      <button
        type="button"
        className="btn-icon"
        onClick={onToggleSidebar}
        aria-label={sidebarCollapsed ? t("shell.expandSidebar") : t("shell.collapseSidebar")}
      >
        <PanelLeft aria-hidden size={16} />
      </button>
      <div className="flex items-center gap-2">
        <span className="flex h-7 w-7 items-center justify-center rounded-[9px] bg-accent text-accent-fg shadow-sm">
          <SquareTerminal aria-hidden size={15} />
        </span>
        <span className="text-[14px] font-semibold tracking-[-0.025em]">{t("app.name")}</span>
      </div>

      <div className="glass-panel mx-auto hidden min-w-0 items-center gap-2 rounded-[11px] border px-3 py-1 text-[11px] shadow-sm sm:flex">
        <span className="mono max-w-[24rem] truncate text-muted" title={workspace}>
          {workspace || t("topbar.noWorkspace")}
        </span>
        <span aria-hidden className="text-faint">
          ·
        </span>
        <span className="mono max-w-[16rem] truncate text-muted">
          {profileLabel ?? t("topbar.noProfile")}
        </span>
      </div>

      <div className="ml-auto flex items-center gap-1">
        <button
          type="button"
          className="btn-icon"
          onClick={onOpenSettings}
          aria-label={t("topbar.settings")}
          data-testid="open-settings"
        >
          <Settings aria-hidden size={16} />
        </button>
        <Select.Root value={preference} onValueChange={(next) => setPreference(next as ThemePreference)}>
          <Select.Trigger asChild>
            <button type="button" className="btn-icon" aria-label={t("topbar.theme")} data-testid="theme-toggle">
              <ThemeIcon aria-hidden size={16} />
            </button>
          </Select.Trigger>
          <Select.Portal>
            <Select.Content
              position="popper"
              sideOffset={4}
              className="z-50 min-w-32 rounded-md border border-border bg-surface p-1 shadow-md"
            >
              <Select.Viewport>
                {(["system", "light", "dark"] as const).map((value) => (
                  <Select.Item
                    key={value}
                    value={value}
                    className="cursor-pointer select-none rounded-sm px-2 py-1.5 text-sm outline-none data-[highlighted]:bg-surface-2"
                  >
                    <Select.ItemText>{t(`topbar.theme.${value}`)}</Select.ItemText>
                  </Select.Item>
                ))}
              </Select.Viewport>
            </Select.Content>
          </Select.Portal>
        </Select.Root>
        <Select.Root value={locale} onValueChange={(next) => setLocale(next as (typeof LOCALES)[number])}>
          <Select.Trigger asChild>
            <button type="button" className="btn-icon mono text-xs" aria-label={t("topbar.language")} data-testid="locale-toggle">
              {t(`topbar.localeShort.${locale}`)}
            </button>
          </Select.Trigger>
          <Select.Portal>
            <Select.Content
              position="popper"
              sideOffset={4}
              className="z-50 min-w-32 rounded-md border border-border bg-surface p-1 shadow-md"
            >
              <Select.Viewport>
                {LOCALES.map((value) => (
                  <Select.Item
                    key={value}
                    value={value}
                    className="cursor-pointer select-none rounded-sm px-2 py-1.5 text-sm outline-none data-[highlighted]:bg-surface-2"
                  >
                    <Select.ItemText>{t(`topbar.localeName.${value}`)}</Select.ItemText>
                  </Select.Item>
                ))}
              </Select.Viewport>
            </Select.Content>
          </Select.Portal>
        </Select.Root>
      </div>
    </header>
  );
}
