import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { InvestmentAssessment, InvestmentInputValue, InvestmentProfile, InvLabel, InvText } from "../api/types";
import { useI18n } from "../i18n";
import { useApp } from "../lib/app";
import { formatDateTime, formatNumber } from "../lib/format";
import { useLoad } from "../lib/hooks";
import { Badge, Field, Modal, StateView, useErrorText } from "./common";

const LABELS: InvLabel[] = ["resale_candidate", "rental_candidate", "owner_candidate", "low_value", "insufficient_data"];
const LABEL_TONE: Record<InvLabel, "info" | "ok" | "warn" | "muted"> = {
  resale_candidate: "info", rental_candidate: "ok", owner_candidate: "ok", low_value: "warn", insufficient_data: "muted",
};
const NUMERIC_INPUTS = [
  "monthly_rent_yen", "management_fee_monthly_yen", "repair_reserve_monthly_yen", "property_tax_annual_yen", "insurance_annual_yen",
  "renovation_budget_yen", "expected_sale_price_yen", "holding_months", "commute_minutes", "comparable_unit_price_yen_m2",
];
const CONDITIONS = ["poor", "average", "good", "renovated"];
const RISK_FLAGS = ["structure", "title_rights", "location", "noise", "sunlight", "incident", "other"];
const TAGS = ["price_below_area", "unit_price_high", "interior_old", "move_in_ready", "renovation_cost_unconfirmed",
  "renovation_possible_needs_calc", "rental_yield_good", "holding_cost_high", "near_station", "old_building",
  "old_seismic_standard", "repair_risk", "low_liquidity", "data_insufficient"];

interface History { items: InvestmentAssessment[]; ai_ready: boolean; ai_unavailable_reason: string | null; profiles: InvestmentProfile[] }

export function LabelBadge({ label, overridden }: { label: InvLabel; overridden?: boolean }) {
  const { t } = useI18n();
  return <Badge tone={LABEL_TONE[label]}>{t(`inv.label.${label}`)}{overridden ? " ✎" : ""}</Badge>;
}

/** Field / calculation / missing-item names ("profile.x" means a value in the investment standard). */
export function useFieldName() {
  const { t } = useI18n();
  return (k: string) => (k.startsWith("profile.") ? t("inv.in_profile", { field: t(`inv.field.${k.slice(8)}`) }) : t(`inv.field.${k}`));
}

function useInvText() {
  const { t, lang } = useI18n();
  return (x: InvText) => {
    if (x.text) return x.text;
    const params = Object.fromEntries(Object.entries(x.params ?? {}).map(([k, v]) => {
      if (typeof v === "number") return [k, formatNumber(v, lang, 2)];
      if (Array.isArray(v)) return [k, v.map((i) => t(k === "flags" ? `inv.flag.${i}` : `inv.pref.${i}`)).join("・")];
      return [k, String(v)];
    }));
    return t(x.key ?? "", params);
  };
}

function formatCalc(value: number, unit: string, lang: string) {
  const n = formatNumber(value, lang as "ja", 2);
  return unit === "yen" ? `${n} 円` : unit === "yen_m2" ? `${n} 円/㎡` : unit === "pct" ? `${n}%` : unit === "years" ? `${n}` : n;
}

/** FR-11 on the property page: the user's inputs, the latest assessment with everything behind it, corrections and history. */
export function InvestmentPanel({ listingId, isBuy }: { listingId: number; isBuy: boolean }) {
  const { t, lang } = useI18n();
  const { settings, toast } = useApp();
  const errText = useErrorText();
  const hist = useLoad(() => api.get<History>(`/properties/${listingId}/investment-assessments`), [listingId]);
  const [profileId, setProfileId] = useState<number | "">("");
  const [useAi, setUseAi] = useState(true);
  const [busy, setBusy] = useState(false);
  const [editInputs, setEditInputs] = useState(false);
  const [overriding, setOverriding] = useState<InvestmentAssessment | null>(null);
  const [showId, setShowId] = useState<number | null>(null);
  const fieldName = useFieldName();

  useEffect(() => {
    const def = hist.data?.profiles.find((p) => p.is_default);
    if (profileId === "" && def) setProfileId(def.id);
  }, [hist.data, profileId]);

  if (!isBuy) return <p className="muted small">{t("inv.buy_only")}</p>;

  const assess = async () => {
    setBusy(true);
    try {
      const a = await api.post<InvestmentAssessment>(`/properties/${listingId}/investment-assessments`, { profile_id: profileId || null, use_ai: useAi });
      toast(a.status === "ai_rejected" ? "error" : "success", t(`inv.done.${a.status}`));
      setShowId(null);
      hist.reload();
    } catch (e) { toast("error", errText(e)); } finally { setBusy(false); }
  };

  const items = hist.data?.items ?? [];
  const current = items.find((a) => a.id === showId) ?? items[0];
  return (
    <StateView loading={hist.loading} error={hist.error} onRetry={hist.reload}>
      {hist.data && (
        <div className="investment">
          <p className="hint-box small">{t("inv.disclaimer")}</p>
          {hist.data.profiles.length === 0 ? (
            <p>{t("inv.need_profile")} <Link to="/settings#investment">{t("inv.go_settings")}</Link></p>
          ) : (
            <div className="row-actions wrap">
              <select value={profileId} onChange={(e) => setProfileId(Number(e.target.value))} aria-label={t("inv.profile")}>
                {hist.data.profiles.map((p) => <option key={p.id} value={p.id}>{p.name}{p.is_default ? ` (${t("inv.default")})` : ""}</option>)}
              </select>
              <label className="check" title={hist.data.ai_ready ? "" : t(`error.${hist.data.ai_unavailable_reason}`)}>
                <input type="checkbox" checked={useAi && hist.data.ai_ready} disabled={!hist.data.ai_ready} onChange={(e) => setUseAi(e.target.checked)} />
                {t("inv.use_ai")}
              </label>
              <button className="btn btn-primary btn-sm" disabled={busy} onClick={() => void assess()}>
                {busy ? <><span className="spinner" /> {t("inv.assessing")}</> : items.length ? t("inv.reassess") : t("inv.assess")}
              </button>
              <button className="btn btn-sm" onClick={() => setEditInputs(true)}>{t("inv.edit_inputs")}</button>
              {!hist.data.ai_ready && <span className="muted small">{t("inv.rules_only_note", { reason: t(`error.${hist.data.ai_unavailable_reason}`) })}</span>}
            </div>
          )}

          {current && <AssessmentView a={current} latest={current.id === items[0]?.id} onOverride={() => setOverriding(current)} />}

          {items.length > 1 && (
            <details>
              <summary>{t("inv.history", { n: items.length })}</summary>
              <table className="table">
                <thead><tr><th>{t("inv.col.time")}</th><th>{t("inv.col.label")}</th><th>{t("inv.col.score")}</th><th>{t("inv.col.mode")}</th><th>{t("inv.col.model")}</th><th /></tr></thead>
                <tbody>
                  {items.map((a) => (
                    <tr key={a.id} className={a.id === current?.id ? "row-selected" : ""}>
                      <td className="nowrap">{formatDateTime(a.created_at, lang, settings.timezone)}</td>
                      <td><LabelBadge label={a.final_label} overridden={a.overrides.length > 0} /></td>
                      <td>{a.score ?? "—"}</td>
                      <td>{t(`inv.status.${a.status}`)}</td>
                      <td className="mono small">{a.model_name ?? "—"}</td>
                      <td><button className="btn btn-sm btn-ghost" onClick={() => setShowId(a.id)}>{t("inv.show")}</button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </details>
          )}
          {!items.length && hist.data.profiles.length > 0 && <p className="muted">{t("inv.not_assessed")}</p>}
          {editInputs && <InputsEditor listingId={listingId} fieldName={fieldName} onClose={() => setEditInputs(false)} onSaved={hist.reload} />}
          {overriding && <OverrideDialog a={overriding} onClose={() => setOverriding(null)} onSaved={hist.reload} />}
        </div>
      )}
    </StateView>
  );
}

function AssessmentView({ a, latest, onOverride }: { a: InvestmentAssessment; latest: boolean; onOverride: () => void }) {
  const { t, lang } = useI18n();
  const { settings, siteName } = useApp();
  const text = useInvText();
  const fieldName = useFieldName();
  const tz = settings.timezone;
  const values = Object.entries(a.calculations.values ?? {});
  const lastOverride = a.overrides[a.overrides.length - 1];
  return (
    <section className="assessment">
      <div className="assessment-head">
        <LabelBadge label={a.final_label} overridden={!!lastOverride} />
        {a.score != null && <span className="score" title={t("inv.score_help")}>{t("inv.score", { n: a.score })}</span>}
        {a.confidence && <span className="small">{t("inv.confidence")}: <b>{t(`inv.conf.${a.confidence}`)}</b></span>}
        <Badge tone={a.status === "ai" ? "info" : a.status === "ai_rejected" ? "bad" : "muted"}>{t(`inv.status.${a.status}`)}</Badge>
        {latest && a.stale && <Badge tone="warn">{t("inv.stale")}</Badge>}
        <span className="muted small">{formatDateTime(a.created_at, lang, tz)}</span>
        <button className="btn btn-sm" onClick={onOverride}>{t("inv.override")}</button>
      </div>
      {a.status === "ai_rejected" && <p className="form-error small">{t("inv.ai_rejected_note", { reason: a.ai_error?.split(":")[1] ?? "" })}</p>}
      {a.status === "rules_only" && a.ai_error && !["not_requested"].includes(a.ai_error) && (
        <p className="muted small">{t("inv.rules_only_because", { reason: t(`error.${a.ai_error}`) })}</p>
      )}
      {lastOverride && (
        <p className="small">{t("inv.override_info", { label: t(`inv.label.${a.primary_label}`), reason: lastOverride.reason })}</p>
      )}
      {a.final_tags.length > 0 && <p>{a.final_tags.map((g) => <span key={g} className="tag">{t(`inv.tag.${g}`)}</span>)}</p>}

      <h4>{t("inv.reasons")}</h4>
      <ul>{a.reasons.map((r, i) => <li key={i}>{text(r)} {r.refs.length > 0 && <span className="muted small">[{r.refs.map(fieldName).join(", ")}]</span>}</li>)}</ul>
      {a.risks.length > 0 && <><h4>{t("inv.risks")}</h4><ul>{a.risks.map((r, i) => <li key={i}>{text(r)}</li>)}</ul></>}
      {a.missing_fields.length > 0 && (
        <div className="hint-box hint-warn">
          <b>{t("inv.missing")}</b>: {a.missing_fields.map(fieldName).join("、")}
          <ul className="small">
            {a.calculations.next_steps.map((s, i) => (
              <li key={i}>{s.key === "next.investigate_risks" ? t("inv.next.investigate_risks") : t("inv.next.fill", { field: fieldName(String(s.key).slice(5).replace(/^profile_/, "profile.")) })}</li>
            ))}
            {(a.calculations.ai_next_steps ?? []).map((s, i) => <li key={`ai${i}`}>{s}</li>)}
          </ul>
        </div>
      )}
      {!a.missing_fields.length && (a.calculations.ai_next_steps ?? []).length > 0 && (
        <><h4>{t("inv.next_steps")}</h4><ul>{a.calculations.ai_next_steps!.map((s, i) => <li key={i}>{s}</li>)}</ul></>
      )}

      <details>
        <summary>{t("inv.calculations", { n: values.length })}</summary>
        <table className="table calc-table">
          <thead><tr><th>{t("inv.col.metric")}</th><th className="num">{t("inv.col.value")}</th><th>{t("inv.col.formula")}</th></tr></thead>
          <tbody>
            {values.map(([k, c]) => (
              <tr key={k}>
                <td>{fieldName(k)}</td>
                <td className="num nowrap">{formatCalc(c.value, c.unit, lang)}</td>
                <td className="small"><code>{c.formula}</code></td>
              </tr>
            ))}
          </tbody>
        </table>
        {Object.keys(a.calculations.assumptions ?? {}).length > 0 && (
          <p className="small">{t("inv.assumptions")}: {Object.entries(a.calculations.assumptions).map(([k, v]) => `${fieldName(k)} ${formatNumber(v, lang, 2)}`).join(" / ")}</p>
        )}
        <p className="muted small">{t("inv.paths")}: {Object.entries(a.calculations.paths ?? {}).map(([k, p]) => `${t(`inv.target.${k}`)} → ${t(`inv.outcome.${p.outcome}`)}`).join(" · ")}</p>
      </details>

      <details>
        <summary>{t("inv.sources")}</summary>
        <ul className="small">
          {a.sources.listing && <li>{t("inv.src.listing", { site: a.sources.listing.site_id ? siteName(a.sources.listing.site_id) : "—", at: formatDateTime(a.sources.listing.last_seen_at, lang, tz) })}</li>}
          {Object.entries(a.sources.user_inputs ?? {}).map(([k, s]) => <li key={k}>{fieldName(k)}: {s.source} ({formatDateTime(s.updated_at, lang, tz)})</li>)}
          {a.sources.comparables && <li>{t("inv.src.comparables", { n: a.sources.comparables.n, from: a.sources.comparables.from, to: a.sources.comparables.to })}</li>}
          {a.sources.profile && <li>{t("inv.src.profile", { name: a.sources.profile.name })}</li>}
        </ul>
        <p className="muted small mono">{t("inv.versions", { rule: a.rule_version, prompt: a.prompt_version ?? "—", model: [a.model_provider, a.model_version ?? a.model_name].filter(Boolean).join(" / ") || "—" })} · #{a.input_snapshot_hash.slice(0, 10)}</p>
      </details>
    </section>
  );
}

function InputsEditor({ listingId, fieldName, onClose, onSaved }: { listingId: number; fieldName: (k: string) => string; onClose: () => void; onSaved: () => void }) {
  const { t, lang } = useI18n();
  const { settings, toast } = useApp();
  const errText = useErrorText();
  const current = useLoad(() => api.get<Record<string, InvestmentInputValue>>(`/properties/${listingId}/investment-inputs`), [listingId]);
  const [form, setForm] = useState<Record<string, { value: string | string[]; source: string }>>({});
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    if (!current.data) return;
    setForm(Object.fromEntries(Object.entries(current.data).map(([k, v]) => [k, { value: Array.isArray(v.value) ? v.value : String(v.value), source: v.source }])));
  }, [current.data]);
  const set = (k: string, patch: Partial<{ value: string | string[]; source: string }>) => setForm({ ...form, [k]: { ...{ value: "", source: "" }, ...form[k], ...patch } });
  const save = async () => {
    const body: Record<string, unknown> = {};
    for (const k of [...NUMERIC_INPUTS, "interior_condition", "risk_flags"]) {
      const f = form[k];
      const empty = !f || f.value === "" || (Array.isArray(f.value) && f.value.length === 0);
      if (empty) { if (current.data?.[k]) body[k] = null; continue; }
      body[k] = { value: NUMERIC_INPUTS.includes(k) ? Number(f.value) : f.value, source: f.source.trim() || t("inv.source_self") };
    }
    try {
      await api.put(`/properties/${listingId}/investment-inputs`, body);
      toast("success", t("settings.saved"));
      onSaved();
      onClose();
    } catch (e) { setErr(errText(e)); }
  };
  const flags = (form.risk_flags?.value as string[] | undefined) ?? [];
  return (
    <Modal wide title={t("inv.inputs_title")} onClose={onClose}
      footer={<><button className="btn" onClick={onClose}>{t("common.cancel")}</button><button className="btn btn-primary" onClick={() => void save()}>{t("common.save")}</button></>}>
      <p className="hint-box small">{t("inv.inputs_help")}</p>
      <StateView loading={current.loading} error={current.error} onRetry={current.reload}>
        <table className="table input-table">
          <thead><tr><th>{t("inv.col.item")}</th><th>{t("inv.col.value")}</th><th>{t("inv.col.source")}</th><th>{t("inv.col.updated")}</th></tr></thead>
          <tbody>
            <tr>
              <td>{fieldName("interior_condition")}</td>
              <td><select value={(form.interior_condition?.value as string) ?? ""} onChange={(e) => set("interior_condition", { value: e.target.value })}>
                <option value="">—</option>{CONDITIONS.map((c) => <option key={c} value={c}>{t(`inv.cond.${c}`)}</option>)}</select></td>
              <td><input value={form.interior_condition?.source ?? ""} placeholder={t("inv.source_ph")} onChange={(e) => set("interior_condition", { source: e.target.value })} /></td>
              <td className="muted small">{formatDateTime(current.data?.interior_condition?.updated_at ?? null, lang, settings.timezone)}</td>
            </tr>
            {NUMERIC_INPUTS.map((k) => (
              <tr key={k}>
                <td>{fieldName(k)}</td>
                <td><input type="number" min={0} value={(form[k]?.value as string) ?? ""} placeholder={k.endsWith("_yen") || k.endsWith("_yen_m2") ? t("unit.yen") : k.endsWith("minutes") ? t("unit.minutes") : t("unit.months")} onChange={(e) => set(k, { value: e.target.value })} /></td>
                <td><input value={form[k]?.source ?? ""} placeholder={t("inv.source_ph")} onChange={(e) => set(k, { source: e.target.value })} /></td>
                <td className="muted small">{formatDateTime(current.data?.[k]?.updated_at ?? null, lang, settings.timezone)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <Field label={fieldName("risk_flags")} hint={t("inv.risk_flags_hint")}>
          <div className="chips">
            {RISK_FLAGS.map((f) => {
              const on = flags.includes(f);
              return <label key={f} className={`chip ${on ? "on" : ""}`}><input type="checkbox" checked={on} onChange={() => set("risk_flags", { value: on ? flags.filter((x) => x !== f) : [...flags, f] })} />{t(`inv.flag.${f}`)}</label>;
            })}
          </div>
        </Field>
      </StateView>
      {err && <p className="form-error">{err}</p>}
    </Modal>
  );
}

function OverrideDialog({ a, onClose, onSaved }: { a: InvestmentAssessment; onClose: () => void; onSaved: () => void }) {
  const { t } = useI18n();
  const errText = useErrorText();
  const [label, setLabel] = useState<InvLabel>(a.final_label);
  const [tags, setTags] = useState<string[]>(a.final_tags);
  const [reason, setReason] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const save = async () => {
    if (!reason.trim()) { setErr(t("error.override_reason_required")); return; }
    try { await api.patch(`/investment-assessments/${a.id}/override`, { user_label: label, user_tags: tags, reason }); onSaved(); onClose(); } catch (e) { setErr(errText(e)); }
  };
  return (
    <Modal title={t("inv.override")} onClose={onClose}
      footer={<><button className="btn" onClick={onClose}>{t("common.cancel")}</button><button className="btn btn-primary" onClick={() => void save()}>{t("common.save")}</button></>}>
      <p className="small">{t("inv.override_help", { label: t(`inv.label.${a.primary_label}`) })}</p>
      <Field label={t("inv.col.label")} required>
        <select value={label} onChange={(e) => setLabel(e.target.value as InvLabel)}>{LABELS.map((l) => <option key={l} value={l}>{t(`inv.label.${l}`)}</option>)}</select>
      </Field>
      <div className="chips">
        {TAGS.map((g) => {
          const on = tags.includes(g);
          return <label key={g} className={`chip ${on ? "on" : ""}`}><input type="checkbox" checked={on} onChange={() => setTags(on ? tags.filter((x) => x !== g) : [...tags, g])} />{t(`inv.tag.${g}`)}</label>;
        })}
      </div>
      <Field label={t("inv.override_reason")} required><textarea rows={3} value={reason} onChange={(e) => setReason(e.target.value)} /></Field>
      {err && <p className="form-error">{err}</p>}
    </Modal>
  );
}
