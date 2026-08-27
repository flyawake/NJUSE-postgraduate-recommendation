import { createContext, useCallback, useContext, useMemo, useState } from "react";
import type { ReactNode } from "react";
import zhCN from "@/i18n/zh-CN";
import enUS from "@/i18n/en-US";

export type Locale = "zh-CN" | "en-US";

const DICTIONARIES: Record<Locale, Record<string, string>> = {
  "zh-CN": zhCN,
  "en-US": enUS,
};

export const LOCALES: Locale[] = ["zh-CN", "en-US"];

export interface I18n {
  locale: Locale;
  setLocale: (locale: Locale) => void;
  t: (key: string, params?: Record<string, string | number>) => string;
}

const I18nContext = createContext<I18n | null>(null);

const STORAGE_KEY = "coding-agent-ui-locale";

function readStoredLocale(): Locale {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored === "zh-CN" || stored === "en-US") return stored;
  } catch {
    /* storage unavailable — keep default */
  }
  return "zh-CN";
}

export function I18nProvider({ children }: { children: ReactNode }) {
  const [locale, setLocaleState] = useState<Locale>(readStoredLocale);

  const setLocale = useCallback((next: Locale) => {
    setLocaleState(next);
    try {
      localStorage.setItem(STORAGE_KEY, next);
    } catch {
      /* best effort */
    }
  }, []);

  const value = useMemo<I18n>(() => {
    const dict = DICTIONARIES[locale];
    const t = (key: string, params?: Record<string, string | number>): string => {
      const template = dict[key] ?? `{{${key}}}`;
      if (!params) return template;
      return template.replace(/\{(\w+)\}/g, (match, name: string) =>
        name in params ? String(params[name]) : match
      );
    };
    return { locale, setLocale, t };
  }, [locale, setLocale]);

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n(): I18n {
  const ctx = useContext(I18nContext);
  if (!ctx) throw new Error("useI18n must be used inside I18nProvider");
  return ctx;
}
