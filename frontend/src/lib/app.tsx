import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api, ApiError } from "../api/client";
import type { Lang, Regions, Settings, Site, SystemInfo } from "../api/types";
import { I18nProvider, LANGS } from "../i18n";

export interface Toast {
  id: number;
  kind: "success" | "error" | "info";
  text: string;
}

interface AppValue {
  settings: Settings;
  regions: Regions;
  sites: Site[];
  system: SystemInfo | null;
  reloadSites: () => Promise<void>;
  reloadSystem: () => Promise<void>;
  saveSettings: (changes: Partial<Settings>) => Promise<Settings>;
  toast: (kind: Toast["kind"], text: string) => void;
  toasts: Toast[];
  dismissToast: (id: number) => void;
  siteName: (id: string | null | undefined) => string;
}

const AppContext = createContext<AppValue | null>(null);

const LANG_CACHE = "houseagent.lang";

function cachedLang(): Lang {
  try {
    const v = window.localStorage.getItem(LANG_CACHE);
    if (v && (LANGS as string[]).includes(v)) return v as Lang;
  } catch {
    /* storage unavailable - use default */
  }
  return "ja";
}

function cacheLang(lang: Lang): void {
  try {
    window.localStorage.setItem(LANG_CACHE, lang);
  } catch {
    /* ignore */
  }
}

type Boot = { settings: Settings; regions: Regions; sites: Site[] };

export function AppProvider({ children, fallback }: { children: ReactNode; fallback: (e: ApiError | null, retry: () => void, lang: Lang) => ReactNode }) {
  const [boot, setBoot] = useState<Boot | null>(null);
  const [bootError, setBootError] = useState<ApiError | null>(null);
  const [system, setSystem] = useState<SystemInfo | null>(null);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [lang, setLangState] = useState<Lang>(cachedLang());
  const nextId = useRef(1);

  const load = useCallback(async () => {
    setBootError(null);
    try {
      const [settings, regions, sites] = await Promise.all([
        api.get<Settings>("/settings"),
        api.get<Regions>("/regions"),
        api.get<Site[]>("/sites"),
      ]);
      setBoot({ settings, regions, sites });
      setLangState(settings.language);
      cacheLang(settings.language);
      api.get<SystemInfo>("/system").then(setSystem).catch(() => undefined);
    } catch (e) {
      setBootError(e instanceof ApiError ? e : null);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const toast = useCallback((kind: Toast["kind"], text: string) => {
    const id = nextId.current++;
    setToasts((ts) => [...ts, { id, kind, text }]);
    window.setTimeout(() => setToasts((ts) => ts.filter((x) => x.id !== id)), kind === "error" ? 9000 : 4000);
  }, []);

  const saveSettings = useCallback(async (changes: Partial<Settings>) => {
    const s = await api.patch<Settings>("/settings", changes);
    setBoot((b) => (b ? { ...b, settings: s } : b));
    if (s.language) {
      setLangState(s.language);
      cacheLang(s.language);
    }
    return s;
  }, []);

  // Language changes apply instantly and never touch business data or running tasks.
  const setLang = useCallback(
    (l: Lang) => {
      setLangState(l);
      cacheLang(l);
      void saveSettings({ language: l }).catch(() => undefined);
    },
    [saveSettings],
  );

  const reloadSites = useCallback(async () => {
    const sites = await api.get<Site[]>("/sites");
    setBoot((b) => (b ? { ...b, sites } : b));
  }, []);

  const reloadSystem = useCallback(async () => {
    setSystem(await api.get<SystemInfo>("/system"));
  }, []);

  const value = useMemo<AppValue | null>(() => {
    if (!boot) return null;
    return {
      settings: boot.settings,
      regions: boot.regions,
      sites: boot.sites,
      system,
      reloadSites,
      reloadSystem,
      saveSettings,
      toast,
      toasts,
      dismissToast: (id: number) => setToasts((ts) => ts.filter((x) => x.id !== id)),
      siteName: (id) => boot.sites.find((s) => s.id === id)?.name ?? id ?? "—",
    };
  }, [boot, system, reloadSites, reloadSystem, saveSettings, toast, toasts]);

  return (
    <I18nProvider lang={lang} setLang={setLang}>
      {value ? <AppContext.Provider value={value}>{children}</AppContext.Provider> : fallback(bootError, () => void load(), lang)}
    </I18nProvider>
  );
}

export function useApp(): AppValue {
  const ctx = useContext(AppContext);
  if (!ctx) throw new Error("AppProvider missing");
  return ctx;
}
