import { useState } from "react";
import { api } from "../api/client";
import type { Account, Site } from "../api/types";
import {
  Badge, ConfirmDialog, Field, LoginBadge, Modal, PageHeader, PermissionBadge, StateView, useErrorText,
} from "../components/common";
import { hasKey, useI18n } from "../i18n";
import { useApp } from "../lib/app";
import { formatDate, formatDateTime } from "../lib/format";
import { RulesCheckPanel } from "../components/RulesCheck";
import { CustomSiteActions, CustomSiteEditor, SiteAssistSetting } from "../components/SiteAssist";
import { useLoad } from "../lib/hooks";

/** Automated real sites first, then register-only sites, test mocks last. */
function siteOrder(s: Site): number {
  return s.is_mock ? 2 : s.capabilities.automation_available ? 0 : 1;
}

const PERMS = ["browser_automation", "data_retention", "commercial_use", "image_storage", "api_access"];

export default function Sites() {
  const { t, lang } = useI18n();
  const { sites, reloadSites, settings } = useApp();
  const accounts = useLoad(() => api.get<Account[]>("/accounts"), [], 5000);
  const [adding, setAdding] = useState(false);
  const [addingSite, setAddingSite] = useState(false);
  const [permEdit, setPermEdit] = useState<{ site: Site; ptype: string } | null>(null);

  return (
    <div className="page">
      <PageHeader title={t("menu.sites")} description={t("sites.desc")}
        actions={<button className="btn btn-primary" onClick={() => setAdding(true)}>+ {t("sites.add_account")}</button>} />
      <p className="hint-box">{t("sites.login_principle")}</p>

      <h2 className="section-title">{t("sites.accounts")}</h2>
      <StateView loading={accounts.loading} error={accounts.error} onRetry={accounts.reload} empty={!!accounts.data && accounts.data.length === 0}
        emptyText={<><p>{t("sites.no_accounts")}</p><button className="btn btn-primary" onClick={() => setAdding(true)}>{t("sites.add_account")}</button></>}>
        <div className="account-grid">
          {(accounts.data ?? []).map((a) => <AccountCard key={a.id} a={a} all={accounts.data ?? []} onChange={accounts.reload} />)}
        </div>
      </StateView>

      <div className="section-title-row">
        <h2 className="section-title">{t("sites.sites_permissions")}</h2>
        <button className="btn btn-sm" onClick={() => setAddingSite(true)}>+ {t("custom_site.add_title")}</button>
      </div>
      <div className="site-grid">
        {[...sites].sort((a, b) => siteOrder(a) - siteOrder(b) || a.name.localeCompare(b.name)).map((s) => (
          <section key={s.id} className="card">
            <div className="card-title-row">
              <h3 className="card-title">{s.name} {s.is_mock && <Badge tone="info">{t("sites.mock")}</Badge>}
                {s.is_custom && <Badge tone="muted">{t("custom_site.badge")}</Badge>}
                {s.capabilities.deals.map((d) => <span key={d} className={`deal-tag deal-${d}`}>{t(`deal.${d}`)}</span>)}</h3>
              {s.is_custom && <span className="row-actions"><CustomSiteActions site={s} /></span>}
              {s.adapter_status === "stopped" && (
                <button className="btn btn-sm" onClick={async () => { await api.post(`/sites/${s.id}/adapter/reenable`); await reloadSites(); }}>{t("sites.reenable_adapter")}</button>
              )}
            </div>
            <p className="small">
              {s.capabilities.assisted ? <Badge tone="info">{t("wizard.site_assisted")}</Badge> : s.capabilities.automation_available
                ? <Badge tone="ok">{t("sites.automation_yes", { types: s.capabilities.property_types.map((ty) => t(`ptype.${ty}`)).join("・") })}</Badge>
                : <Badge tone="muted">{t("wizard.site_manual_only")}</Badge>}
              {" "}{(Object.entries(s.capabilities.links) as [string, string][]).map(([d, url]) => (
                <a key={d} className="site-link" href={url} target="_blank" rel="noopener noreferrer">{t("sites.open_search_deal", { deal: t(`deal.${d}`) })}</a>
              ))}
            </p>
            {!s.is_mock && <SiteAssistSetting site={s} />}
            {s.adapter_status === "stopped" && <p className="form-error">{t("site.adapter_stopped")}: {s.adapter_diagnostic?.run_no ?? ""}</p>}
            {s.capabilities.notes_key && hasKey(s.capabilities.notes_key) && <p className="muted small">{t(s.capabilities.notes_key)}</p>}
            {/* Permissions only matter once a site can be searched automatically; fold them away otherwise. */}
            <details open={s.capabilities.automation_available && !s.is_mock}>
              <summary>{t("sites.permissions_section")}</summary>
              <table className="table">
                <tbody>
                  {PERMS.map((p) => {
                    const info = s.permissions[p];
                    return (
                      <tr key={p}>
                        <td>{t(`perm.${p}`)}</td>
                        <td><PermissionBadge status={info.status} /></td>
                        <td className="muted small">{info.source ?? t("sites.no_source")}{info.reviewed_at ? ` · ${t("sites.reviewed")} ${formatDate(info.reviewed_at, lang, settings.timezone)}` : ""}</td>
                        <td>{!s.is_mock && <button className="btn btn-sm btn-ghost" onClick={() => setPermEdit({ site: s, ptype: p })}>{t("common.edit")}</button>}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </details>
            {s.rules_check_available && (
              <details open={s.permissions.browser_automation?.status !== "allowed"}>
                <summary>{t("rules.section")}</summary>
                <p className="muted small">{t("rules.section_help")}</p>
                <RulesCheckPanel siteId={s.id} />
              </details>
            )}
            <details>
              <summary>{t("sites.capabilities")}</summary>
              <div className="support-table">
                {Object.entries(s.capabilities.fields).filter(([f]) => f !== "result_limit").map(([f, sup]) => (
                  <span key={f} className="support-item">{t(`field.${f}`)} <Badge tone={sup === "supported" ? "ok" : sup === "local_filter" ? "warn" : "bad"}>{t(`support.${sup}`)}</Badge></span>
                ))}
              </div>
            </details>
          </section>
        ))}
      </div>
      <p className="muted small">{t("sites.permission_note")}</p>
      {addingSite && <CustomSiteEditor onClose={() => setAddingSite(false)} />}
      {adding && <AddAccount onClose={() => setAdding(false)} onDone={() => { accounts.reload(); void reloadSites(); }} />}
      {permEdit && <PermissionEditor site={permEdit.site} ptype={permEdit.ptype} onClose={() => setPermEdit(null)} onDone={() => void reloadSites()} />}
    </div>
  );
}

function AccountCard({ a, all, onChange }: { a: Account; all: Account[]; onChange: () => void }) {
  const { t, lang } = useI18n();
  const { settings, toast } = useApp();
  const errText = useErrorText();
  const [confirm, setConfirm] = useState<"clear" | "delete" | null>(null);
  const [affected, setAffected] = useState<{ id: number; name: string }[]>([]);
  const [action, setAction] = useState("pause");
  const [rebindTo, setRebindTo] = useState<number | "">("");
  const [busy, setBusy] = useState(false);

  const call = async (path: string, okKey?: string, body?: unknown) => {
    setBusy(true);
    try {
      await api.post(`/accounts/${a.id}/${path}`, body);
      if (okKey) toast("success", t(okKey));
      onChange();
    } catch (e) { toast("error", errText(e)); } finally { setBusy(false); }
  };
  const others = all.filter((x) => x.site_id === a.site_id && x.id !== a.id);
  const autoAllowed = a.permissions.browser_automation?.status === "allowed";

  return (
    <section className="card account-card">
      <div className="card-title-row">
        <h3 className="card-title">{a.account_alias}</h3>
        <LoginBadge status={a.login_status} />
      </div>
      <dl className="kv small">
        <dt>{t("sites.site")}</dt><dd>{a.site_name}</dd>
        <dt>{t("sites.permission")}</dt><dd><PermissionBadge status={a.permissions.browser_automation?.status ?? "unknown"} /></dd>
        <dt>{t("sites.last_check")}</dt><dd>{formatDateTime(a.last_checked_at, lang, settings.timezone)}</dd>
        <dt>{t("sites.task_count")}</dt><dd>{a.task_count}</dd>
        <dt>{t("sites.profile")}</dt><dd className="mono small ellipsis" title={a.profile_path}>{a.profile_path}</dd>
        {a.note && <><dt>{t("sites.note")}</dt><dd>{a.note}</dd></>}
      </dl>
      <label className="check">
        <input type="checkbox" checked={a.auto_search_enabled} disabled={busy}
          onChange={async (e) => { try { await api.patch(`/accounts/${a.id}`, { auto_search_enabled: e.target.checked }); onChange(); } catch (err) { toast("error", errText(err)); } }} />
        {t("sites.auto_search")}
      </label>
      {a.auto_search_enabled && !autoAllowed && <p className="muted small">{t("sites.auto_needs_permission")}</p>}
      {a.login_window === "open" && (
        <div className="hint-box">
          <p>{t("sites.login_window_open")}</p>
          <button className="btn btn-sm" onClick={() => void call("close-login", "sites.login_closed")}>{t("sites.close_login")}</button>
        </div>
      )}
      {a.login_window?.startsWith("failed:") && <p className="form-error">{t(`error.${a.login_window.slice(7)}`)}</p>}
      <div className="row-actions wrap">
        <button className="btn btn-sm btn-primary" disabled={busy || a.busy} onClick={() => void call("open-login", "sites.login_opened")}>{t("sites.open_login")}</button>
        <button className="btn btn-sm" disabled={busy || a.busy} onClick={() => void call("check-login", "sites.checked")}>{t("sites.check_login")}</button>
        {a.login_status === "check_failed" && <button className="btn btn-sm" onClick={() => void call("confirm-login", "sites.confirmed")}>{t("sites.confirm_login")}</button>}
        <button className="btn btn-sm" disabled={a.busy} onClick={() => setConfirm("clear")}>{t("sites.clear_session")}</button>
        <button className="btn btn-sm btn-ghost-danger" disabled={a.busy} onClick={async () => { setAffected(await api.get(`/accounts/${a.id}/affected-tasks`)); setConfirm("delete"); }}>{t("common.delete")}</button>
      </div>
      {confirm === "clear" && (
        <ConfirmDialog title={t("sites.clear_session")} message={t("sites.clear_body")} onClose={() => setConfirm(null)}
          onConfirm={async () => { await api.post(`/accounts/${a.id}/clear-session`); toast("success", t("sites.cleared")); onChange(); }} />
      )}
      {confirm === "delete" && (
        <ConfirmDialog danger title={t("sites.delete_title")} confirmLabel={t("common.delete")} onClose={() => setConfirm(null)}
          message={<>
            <p>{t("sites.delete_body")}</p>
            {affected.length > 0 ? <><b>{t("sites.affected_tasks")}</b><ul>{affected.map((x) => <li key={x.id}>{x.name}</li>)}</ul></> : <p className="muted">{t("sites.no_affected")}</p>}
          </>}
          onConfirm={async () => {
            await api.post(`/accounts/${a.id}/delete`, { action, rebind_to: action === "rebind" ? rebindTo || null : null });
            toast("success", t("sites.deleted"));
            onChange();
          }}>
          <Field label={t("sites.delete_action")}>
            <select value={action} onChange={(e) => setAction(e.target.value)}>
              <option value="pause">{t("sites.action.pause")}</option>
              {others.length > 0 && <option value="rebind">{t("sites.action.rebind")}</option>}
              <option value="session_only">{t("sites.action.session_only")}</option>
            </select>
          </Field>
          {action === "rebind" && (
            <Field label={t("sites.rebind_to")}>
              <select value={rebindTo} onChange={(e) => setRebindTo(e.target.value ? Number(e.target.value) : "")}>
                <option value="">—</option>{others.map((o) => <option key={o.id} value={o.id}>{o.account_alias}</option>)}
              </select>
            </Field>
          )}
        </ConfirmDialog>
      )}
    </section>
  );
}

function AddAccount({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const { t } = useI18n();
  const { sites, toast } = useApp();
  const errText = useErrorText();
  const [siteId, setSiteId] = useState(sites[0]?.id ?? "");
  const [alias, setAlias] = useState("");
  const [note, setNote] = useState("");
  const [auto, setAuto] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  return (
    <Modal title={t("sites.add_account")} onClose={onClose}
      footer={<><button className="btn" onClick={onClose}>{t("common.cancel")}</button>
        <button className="btn btn-primary" onClick={async () => {
          if (!alias.trim()) { setErr(t("sites.alias_required")); return; }
          try {
            await api.post("/accounts", { site_id: siteId, account_alias: alias, note: note || null, auto_search_enabled: auto });
            toast("success", t("sites.account_created"));
            onDone();
            onClose();
          } catch (e) { setErr(errText(e)); }
        }}>{t("common.save")}</button></>}>
      <ol className="steps-list small">
        <li>{t("sites.flow1")}</li><li>{t("sites.flow2")}</li><li>{t("sites.flow3")}</li><li>{t("sites.flow4")}</li>
      </ol>
      <Field label={t("sites.site")} required>
        <select value={siteId} onChange={(e) => setSiteId(e.target.value)}>{sites.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</select>
      </Field>
      <Field label={t("sites.alias")} required hint={t("sites.alias_hint")}><input value={alias} onChange={(e) => setAlias(e.target.value)} placeholder={t("sites.alias_ph")} /></Field>
      <Field label={t("sites.note")}><input value={note} onChange={(e) => setNote(e.target.value)} /></Field>
      <label className="check"><input type="checkbox" checked={auto} onChange={(e) => setAuto(e.target.checked)} />{t("sites.auto_search")}</label>
      <p className="muted small">{t("sites.no_password_note")}</p>
      {err && <p className="form-error">{err}</p>}
    </Modal>
  );
}

function PermissionEditor({ site, ptype, onClose, onDone }: { site: Site; ptype: string; onClose: () => void; onDone: () => void }) {
  const { t } = useI18n();
  const errText = useErrorText();
  const cur = site.permissions[ptype];
  const [status, setStatus] = useState(cur.status);
  const [source, setSource] = useState(cur.source ?? "");
  const [note, setNote] = useState(cur.note ?? "");
  const [err, setErr] = useState<string | null>(null);
  return (
    <Modal title={`${site.name} · ${t(`perm.${ptype}`)}`} onClose={onClose}
      footer={<><button className="btn" onClick={onClose}>{t("common.cancel")}</button>
        <button className="btn btn-primary" onClick={async () => {
          if (status === "allowed" && !source.trim()) { setErr(t("error.permission_source_required")); return; }
          try { await api.put(`/sites/${site.id}/permissions/${ptype}`, { status, source: source || null, note: note || null }); onDone(); onClose(); } catch (e) { setErr(errText(e)); }
        }}>{t("common.save")}</button></>}>
      <p className="hint-box">{t("sites.permission_warning")}</p>
      <Field label={t("sites.permission")}>
        <select value={status} onChange={(e) => setStatus(e.target.value as typeof status)}>
          {(["unknown", "allowed", "denied"] as const).map((s) => <option key={s} value={s}>{t(`perm_status.${s}`)}</option>)}
        </select>
      </Field>
      <Field label={t("sites.permission_source")} required={status === "allowed"} hint={t("sites.permission_source_hint")}><textarea rows={2} value={source} onChange={(e) => setSource(e.target.value)} /></Field>
      <Field label={t("sites.note")}><input value={note} onChange={(e) => setNote(e.target.value)} /></Field>
      {err && <p className="form-error">{err}</p>}
    </Modal>
  );
}
