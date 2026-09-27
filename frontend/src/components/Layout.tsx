import { NavLink, Outlet } from "react-router-dom";
import { api } from "../api/client";
import type { Lang, Notice } from "../api/types";
import { LANGS, useI18n } from "../i18n";
import { useApp } from "../lib/app";
import { useLoad } from "../lib/hooks";
import { Toasts } from "./common";
import {
  IconChart, IconDashboard, IconGlobe, IconHome, IconKey, IconList, IconSearch, IconSettings, IconStar,
} from "./Icons";

// Menu order and icons are identical in every language so users never lose their place.
const MENU = [
  { to: "/", key: "menu.dashboard", icon: IconDashboard, end: true },
  { to: "/tasks", key: "menu.tasks", icon: IconSearch },
  { to: "/properties", key: "menu.properties", icon: IconHome },
  { to: "/statistics", key: "menu.statistics", icon: IconChart },
  { to: "/favorites", key: "menu.favorites", icon: IconStar },
  { to: "/sites", key: "menu.sites", icon: IconKey },
  { to: "/logs", key: "menu.logs", icon: IconList },
  { to: "/settings", key: "menu.settings", icon: IconSettings },
];

const LANG_LABEL: Record<Lang, string> = { ja: "日本語", zh: "简体中文", en: "English" };

export default function Layout() {
  const { t, lang, setLang } = useI18n();
  const { system } = useApp();
  const notices = useLoad(() => api.get<Notice[]>("/notifications"), [], 30000);
  const count = notices.data?.length ?? 0;
  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <svg width="26" height="26" viewBox="0 0 64 64" aria-hidden="true"><path d="M32 6 60 30h-8v28H12V30H4z" fill="currentColor" /><rect x="26" y="38" width="12" height="20" fill="var(--sidebar-bg)" /></svg>
          <span>HouseAgent</span>
        </div>
        <nav>
          {MENU.map(({ to, key, icon: Icon, end }) => (
            <NavLink key={to} to={to} end={end} className={({ isActive }) => `nav-item ${isActive ? "active" : ""}`}>
              <Icon />
              <span>{t(key)}</span>
              {to === "/" && count > 0 && <span className="nav-count" title={t("dash.needs_attention")}>{count}</span>}
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-foot">
          <label className="lang-switch">
            <IconGlobe />
            <select value={lang} onChange={(e) => setLang(e.target.value as Lang)} aria-label={t("settings.language")}>
              {LANGS.map((l) => <option key={l} value={l}>{LANG_LABEL[l]}</option>)}
            </select>
          </label>
          <span className="version">v{system?.version ?? "1.0.0"} · {t("common.local_only")}</span>
        </div>
      </aside>
      <main className="workspace">
        {system?.read_only && (
          <div className="banner banner-bad" role="alert">{t("banner.read_only")} ({system.read_only})</div>
        )}
        <Outlet />
      </main>
      <Toasts />
    </div>
  );
}
