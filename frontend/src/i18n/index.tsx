// UI i18n. Business logic only uses stable keys (menu.dashboard, error.AUTH_REQUIRED ...); text is resolved
// here. Missing translations fall back to Japanese and are reported to the backend log for developers.
import { createContext, useCallback, useContext, useMemo, type ReactNode } from "react";
import type { Lang } from "../api/types";
import en from "./locales/en";
import ja from "./locales/ja";
import zh from "./locales/zh";

export type Params = Record<string, string | number | null | undefined>;
export const LANGS: Lang[] = ["ja", "zh", "en"];
export const LOCALE: Record<Lang, string> = { ja: "ja-JP", zh: "zh-CN", en: "en-US" };
const DICTS: Record<Lang, Record<string, string>> = { ja, zh, en };

const missing = new Map<Lang, Set<string>>();
let flushTimer: number | undefined;

function reportMissing(lang: Lang, key: string): void {
  const set = missing.get(lang) ?? new Set<string>();
  if (set.has(key)) return;
  set.add(key);
  missing.set(lang, set);
  if (import.meta.env.DEV) console.warn(`[i18n] missing ${lang}: ${key}`);
  window.clearTimeout(flushTimer);
  flushTimer = window.setTimeout(() => {
    for (const [l, keys] of missing) {
      if (keys.size === 0) continue;
      const token = document.querySelector<HTMLMetaElement>('meta[name="houseagent-token"]')?.content ?? "";
      void fetch("/api/v1/i18n/missing", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-HouseAgent-Token": token },
        body: JSON.stringify({ language: l, keys: [...keys] }),
      }).catch(() => undefined);
      keys.clear();
    }
  }, 2000);
}

export function translate(lang: Lang, key: string, params?: Params): string {
  let text = DICTS[lang][key];
  if (text === undefined) {
    if (typeof window !== "undefined") reportMissing(lang, key);
    text = DICTS.ja[key];
    if (text === undefined) {
      if (typeof window !== "undefined" && lang !== "ja") reportMissing("ja", key);
      text = key;
    }
  }
  if (params) {
    text = text.replace(/\{(\w+)\}/g, (_m, name: string) => {
      const v = params[name];
      return v === undefined || v === null ? "" : String(v);
    });
  }
  return text;
}

/** Whether a key exists (used for optional per-value labels such as a site's note). */
export function hasKey(key: string): boolean {
  return key in DICTS.ja;
}

interface I18nValue {
  lang: Lang;
  locale: string;
  setLang: (lang: Lang) => void;
  t: (key: string, params?: Params) => string;
  /** Translate an enum value, falling back to the raw value. */
  tv: (prefix: string, value: string | null | undefined) => string;
}

const I18nContext = createContext<I18nValue | null>(null);

export function I18nProvider({ lang, setLang, children }: { lang: Lang; setLang: (l: Lang) => void; children: ReactNode }) {
  const t = useCallback((key: string, params?: Params) => translate(lang, key, params), [lang]);
  const tv = useCallback(
    (prefix: string, value: string | null | undefined) => {
      if (value === null || value === undefined || value === "") return "—";
      const key = `${prefix}.${value}`;
      return hasKey(key) ? translate(lang, key) : value;
    },
    [lang],
  );
  const value = useMemo(() => ({ lang, locale: LOCALE[lang], setLang, t, tv }), [lang, setLang, t, tv]);
  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n(): I18nValue {
  const ctx = useContext(I18nContext);
  if (!ctx) throw new Error("I18nProvider missing");
  return ctx;
}
