import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { Backup, Lang, Settings } from "../api/types";
import { ConfirmDialog, Field, PageHeader, useErrorText } from "../components/common";
import { LANGS, useI18n } from "../i18n";
import { useApp } from "../lib/app";
import { formatDateTime, formatNumber } from "../lib/format";
import { useLoad, useUnsavedGuard } from "../lib/hooks";
import { AIServicesPanel } from "../components/AIServices";
import { InvestmentProfilesPanel } from "../components/InvestmentProfiles";

const LANG_LABEL: Record<Lang, string> = { ja: "日本語", zh: "简体中文", en: "English" };
const AI_FIELDS = ["property_type", "city", "price_yen", "area_m2", "layout", "built_year", "address", "building_name", "events"];

export default function SettingsPage() {
  const { t, lang, setLang } = useI18n();
  const { settings, saveSettings, system, toast } = useApp();
  const errText = useErrorText();
  const [form, setForm] = useState<Settings>(settings);
  const [state, setState] = useState<"idle" | "saved" | "invalid">("idle");
  const [error, setError] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<{ kind: "restore"; name: string } | { kind: "delete_all" } | null>(null);
  const [deleteText, setDeleteText] = useState("");
  const backups = useLoad(() => api.get<Backup[]>("/backups"), []);

  useEffect(() => setForm((f) => ({ ...f, language: settings.language })), [settings.language]);
  const dirty = JSON.stringify({ ...form, language: "" }) !== JSON.stringify({ ...settings, language: "" });
  useUnsavedGuard(dirty, t("common.unsaved_confirm"));
  const set = <K extends keyof Settings>(k: K, v: Settings[K]) => { setForm({ ...form, [k]: v }); setState("idle"); };

  const save = async () => {
    if (!form.timezone.trim() || form.backup_keep < 1) { setState("invalid"); setError(t("settings.invalid")); return; }
    try {
      const { language: _ignored, ...rest } = form;
      void _ignored;
      await saveSettings(rest);
      setState("saved");
      setError(null);
      toast("success", t("settings.saved"));
    } catch (e) {
      setState("invalid");
      setError(errText(e));
    }
  };

  return (
    <div className="page">
      <PageHeader title={t("menu.settings")} description={t("settings.desc")}
        actions={<>
          {dirty && <span className="badge badge-warn">{t("settings.unsaved")}</span>}
          {state === "saved" && !dirty && <span className="badge badge-ok">{t("settings.saved")}</span>}
          <button className="btn btn-primary" disabled={!dirty} onClick={() => void save()}>{t("common.save")}</button>
        </>} />
      {error && <p className="form-error">{error}</p>}

      <section className="card">
        <h2 className="card-title">{t("settings.group.language")}</h2>
        <div className="form-grid two">
          <Field label={t("settings.language")} hint={t("settings.language_hint")}>
            <select value={lang} onChange={(e) => setLang(e.target.value as Lang)}>{LANGS.map((l) => <option key={l} value={l}>{LANG_LABEL[l]}</option>)}</select>
          </Field>
          <Field label={t("settings.timezone")} hint={t("settings.timezone_hint")}><input value={form.timezone} onChange={(e) => set("timezone", e.target.value)} /></Field>
        </div>
        <p className="muted small">{t("settings.format_preview")}: {formatDateTime(new Date().toISOString(), lang, form.timezone)} · {formatNumber(39800000, lang)} · 70.5㎡</p>
      </section>

      <section className="card">
        <h2 className="card-title">{t("settings.group.running")}</h2>
        <div className="form-grid two">
          <label className="check"><input type="checkbox" checked={form.startup_open_browser} onChange={(e) => set("startup_open_browser", e.target.checked)} />{t("settings.open_browser")}</label>
          <label className="check"><input type="checkbox" checked={form.tray_enabled} onChange={(e) => set("tray_enabled", e.target.checked)} />{t("settings.tray")}</label>
          <Field label={t("settings.close_behavior")}>
            <select value={form.close_behavior} onChange={(e) => set("close_behavior", e.target.value as "tray" | "exit")}>
              <option value="tray">{t("settings.close.tray")}</option><option value="exit">{t("settings.close.exit")}</option>
            </select>
          </Field>
          <label className="check"><input type="checkbox" checked={form.network_retry_enabled} onChange={(e) => set("network_retry_enabled", e.target.checked)} />{t("settings.network_retry")}</label>
          <Field label={t("schedule.window_start")}><input type="time" value={form.allowed_window_start} onChange={(e) => set("allowed_window_start", e.target.value)} /></Field>
          <Field label={t("schedule.window_end")}><input type="time" value={form.allowed_window_end} onChange={(e) => set("allowed_window_end", e.target.value)} /></Field>
        </div>
        <p className="muted small">{t("settings.retry_rule")}</p>
      </section>

      <section className="card">
        <h2 className="card-title">{t("settings.group.data")}</h2>
        {system && (
          <dl className="kv small">
            <dt>{t("settings.data_dir")}</dt><dd className="mono">{system.data_dir}</dd>
            <dt>{t("settings.database")}</dt><dd className="mono">{system.database}</dd>
            <dt>{t("settings.backups_dir")}</dt><dd className="mono">{system.backups_dir}</dd>
            <dt>{t("settings.logs_dir")}</dt><dd className="mono">{system.logs_dir}</dd>
          </dl>
        )}
        <div className="form-grid two">
          <label className="check"><input type="checkbox" checked={form.backup_auto_daily} onChange={(e) => set("backup_auto_daily", e.target.checked)} />{t("settings.auto_backup")}</label>
          <Field label={t("settings.backup_keep")}><input type="number" min={1} value={form.backup_keep} onChange={(e) => set("backup_keep", Number(e.target.value))} /></Field>
        </div>
        <div className="row-actions wrap">
          <button className="btn" onClick={async () => { try { await api.post("/backups"); toast("success", t("settings.backup_done")); backups.reload(); } catch (e) { toast("error", errText(e)); } }}>{t("settings.backup_now")}</button>
          <button className="btn" onClick={async () => { try { const r = await api.post<{ directory: string }>("/export"); toast("success", t("settings.exported", { dir: r.directory })); } catch (e) { toast("error", errText(e)); } }}>{t("settings.export")}</button>
          <button className="btn btn-ghost-danger" onClick={() => setConfirm({ kind: "delete_all" })}>{t("settings.delete_all")}</button>
        </div>
        <p className="muted small">{t("settings.backup_note")}</p>
        <table className="table">
          <thead><tr><th>{t("settings.backup_file")}</th><th>{t("settings.backup_time")}</th><th>{t("settings.size")}</th><th /></tr></thead>
          <tbody>
            {(backups.data ?? []).map((b) => (
              <tr key={b.name}>
                <td className="mono small">{b.name}</td><td>{formatDateTime(b.created_at, lang, form.timezone)}</td><td>{formatNumber(b.size / 1024, lang)} KB</td>
                <td><button className="btn btn-sm" onClick={() => setConfirm({ kind: "restore", name: b.name })}>{t("settings.restore")}</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section className="card">
        <h2 className="card-title">{t("settings.group.notifications")}</h2>
        <label className="check"><input type="checkbox" checked={form.notifications_in_app} onChange={(e) => set("notifications_in_app", e.target.checked)} />{t("settings.in_app")}</label>
        <p className="muted small">{t("settings.notify_future")}</p>
      </section>

      <section className="card">
        <h2 className="card-title">{t("settings.group.ai")}</h2>
        <p className="hint-box">{t("settings.ai_principle")}</p>
        <label className="check"><input type="checkbox" checked={form.ai_enabled} onChange={(e) => set("ai_enabled", e.target.checked)} />{t("settings.ai_enabled")}</label>
        <p className="muted small">{t("ai.switch_note")}</p>
        <AIServicesPanel />
        <h3>{t("ai.summary_title")}</h3>
        <Field label={t("settings.ai_fields")}>
          <div className="chips">
            {AI_FIELDS.map((f) => {
              const on = form.ai_allowed_fields.includes(f);
              return <label key={f} className={`chip ${on ? "on" : ""}`}><input type="checkbox" checked={on} onChange={() => set("ai_allowed_fields", on ? form.ai_allowed_fields.filter((x) => x !== f) : [...form.ai_allowed_fields, f])} />{t(`ai_field.${f}`)}</label>;
            })}
          </div>
        </Field>
        <label className="check"><input type="checkbox" checked={form.ai_send_notes} onChange={(e) => set("ai_send_notes", e.target.checked)} />{t("settings.ai_send_notes")}</label>
      </section>

      <section className="card" id="investment">
        <h2 className="card-title">{t("settings.group.investment")}</h2>
        <InvestmentProfilesPanel />
      </section>

      <section className="card">
        <h2 className="card-title">{t("settings.group.privacy")}</h2>
        {system && <p className="small">{t("settings.profiles_dir")}: <span className="mono">{system.profiles_dir}</span></p>}
        <div className="form-grid two">
          <Field label={t("settings.log_level")}>
            <select value={form.log_level} onChange={(e) => set("log_level", e.target.value)}>{["DEBUG", "INFO", "WARNING", "ERROR"].map((l) => <option key={l}>{l}</option>)}</select>
          </Field>
          <Field label={t("settings.retention_days")} hint={t("settings.retention_hint")}><input type="number" min={0} value={form.data_retention_days} onChange={(e) => set("data_retention_days", Number(e.target.value))} /></Field>
        </div>
        <p className="muted small">{t("settings.privacy_note")} <Link to="/sites">{t("menu.sites")}</Link></p>
      </section>

      {confirm?.kind === "restore" && (
        <ConfirmDialog title={t("settings.restore")} message={t("settings.restore_body", { name: confirm.name })} onClose={() => setConfirm(null)}
          onConfirm={async () => {
            const v = await api.get<{ compatible: boolean }>(`/backups/${confirm.name}/verify`);
            if (!v.compatible) throw new Error("incompatible");
            await api.post(`/backups/${confirm.name}/restore`);
            toast("success", t("settings.restored"));
            window.setTimeout(() => window.location.reload(), 800);
          }} />
      )}
      {confirm?.kind === "delete_all" && (
        <ConfirmDialog danger title={t("settings.delete_all")} confirmLabel={t("common.delete")} onClose={() => { setConfirm(null); setDeleteText(""); }}
          message={<p>{t("settings.delete_all_body")}</p>}
          onConfirm={async () => {
            await api.post("/data/delete-all", { confirm: deleteText });
            window.setTimeout(() => window.location.reload(), 500);
          }}>
          <Field label={t("settings.type_delete")}><input value={deleteText} onChange={(e) => setDeleteText(e.target.value)} placeholder="DELETE" /></Field>
        </ConfirmDialog>
      )}
    </div>
  );
}
