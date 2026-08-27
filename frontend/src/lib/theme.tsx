import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";

export type ThemePreference = "system" | "light" | "dark";
export type EffectiveTheme = "light" | "dark";

const STORAGE_KEY = "coding-agent-ui-theme";

function readStoredTheme(): ThemePreference {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored === "system" || stored === "light" || stored === "dark") return stored;
  } catch {
    /* fall through */
  }
  return "system";
}

function systemTheme(): EffectiveTheme {
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export interface ThemeController {
  preference: ThemePreference;
  effective: EffectiveTheme;
  setPreference: (preference: ThemePreference) => void;
}

const ThemeContext = createContext<ThemeController | null>(null);

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [preference, setPreferenceState] = useState<ThemePreference>(readStoredTheme);
  const [systemIsDark, setSystemIsDark] = useState<boolean>(() => systemTheme() === "dark");

  useEffect(() => {
    const media = window.matchMedia?.("(prefers-color-scheme: dark)");
    if (!media) return;
    const onChange = (event: MediaQueryListEvent) => setSystemIsDark(event.matches);
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, []);

  const effective: EffectiveTheme = preference === "system" ? (systemIsDark ? "dark" : "light") : preference;

  useEffect(() => {
    document.documentElement.dataset.theme = effective;
    document.documentElement.style.colorScheme = effective;
  }, [effective]);

  const setPreference = useCallback((next: ThemePreference) => {
    setPreferenceState(next);
    try {
      localStorage.setItem(STORAGE_KEY, next);
    } catch {
      /* best effort */
    }
  }, []);

  const value = useMemo(
    () => ({ preference, effective, setPreference }),
    [preference, effective, setPreference]
  );
  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme(): ThemeController {
  const ctx = useContext(ThemeContext);
  if (!ctx) throw new Error("useTheme must be used inside ThemeProvider");
  return ctx;
}
