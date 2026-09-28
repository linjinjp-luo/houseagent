import { useState } from "react";
import { api } from "../api/client";
import type { InvestmentProfile, InvestmentVocabulary, InvTarget } from "../api/types";
import { useI18n } from "../i18n";
import { useApp } from "../lib/app";
import { useLoad } from "../lib/hooks";
import { Badge, ConfirmDialog, Field, Modal, StateView, useErrorText } from "./common";

interface ProfilesResponse { items: InvestmentProfile[]; vocabulary: InvestmentVocabulary }

/** Thresholds grouped by the path they drive (spec 4.6.3). */
const GROUPS: { key: string; fields: string[] }[] = [
  { key: "rental", fields: ["min_gross_yield_pct", "min_net_yield_pct"] },
  { key: "resale", fields: ["min_resale_profit_yen", "min_resale_margin_pct", "resale_safety_margin_pct", "min_discount_pct", "max_renovation_budget_yen", "max_holding_months"] },
  { key: "general", fields: ["max_price_yen", "max_unit_price_yen_m2", "max_price_premium_pct", "max_age_years", "max_walk_minutes"] },
];

export function useProfiles() {
  return useLoad(() => api.get<ProfilesResponse>("/investment-profiles"), []);
}

/** FR-11: the user's own investment standards. Assessments always use one of these; nothing is a fixed default. */
export function InvestmentProfilesPanel() {
  const { t } = useI18n();
  const profiles = useProfiles();
  const [editing, setEditing] = useState<InvestmentProfile | "new" | null>(null);
  const [removing, setRemoving] = useState<InvestmentProfile | null>(null);
  return (
    <StateView loading={profiles.loading} error={profiles.error} onRetry={profiles.reload}>
      {profiles.data && (
        <>
          <p className="hint-box">{t("inv.profile_principle")}</p>
          {profiles.data.items.length === 0 && <p className="muted">{t("inv.no_profiles")}</p>}
          <ul className="profile-list">
            {profiles.data.items.map((p) => (
              <li key={p.id}>
                <b>{p.name}</b> <Badge tone="muted">{t(`inv.target.${p.target_type}`)}</Badge> {p.is_default && <Badge tone="info">{t("inv.default")}</Badge>}
                <span className="muted small"> {t("inv.threshold_count", { n: Object.keys(p.thresholds).length + Object.keys(p.assumptions).length })}</span>
                <span className="row-actions">
                  <button className="btn btn-sm" onClick={() => setEditing(p)}>{t("common.edit")}</button>
                  <button className="btn btn-sm btn-ghost-danger" onClick={() => setRemoving(p)}>{t("common.delete")}</button>
                </span>
              </li>
            ))}
          </ul>
          <button className="btn" onClick={() => setEditing("new")}>+ {t("inv.add_profile")}</button>
          {editing && <ProfileEditor vocab={profiles.data.vocabulary} profile={editing === "new" ? null : editing} onClose={() => setEditing(null)} onSaved={profiles.reload} />}
          {removing && (
            <ConfirmDialog danger title={t("inv.remove_profile")} message={t("inv.remove_profile_body", { name: removing.name })} confirmLabel={t("common.delete")}
              onClose={() => setRemoving(null)} onConfirm={async () => { await api.del(`/investment-profiles/${removing.id}`); profiles.reload(); }} />
          )}
        </>
      )}
    </StateView>
  );
}

function ProfileEditor({ vocab, profile, onClose, onSaved }: { vocab: InvestmentVocabulary; profile: InvestmentProfile | null; onClose: () => void; onSaved: () => void }) {
  const { t, lang } = useI18n();
  const { regions, toast } = useApp();
  const errText = useErrorText();
  const [name, setName] = useState(profile?.name ?? "");
  const [target, setTarget] = useState<InvTarget>(profile?.target_type ?? "any");
  const num = (o: Record<string, number> | undefined) => Object.fromEntries(Object.entries(o ?? {}).map(([k, v]) => [k, String(v)]));
  const [th, setTh] = useState<Record<string, string>>(num(profile?.thresholds));
  const [asm, setAsm] = useState<Record<string, string>>(num(profile?.assumptions));
  const [prefs, setPrefs] = useState(profile?.preferences ?? {});
  const [isDefault, setIsDefault] = useState(profile?.is_default ?? false);
  const [err, setErr] = useState<string | null>(null);

  const save = async () => {
    if (!name.trim()) { setErr(t("inv.name_required")); return; }
    const clean = (o: Record<string, string>) => Object.fromEntries(Object.entries(o).filter(([, v]) => v !== "").map(([k, v]) => [k, Number(v)]));
    const body = { name, target_type: target, thresholds: clean(th), assumptions: clean(asm), preferences: prefs, is_default: isDefault };
    try {
      if (profile) await api.patch(`/investment-profiles/${profile.id}`, body); else await api.post("/investment-profiles", body);
      toast("success", t("settings.saved"));
      onSaved();
      onClose();
    } catch (e) { setErr(errText(e)); }
  };
  const numField = (k: string, unit: string, values: Record<string, string>, set: (v: Record<string, string>) => void) => (
    <Field key={k} label={`${t(`inv.field.${k}`)} (${t(`unit.${unit}`)})`}>
      <input type="number" step="any" value={values[k] ?? ""} onChange={(e) => set({ ...values, [k]: e.target.value })} />
    </Field>
  );
  const layouts = prefs.layouts ?? [];

  return (
    <Modal wide title={t(profile ? "inv.edit_profile" : "inv.add_profile")} onClose={onClose}
      footer={<><button className="btn" onClick={onClose}>{t("common.cancel")}</button><button className="btn btn-primary" onClick={() => void save()}>{t("common.save")}</button></>}>
      <div className="form-grid two">
        <Field label={t("inv.profile_name")} required><input value={name} onChange={(e) => setName(e.target.value)} /></Field>
        <Field label={t("inv.target_type")} hint={t(`inv.target_help.${target}`)}>
          <select value={target} onChange={(e) => setTarget(e.target.value as InvTarget)}>
            {vocab.targets.map((x) => <option key={x} value={x}>{t(`inv.target.${x}`)}</option>)}
          </select>
        </Field>
      </div>
      <p className="muted small">{t("inv.empty_is_missing")}</p>
      {GROUPS.map((g) => (
        <fieldset key={g.key} className="fieldset">
          <legend>{t(`inv.group.${g.key}`)}</legend>
          <div className="form-grid three">{g.fields.map((k) => numField(k, vocab.thresholds[k], th, setTh))}</div>
        </fieldset>
      ))}
      <fieldset className="fieldset">
        <legend>{t("inv.group.assumptions")}</legend>
        <p className="muted small">{t("inv.assumptions_hint")}</p>
        <div className="form-grid three">{Object.entries(vocab.assumptions).map(([k, u]) => numField(k, u, asm, setAsm))}</div>
      </fieldset>
      <fieldset className="fieldset">
        <legend>{t("inv.group.owner")}</legend>
        <div className="form-grid three">
          <Field label={`${t("inv.field.min_area_m2")} (${t("unit.m2")})`}>
            <input type="number" value={prefs.min_area_m2 ?? ""} onChange={(e) => setPrefs({ ...prefs, min_area_m2: e.target.value === "" ? undefined : Number(e.target.value) })} />
          </Field>
          <Field label={`${t("inv.field.max_commute_minutes")} (${t("unit.minutes")})`}>
            <input type="number" value={prefs.max_commute_minutes ?? ""} onChange={(e) => setPrefs({ ...prefs, max_commute_minutes: e.target.value === "" ? undefined : Number(e.target.value) })} />
          </Field>
          <Field label={t("inv.field.cities")}>
            <select multiple size={4} value={prefs.cities ?? []} onChange={(e) => setPrefs({ ...prefs, cities: [...e.target.selectedOptions].map((o) => o.value) })}>
              {regions.cities.map((c) => <option key={c.code} value={c.code}>{c.name[lang]}</option>)}
            </select>
          </Field>
        </div>
        <div className="chips">
          {regions.layouts.map((l) => {
            const on = layouts.includes(l);
            return <label key={l} className={`chip ${on ? "on" : ""}`}><input type="checkbox" checked={on} onChange={() => setPrefs({ ...prefs, layouts: on ? layouts.filter((x) => x !== l) : [...layouts, l] })} />{l}</label>;
          })}
        </div>
      </fieldset>
      <label className="check"><input type="checkbox" checked={isDefault} onChange={(e) => setIsDefault(e.target.checked)} />{t("inv.make_default")}</label>
      {err && <p className="form-error">{err}</p>}
    </Modal>
  );
}
