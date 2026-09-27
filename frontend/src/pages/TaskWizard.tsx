import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, ApiError } from "../api/client";
import type { Account, Conditions, Priority, Schedule, ScheduleType, Task, TaskSite, Validation } from "../api/types";
import { Badge, Field, PageHeader, StateView, useErrorText } from "../components/common";
import { useI18n } from "../i18n";
import { useApp } from "../lib/app";
import { formatDateTime } from "../lib/format";
import { useLoad, useUnsavedGuard } from "../lib/hooks";
import { ConditionSummary, scheduleText } from "./taskText";

const EMPTY: Conditions = {
  deal_type: "buy",
  transaction_type: [], prefectures: [], cities: [], stations: [], price_min: null, price_max: null,
  price_includes_fees: false, area_min: null, area_max: null, building_age_max: null, walk_minutes_max: null,
  layouts: [], keywords_include: [], keywords_exclude: [], sort_order: null, result_limit: 100, region_logic: "OR",
};

interface Form {
  name: string;
  description: string;
  status: "active" | "draft";
  priority: Priority;
  conditions: Conditions;
  sites: TaskSite[];
  schedule_type: ScheduleType;
  schedule: Schedule;
  timezone: string;
}

const STEPS = ["wizard.step.basic", "wizard.step.conditions", "wizard.step.sites", "wizard.step.schedule", "wizard.step.confirm"];

function numOrNull(v: string): number | null {
  if (v.trim() === "") return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

function splitList(v: string): string[] {
  return v.split(/[,、，\n]/).map((x) => x.trim()).filter(Boolean);
}

export default function TaskWizard() {
  const { id } = useParams();
  const editing = id !== undefined;
  const { t, lang } = useI18n();
  const { settings, regions, sites, siteName, toast } = useApp();
  const nav = useNavigate();
  const errText = useErrorText();
  const accounts = useLoad(() => api.get<Account[]>("/accounts"), []);
  const existing = useLoad(() => (editing ? api.get<Task>(`/search-tasks/${id}`) : Promise.resolve(null)), [id]);

  const [step, setStep] = useState(0);
  const [form, setForm] = useState<Form>({
    name: "", description: "", status: "active", priority: "normal", conditions: EMPTY, sites: [],
    schedule_type: "manual",
    schedule: { time: "08:00", weekdays: [5], interval_hours: 6, window_start: settings.allowed_window_start, window_end: settings.allowed_window_end },
    timezone: settings.timezone,
  });
  const [dirty, setDirty] = useState(false);
  const [validation, setValidation] = useState<Record<string, Validation> | null>(null);
  const [ack, setAck] = useState(false);
  const [preview, setPreview] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [stationText, setStationText] = useState("");
  const [kwInc, setKwInc] = useState("");
  const [kwExc, setKwExc] = useState("");

  useUnsavedGuard(dirty && !saving, t("common.unsaved_confirm"));

  useEffect(() => {
    const task = existing.data;
    if (!task) return;
    setForm({
      name: task.name, description: task.description ?? "", status: task.status === "draft" ? "draft" : "active",
      priority: task.priority, conditions: { ...EMPTY, ...task.conditions },
      sites: task.sites.map((s) => ({ site_id: s.site_id, account_id: s.account_id })),
      schedule_type: task.schedule_type, schedule: { time: "08:00", weekdays: [5], interval_hours: 6, ...task.schedule }, timezone: task.timezone,
    });
    setStationText(task.conditions.stations.map((s) => s.name).join(", "));
    setKwInc(task.conditions.keywords_include.join(", "));
    setKwExc(task.conditions.keywords_exclude.join(", "));
  }, [existing.data]);

  const update = (patch: Partial<Form>) => { setForm((f) => ({ ...f, ...patch })); setDirty(true); };
  const cond = (patch: Partial<Conditions>) => update({ conditions: { ...form.conditions, ...patch } });
  const sched = (patch: Partial<Schedule>) => update({ schedule: { ...form.schedule, ...patch } });

  const siteIds = form.sites.map((s) => s.site_id);
  const condKey = JSON.stringify(form.conditions) + siteIds.join(",");

  // Tell the user per site which conditions are sent, filtered locally, or not supported.
  useEffect(() => {
    if (!siteIds.length || !form.conditions.prefectures.length) { setValidation(null); return; }
    const h = window.setTimeout(() => {
      api.post<Record<string, Validation>>("/search-tasks/validate", { conditions: form.conditions, site_ids: siteIds })
        .then((v) => setValidation(v)).catch(() => setValidation(null));
    }, 300);
    return () => window.clearTimeout(h);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [condKey]);

  useEffect(() => {
    if (step !== 3 && step !== 4) return;
    api.post<string[]>("/search-tasks/schedule-preview", { schedule_type: form.schedule_type, schedule: form.schedule, timezone: form.timezone, count: 3 })
      .then(setPreview).catch(() => setPreview([]));
  }, [step, form.schedule_type, form.schedule, form.timezone]);

  const isRent = form.conditions.deal_type === "rent";
  const unsupported = useMemo(() => Object.entries(validation ?? {}).filter(([, v]) => v.unsupported.length), [validation]);
  const cityOptions = regions.cities.filter((c) => form.conditions.prefectures.includes(c.prefecture));

  const stepError = (s: number): string | null => {
    const c = form.conditions;
    if (s === 0 && !form.name.trim()) return t("wizard.err.name");
    if (s === 1) {
      if (!c.prefectures.length) return t("wizard.err.prefecture");
      if (c.price_min != null && c.price_max != null && c.price_max < c.price_min) return t("wizard.err.price");
      if (c.area_min != null && c.area_max != null && c.area_max < c.area_min) return t("wizard.err.area");
      if ((c.area_min != null && c.area_min <= 0) || (c.area_max != null && c.area_max <= 0)) return t("wizard.err.area_positive");
      if (c.result_limit < 1 || c.result_limit > 500) return t("wizard.err.limit");
    }
    if (s === 2 && !form.sites.length) return t("wizard.err.sites");
    if (s === 4 && unsupported.length && !ack) return t("wizard.err.ack");
    return null;
  };

  const next = () => {
    const e = stepError(step);
    setError(e);
    if (!e) setStep((x) => Math.min(x + 1, STEPS.length - 1));
  };

  const save = async () => {
    for (let s = 0; s < STEPS.length; s++) {
      const e = stepError(s);
      if (e) { setStep(s); setError(e); return; }
    }
    const body = {
      name: form.name, description: form.description || null, status: form.status, priority: form.priority,
      schedule_type: form.schedule_type, schedule: form.schedule, timezone: form.timezone,
      conditions: {
        ...form.conditions,
        stations: splitList(stationText).map((name) => ({ name })),
        keywords_include: splitList(kwInc), keywords_exclude: splitList(kwExc),
      },
      sites: form.sites, acknowledge_unsupported: ack,
    };
    setSaving(true);
    setError(null);
    try {
      if (editing) await api.patch(`/search-tasks/${id}`, body);
      else await api.post("/search-tasks", body);
      setDirty(false);
      toast("success", editing ? t("wizard.saved_version") : t("wizard.created"));
      window.setTimeout(() => nav("/tasks"), 0);
    } catch (e) {
      setError(e instanceof ApiError && e.code === "CONDITION_UNSUPPORTED" ? t("wizard.err.ack") : errText(e));
      setSaving(false);
    }
  };

  const toggle = <K extends keyof Conditions>(key: K, value: string) => {
    const list = form.conditions[key] as unknown as string[];
    cond({ [key]: list.includes(value) ? list.filter((x) => x !== value) : [...list, value] } as Partial<Conditions>);
  };

  const setSite = (siteId: string, on: boolean) => {
    if (on) update({ sites: [...form.sites, { site_id: siteId, account_id: accountsFor(siteId)[0]?.id ?? null }] });
    else update({ sites: form.sites.filter((s) => s.site_id !== siteId) });
  };
  const accountsFor = (siteId: string) => (accounts.data ?? []).filter((a) => a.site_id === siteId);

  const supportBadge = (siteId: string, field: string) => {
    const v = validation?.[siteId];
    if (!v) return null;
    if (v.unsupported.includes(field)) return <Badge tone="bad">{t("support.unsupported")}</Badge>;
    if (v.local_filters.includes(field)) return <Badge tone="warn">{t("support.local_filter")}</Badge>;
    if (field in v.submit) return <Badge tone="ok">{t("support.supported")}</Badge>;
    return null;
  };

  return (
    <div className="page">
      <PageHeader title={editing ? t("wizard.edit_title") : t("wizard.new_title")} description={t("wizard.desc")} />
      <StateView loading={existing.loading || accounts.loading} error={existing.error ?? accounts.error} onRetry={existing.reload}>
        <ol className="wizard-steps">
          {STEPS.map((k, i) => (
            <li key={k} className={i === step ? "current" : i < step ? "done" : ""}>
              <button onClick={() => { if (i < step) setStep(i); }} disabled={i > step}>{i + 1}. {t(k)}</button>
            </li>
          ))}
        </ol>
        <section className="card wizard-body">
          {step === 0 && (
            <div className="form-grid">
              <Field label={t("wizard.name")} required hint={t("wizard.name_hint")}>
                <input value={form.name} onChange={(e) => update({ name: e.target.value })} maxLength={200} autoFocus />
              </Field>
              <Field label={t("wizard.purpose")}>
                <textarea value={form.description} onChange={(e) => update({ description: e.target.value })} rows={2} />
              </Field>
              <Field label={t("wizard.priority")} hint={t("wizard.priority_hint")}>
                <select value={form.priority} onChange={(e) => update({ priority: e.target.value as Priority })}>
                  {(["high", "normal", "low"] as const).map((p) => <option key={p} value={p}>{t(`priority.${p}`)}</option>)}
                </select>
              </Field>
              <Field label={t("wizard.status")}>
                <select value={form.status} onChange={(e) => update({ status: e.target.value as "active" | "draft" })}>
                  <option value="active">{t("task_status.active")}</option>
                  <option value="draft">{t("task_status.draft")}</option>
                </select>
              </Field>
              {editing && <p className="hint-box">{t("wizard.version_note")}</p>}
            </div>
          )}

          {step === 1 && (
            <div className="form-grid">
              <Field label={t("cond.deal")} hint={t("cond.deal_hint")}>
                <div className="segmented">
                  {(["buy", "rent"] as const).map((d) => (
                    <button key={d} type="button" className={form.conditions.deal_type === d ? "on" : ""}
                      onClick={() => {
                        if (form.conditions.deal_type === d) return;
                        // Types, prices and sites differ per deal type, so switching resets them.
                        update({
                          conditions: { ...form.conditions, deal_type: d, transaction_type: [], price_min: null, price_max: null },
                          sites: form.sites.filter((s) => sites.find((x) => x.id === s.site_id)?.capabilities.deals.includes(d)),
                        });
                      }}>{t(`deal.${d}`)}</button>
                  ))}
                </div>
              </Field>
              <Field label={t("cond.types")} hint={t("cond.types_hint")}>
                <div className="chips">
                  {regions.deals[form.conditions.deal_type].map((p) => (
                    <label key={p} className={`chip ${form.conditions.transaction_type.includes(p) ? "on" : ""}`}>
                      <input type="checkbox" checked={form.conditions.transaction_type.includes(p)} onChange={() => toggle("transaction_type", p)} />{t(`ptype.${p}`)}
                    </label>
                  ))}
                </div>
              </Field>
              <Field label={t("cond.prefectures")} required>
                <select value="" onChange={(e) => e.target.value && toggle("prefectures", e.target.value)}>
                  <option value="">{t("cond.add_prefecture")}</option>
                  {regions.prefectures.filter((p) => !form.conditions.prefectures.includes(p.code)).map((p) => <option key={p.code} value={p.code}>{p.name[lang]}</option>)}
                </select>
                <div className="chips">
                  {form.conditions.prefectures.map((code) => (
                    <button key={code} className="chip on" onClick={() => cond({ prefectures: form.conditions.prefectures.filter((x) => x !== code), cities: form.conditions.cities.filter((c) => !c.startsWith(code)) })}>
                      {regions.prefectures.find((p) => p.code === code)?.name[lang]} ×
                    </button>
                  ))}
                </div>
              </Field>
              {cityOptions.length > 0 && (
                <Field label={t("cond.cities")} hint={t("cond.cities_hint")}>
                  <div className="chips scroll-chips">
                    {cityOptions.map((c) => (
                      <label key={c.code} className={`chip ${form.conditions.cities.includes(c.code) ? "on" : ""}`}>
                        <input type="checkbox" checked={form.conditions.cities.includes(c.code)} onChange={() => toggle("cities", c.code)} />{c.name[lang]}
                      </label>
                    ))}
                  </div>
                </Field>
              )}
              <Field label={t("cond.region_logic")} hint={t("cond.region_logic_hint")}>
                <select value={form.conditions.region_logic} onChange={(e) => cond({ region_logic: e.target.value as "AND" | "OR" })}>
                  <option value="OR">OR</option><option value="AND">AND</option>
                </select>
              </Field>
              <Field label={t("cond.stations")} hint={t("cond.stations_hint")}>
                <input value={stationText} onChange={(e) => { setStationText(e.target.value); setDirty(true); cond({ stations: splitList(e.target.value).map((name) => ({ name })) }); }} placeholder="武蔵浦和, 浦和" />
              </Field>
              <div className="field-row">
                <Field label={`${t(isRent ? "cond.rent_min" : "cond.price_min")} (${t(isRent ? "unit.man_yen_month" : "unit.man_yen")})`}><input type="number" min={0} step={isRent ? 0.1 : 1} value={form.conditions.price_min ?? ""} onChange={(e) => cond({ price_min: numOrNull(e.target.value) })} /></Field>
                <Field label={`${t(isRent ? "cond.rent_max" : "cond.price_max")} (${t(isRent ? "unit.man_yen_month" : "unit.man_yen")})`}><input type="number" min={0} step={isRent ? 0.1 : 1} value={form.conditions.price_max ?? ""} onChange={(e) => cond({ price_max: numOrNull(e.target.value) })} /></Field>
                <label className="check"><input type="checkbox" checked={form.conditions.price_includes_fees} onChange={(e) => cond({ price_includes_fees: e.target.checked })} />{t(isRent ? "cond.include_mgmt_fee" : "cond.include_fees")}</label>
              </div>
              <div className="field-row">
                <Field label={`${t("cond.area_min")} (㎡)`}><input type="number" min={0} step={0.1} value={form.conditions.area_min ?? ""} onChange={(e) => cond({ area_min: numOrNull(e.target.value) })} /></Field>
                <Field label={`${t("cond.area_max")} (㎡)`}><input type="number" min={0} step={0.1} value={form.conditions.area_max ?? ""} onChange={(e) => cond({ area_max: numOrNull(e.target.value) })} /></Field>
              </div>
              <div className="field-row">
                <Field label={t("cond.age_max")}><input type="number" min={0} value={form.conditions.building_age_max ?? ""} onChange={(e) => cond({ building_age_max: numOrNull(e.target.value) })} /></Field>
                <Field label={t("cond.walk_max")}><input type="number" min={0} value={form.conditions.walk_minutes_max ?? ""} onChange={(e) => cond({ walk_minutes_max: numOrNull(e.target.value) })} /></Field>
              </div>
              <Field label={t("cond.layouts")}>
                <div className="chips">
                  {regions.layouts.map((l) => (
                    <label key={l} className={`chip ${form.conditions.layouts.includes(l) ? "on" : ""}`}>
                      <input type="checkbox" checked={form.conditions.layouts.includes(l)} onChange={() => toggle("layouts", l)} />{l}
                    </label>
                  ))}
                </div>
              </Field>
              <Field label={t("cond.kw_include")} hint={t("cond.kw_hint")}><input value={kwInc} onChange={(e) => { setKwInc(e.target.value); cond({ keywords_include: splitList(e.target.value) }); }} /></Field>
              <Field label={t("cond.kw_exclude")}><input value={kwExc} onChange={(e) => { setKwExc(e.target.value); cond({ keywords_exclude: splitList(e.target.value) }); }} /></Field>
              <div className="field-row">
                <Field label={t("cond.sort")}>
                  <select value={form.conditions.sort_order ?? ""} onChange={(e) => cond({ sort_order: e.target.value || null })}>
                    <option value="">{t("common.default")}</option>
                    {regions.sort_orders.map((s) => <option key={s} value={s}>{t(`sort.${s}`)}</option>)}
                  </select>
                </Field>
                <Field label={t("cond.limit")} hint={t("cond.limit_hint")}><input type="number" min={1} max={500} value={form.conditions.result_limit} onChange={(e) => cond({ result_limit: Number(e.target.value) || 100 })} /></Field>
              </div>
            </div>
          )}

          {step === 2 && (
            <div>
              <p className="muted">{t("wizard.sites_hint")}</p>
              <p className="muted small">{t("wizard.sites_for_deal", { deal: t(`deal.${form.conditions.deal_type}`) })}</p>
              {sites.filter((site) => site.capabilities.deals.includes(form.conditions.deal_type)).map((site) => {
                const selected = form.sites.find((s) => s.site_id === site.id);
                const accs = accountsFor(site.id);
                const auto = site.permissions.browser_automation?.status;
                return (
                  <div key={site.id} className={`site-pick ${selected ? "on" : ""}`}>
                    <label className="check">
                      <input type="checkbox" checked={!!selected} onChange={(e) => setSite(site.id, e.target.checked)} />
                      <b>{site.name}</b>
                    </label>
                    {site.capabilities.assisted ? <Badge tone="info">{t("wizard.site_assisted")}</Badge>
                      : !site.capabilities.automation_available ? <Badge tone="muted">{t("wizard.site_manual_only")}</Badge>
                      : !regions.deals[form.conditions.deal_type].some((ty) => site.capabilities.property_types.includes(ty))
                        ? <Badge tone="muted">{t("wizard.site_deal_manual_only", { deal: t(`deal.${form.conditions.deal_type}`) })}</Badge>
                        : auto !== "allowed" && <Badge tone="warn">{site.rules_check_available ? t("wizard.site_rules_on_run") : t("wizard.site_not_permitted")}</Badge>}
                    {site.capabilities.links[form.conditions.deal_type] && (
                      <a className="small" href={site.capabilities.links[form.conditions.deal_type]} target="_blank" rel="noopener noreferrer">{t("sites.open_search")}</a>
                    )}
                    {site.adapter_status === "stopped" && <Badge tone="bad">{t("site.adapter_stopped")}</Badge>}
                    {selected && site.capabilities.automation_available && (
                      <Field label={site.capabilities.login_required ? t("wizard.account") : t("wizard.account_optional")}
                        hint={site.capabilities.login_required ? (!selected.account_id ? t("wizard.account_required_hint") : undefined) : t("wizard.account_optional_hint")}>
                        {accs.length ? (
                          <select value={selected.account_id ?? ""} onChange={(e) => update({ sites: form.sites.map((s) => s.site_id === site.id ? { ...s, account_id: e.target.value ? Number(e.target.value) : null } : s) })}>
                            <option value="">{site.capabilities.login_required ? t("tasks.no_account") : t("wizard.no_account_anonymous")}</option>
                            {accs.map((a) => <option key={a.id} value={a.id}>{a.account_alias} ({t(`login.${a.login_status}`)})</option>)}
                          </select>
                        ) : <span className="muted">{site.capabilities.login_required ? t("wizard.no_accounts") : t("wizard.no_account_anonymous")}</span>}
                      </Field>
                    )}
                    {selected && validation?.[site.id] && (
                      <div className="support-table">
                        {[...Object.keys(validation[site.id].submit), ...validation[site.id].local_filters, ...validation[site.id].unsupported]
                          .filter((f, i, arr) => arr.indexOf(f) === i && f !== "result_limit")
                          .map((f) => <span key={f} className="support-item">{t(`field.${f}`)} {supportBadge(site.id, f)}</span>)}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}

          {step === 3 && (
            <div className="form-grid">
              <Field label={t("wizard.schedule_type")}>
                <div className="chips">
                  {(["manual", "daily", "weekly", "interval", "startup", "reminder"] as const).map((s) => (
                    <label key={s} className={`chip ${form.schedule_type === s ? "on" : ""}`}>
                      <input type="radio" name="st" checked={form.schedule_type === s} onChange={() => update({ schedule_type: s })} />{t(`schedule.${s}`)}
                    </label>
                  ))}
                </div>
              </Field>
              <p className="hint-box">{t(`schedule.help.${form.schedule_type}`)}</p>
              {(form.schedule_type === "daily" || form.schedule_type === "weekly" || form.schedule_type === "reminder") && (
                <Field label={t("schedule.time")}><input type="time" value={form.schedule.time ?? "08:00"} onChange={(e) => sched({ time: e.target.value })} /></Field>
              )}
              {(form.schedule_type === "weekly" || form.schedule_type === "reminder") && (
                <Field label={t("schedule.weekdays")}>
                  <div className="chips">
                    {[0, 1, 2, 3, 4, 5, 6].map((d) => {
                      const on = (form.schedule.weekdays ?? []).includes(d);
                      return (
                        <label key={d} className={`chip ${on ? "on" : ""}`}>
                          <input type="checkbox" checked={on} onChange={() => sched({ weekdays: on ? (form.schedule.weekdays ?? []).filter((x) => x !== d) : [...(form.schedule.weekdays ?? []), d].sort() })} />{t(`weekday.${d}`)}
                        </label>
                      );
                    })}
                  </div>
                </Field>
              )}
              {form.schedule_type === "interval" && (
                <Field label={t("schedule.interval_hours")}><input type="number" min={1} max={168} value={form.schedule.interval_hours ?? 6} onChange={(e) => sched({ interval_hours: Number(e.target.value) || 6 })} /></Field>
              )}
              {form.schedule_type === "startup" && (
                <Field label={t("schedule.min_interval")} hint={t("schedule.min_interval_hint")}><input type="number" min={0} value={form.schedule.min_interval_hours ?? 6} onChange={(e) => sched({ min_interval_hours: Number(e.target.value) })} /></Field>
              )}
              {form.schedule_type !== "manual" && form.schedule_type !== "startup" && (
                <>
                  <div className="field-row">
                    <Field label={t("schedule.start_date")}><input type="date" value={form.schedule.start_date ?? ""} onChange={(e) => sched({ start_date: e.target.value || null })} /></Field>
                    <Field label={t("schedule.end_date")} hint={t("schedule.end_date_hint")}><input type="date" value={form.schedule.end_date ?? ""} onChange={(e) => sched({ end_date: e.target.value || null })} /></Field>
                  </div>
                  <div className="field-row">
                    <Field label={t("schedule.window_start")}><input type="time" value={form.schedule.window_start ?? ""} onChange={(e) => sched({ window_start: e.target.value || null })} /></Field>
                    <Field label={t("schedule.window_end")} hint={t("schedule.window_hint")}><input type="time" value={form.schedule.window_end ?? ""} onChange={(e) => sched({ window_end: e.target.value || null })} /></Field>
                  </div>
                  <Field label={t("settings.timezone")}><input value={form.timezone} onChange={(e) => update({ timezone: e.target.value })} /></Field>
                </>
              )}
              <p className="muted">{t("schedule.missed_rule")}</p>
              {preview.length > 0 && (
                <div><b>{t("schedule.preview")}</b><ul>{preview.map((p) => <li key={p}>{formatDateTime(p, lang, form.timezone)}</li>)}</ul></div>
              )}
            </div>
          )}

          {step === 4 && (
            <div>
              <dl className="kv">
                <dt>{t("wizard.name")}</dt><dd>{form.name}</dd>
                <dt>{t("wizard.priority")}</dt><dd>{t(`priority.${form.priority}`)}</dd>
                <dt>{t("tasks.conditions")}</dt><dd><ConditionSummary conditions={form.conditions} /></dd>
                <dt>{t("tasks.col.sites")}</dt><dd>{form.sites.map((s) => `${siteName(s.site_id)} / ${(accounts.data ?? []).find((a) => a.id === s.account_id)?.account_alias ?? t("tasks.no_account")}`).join(", ")}</dd>
                <dt>{t("tasks.col.schedule")}</dt><dd>{scheduleText(t, form.schedule_type, form.schedule)}</dd>
                {preview[0] && <><dt>{t("tasks.col.next")}</dt><dd>{formatDateTime(preview[0], lang, form.timezone)}</dd></>}
              </dl>
              {validation && Object.entries(validation).map(([sid, v]) => (v.local_filters.length > 0 || v.unsupported.length > 0) && (
                <div key={sid} className={`hint-box ${v.unsupported.length ? "hint-warn" : ""}`}>
                  <b>{siteName(sid)}</b>
                  {v.local_filters.length > 0 && <p>{t("wizard.local_note", { fields: v.local_filters.map((f) => t(`field.${f}`)).join("、") })}</p>}
                  {v.unsupported.length > 0 && <p>{t("wizard.unsupported_note", { fields: v.unsupported.map((f) => t(`field.${f}`)).join("、") })}</p>}
                </div>
              ))}
              {unsupported.length > 0 && (
                <label className="check"><input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} />{t("wizard.ack_unsupported")}</label>
              )}
            </div>
          )}
          {error && <p className="form-error">{error}</p>}
        </section>
        <div className="wizard-nav">
          <button className="btn" onClick={() => nav("/tasks")}>{t("common.cancel")}</button>
          <div>
            {step > 0 && <button className="btn" onClick={() => { setError(null); setStep(step - 1); }}>{t("common.back")}</button>}
            {step < STEPS.length - 1
              ? <button className="btn btn-primary" onClick={next}>{t("common.next")}</button>
              : <button className="btn btn-primary" onClick={() => void save()} disabled={saving}>{t("common.save")}</button>}
          </div>
        </div>
      </StateView>
    </div>
  );
}
