import { useState } from "react";
import { api } from "../api/client";
import type { Deal, Site } from "../api/types";
import { useI18n } from "../i18n";
import { useApp } from "../lib/app";
import { formatDateTime } from "../lib/format";
import { Badge, ConfirmDialog, Field, Modal, useErrorText } from "./common";

const MODES = ["auto", "on", "off"] as const;

interface ProbeResult {
  detected: boolean;
  reachable: boolean;
  site: Site;
}

/**
 * Per-site browser-assisted import setting. "auto" offers it once HouseAgent has seen the site answer with human
 * verification (an automated run, or the one-off check below); "on" / "off" are the user's choice.
 */
export function SiteAssistSetting({ site }: { site: Site }) {
  const { t, lang } = useI18n();
  const { settings, toast, reloadSites } = useApp();
  const errText = useErrorText();
  const [busy, setBusy] = useState(false);
  const probe = site.verification_probe;

  const setMode = async (mode: string) => {
    try { await api.put(`/sites/${site.id}/assisted`, { mode }); await reloadSites(); } catch (e) { toast("error", errText(e)); }
  };
  const detect = async () => {
    setBusy(true);
    try {
      const r = await api.post<ProbeResult>(`/sites/${site.id}/detect-verification`);
      toast(r.detected ? "info" : "success",
        t(r.detected ? "site_assist.detect_found" : r.reachable ? "site_assist.detect_clean" : "site_assist.detect_unreachable"));
      await reloadSites();
    } catch (e) { toast("error", errText(e)); } finally { setBusy(false); }
  };

  let status: string;
  if (site.assisted_mode === "off") status = t("site_assist.status_off");
  else if (site.capabilities.dedicated_assisted) status = t("site_assist.status_dedicated");
  else if (site.assisted_mode === "on") status = t("site_assist.status_on");
  else if (site.verification_detected_at) {
    status = t("site_assist.status_detected", {
      at: formatDateTime(site.verification_detected_at, lang, settings.timezone),
      source: t(`site_assist.source.${probe?.source === "run" ? "run" : "probe"}`),
    });
  } else if (probe && !probe.detected) {
    status = t("site_assist.status_clean", { at: formatDateTime(probe.checked_at, lang, settings.timezone) });
  } else status = t("site_assist.status_unchecked");

  return (
    <div className="assist-setting">
      <div className="assist-row">
        <span className="small"><b>{t("site_assist.title")}</b></span>
        <select className="select-sm" value={site.assisted_mode} onChange={(e) => void setMode(e.target.value)} aria-label={t("site_assist.title")}>
          {MODES.map((m) => <option key={m} value={m}>{t(`site_assist.mode.${m}`)}</option>)}
        </select>
        <Badge tone={site.capabilities.assisted ? "info" : "muted"}>{t(site.capabilities.assisted ? "site_assist.available" : "site_assist.unavailable")}</Badge>
        {!site.is_custom && (
          <button className="btn btn-sm" disabled={busy} onClick={() => void detect()}>
            {busy ? <><span className="spinner" /> {t("site_assist.detecting")}</> : t("site_assist.detect")}
          </button>
        )}
      </div>
      <p className="muted small">{status}</p>
    </div>
  );
}

/** Add or edit a user-defined site: a name and the search entry page(s). No per-site code is needed. */
export function CustomSiteEditor({ site, onClose }: { site?: Site; onClose: () => void }) {
  const { t } = useI18n();
  const { toast, reloadSites } = useApp();
  const errText = useErrorText();
  const [name, setName] = useState(site?.name ?? "");
  const [links, setLinks] = useState<Partial<Record<Deal, string>>>(site?.capabilities.links ?? {});
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const save = async () => {
    if (!name.trim()) { setErr(t("custom_site.name_required")); return; }
    if (!links.buy?.trim() && !links.rent?.trim()) { setErr(t("error.custom_site_link_required")); return; }
    setBusy(true);
    try {
      const body = { name, links: { buy: links.buy || null, rent: links.rent || null } };
      if (site) await api.patch(`/sites/${site.id}`, body); else await api.post("/sites", body);
      toast("success", t(site ? "custom_site.saved" : "custom_site.added"));
      await reloadSites();
      onClose();
    } catch (e) { setErr(errText(e)); } finally { setBusy(false); }
  };

  return (
    <Modal title={t(site ? "custom_site.edit_title" : "custom_site.add_title")} onClose={onClose}
      footer={<><button className="btn" onClick={onClose}>{t("common.cancel")}</button>
        <button className="btn btn-primary" disabled={busy} onClick={() => void save()}>{t("common.save")}</button></>}>
      <p className="hint-box">{t("custom_site.help")}</p>
      <Field label={t("custom_site.name")} required><input value={name} onChange={(e) => setName(e.target.value)} /></Field>
      {(["buy", "rent"] as const).map((d) => (
        <Field key={d} label={t("custom_site.link", { deal: t(`deal.${d}`) })} hint={t("custom_site.link_hint")}>
          <input type="url" value={links[d] ?? ""} placeholder="https://" onChange={(e) => setLinks({ ...links, [d]: e.target.value })} />
        </Field>
      ))}
      <p className="muted small">{t("custom_site.principle")}</p>
      {err && <p className="form-error">{err}</p>}
    </Modal>
  );
}

export function CustomSiteActions({ site }: { site: Site }) {
  const { t } = useI18n();
  const { toast, reloadSites } = useApp();
  const [editing, setEditing] = useState(false);
  const [removing, setRemoving] = useState(false);
  return (
    <>
      <button className="btn btn-sm btn-ghost" onClick={() => setEditing(true)}>{t("common.edit")}</button>
      <button className="btn btn-sm btn-ghost-danger" onClick={() => setRemoving(true)}>{t("custom_site.remove")}</button>
      {editing && <CustomSiteEditor site={site} onClose={() => setEditing(false)} />}
      {removing && (
        <ConfirmDialog danger title={t("custom_site.remove")} confirmLabel={t("custom_site.remove")} message={t("custom_site.remove_body", { site: site.name })}
          onClose={() => setRemoving(false)}
          onConfirm={async () => { await api.del(`/sites/${site.id}`); toast("success", t("custom_site.removed")); await reloadSites(); }} />
      )}
    </>
  );
}
