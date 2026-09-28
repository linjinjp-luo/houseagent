import { useState } from "react";
import { api } from "../api/client";
import type { AILimits, AIProvider, AIProvidersInfo, AITestResult, AIUsage, ProviderType } from "../api/types";
import { useI18n } from "../i18n";
import { useApp } from "../lib/app";
import { formatDateTime, formatNumber } from "../lib/format";
import { useLoad } from "../lib/hooks";
import { Badge, ConfirmDialog, Field, Modal, StateView, useErrorText } from "./common";

const TYPES: ProviderType[] = ["openai", "anthropic", "compatible"];
const LIMIT_KEYS: (keyof AILimits)[] = ["timeout_s", "max_retries", "daily_call_limit", "daily_cost_limit", "batch_max", "concurrency"];

/**
 * FR-12: the AI services the user brings (BYOK). The key is write-only here: the backend stores it in the OS
 * protected store and only ever reports whether one is set and its last four characters.
 */
export function AIServicesPanel() {
  const { t, lang } = useI18n();
  const { settings, toast } = useApp();
  const errText = useErrorText();
  const info = useLoad(() => api.get<AIProvidersInfo>("/ai/providers"), []);
  const usage = useLoad(() => api.get<AIUsage>("/ai/usage"), []);
  const [editing, setEditing] = useState<AIProvider | "new" | null>(null);
  const [testing, setTesting] = useState<number | null>(null);
  const [removing, setRemoving] = useState<AIProvider | null>(null);
  const [lastTest, setLastTest] = useState<Record<number, AITestResult>>({});

  const reload = () => { info.reload(); usage.reload(); };
  const test = async (p: AIProvider) => {
    setTesting(p.id);
    try {
      const r = await api.post<AITestResult>(`/ai/providers/${p.id}/test`);
      setLastTest({ ...lastTest, [p.id]: r });
      toast(r.ok ? "success" : "error", r.ok ? t("ai.test_ok", { ms: r.duration_ms ?? 0 }) : t(r.message_key ?? "error.ai_unavailable"));
      reload();
    } catch (e) { toast("error", errText(e)); } finally { setTesting(null); }
  };
  const act = async (fn: () => Promise<unknown>, okKey: string) => {
    try { await fn(); toast("success", t(okKey)); reload(); } catch (e) { toast("error", errText(e)); }
  };

  return (
    <StateView loading={info.loading} error={info.error} onRetry={info.reload}>
      {info.data && (
        <div className="ai-services">
          <p className="hint-box">{t("ai.byok_principle")}</p>
          <p className="small">
            {t("ai.secret_store")}: <b>{t(`ai.store.${info.data.secret_store}`)}</b>{" "}
            {!info.data.secret_store_available && <Badge tone="warn">{t("ai.store_unavailable")}</Badge>}
          </p>
          {!settings.ai_enabled && <p className="muted small">{t("ai.disabled_note")}</p>}
          {info.data.items.length === 0 && <p className="muted">{t("ai.no_providers")}</p>}
          <div className="provider-list">
            {info.data.items.map((p) => {
              const tr = lastTest[p.id];
              return (
                <section key={p.id} className={`card provider-card ${p.active ? "provider-active" : ""}`}>
                  <div className="card-title-row">
                    <h3 className="card-title">{p.display_name} <Badge tone="muted">{t(`ai.type.${p.provider_type}`)}</Badge>
                      {p.active && <Badge tone="info">{t("ai.active")}</Badge>}{!p.enabled && <Badge tone="muted">{t("ai.disabled")}</Badge>}</h3>
                  </div>
                  <dl className="kv small">
                    <dt>{t("ai.model")}</dt><dd className="mono">{p.model_id}</dd>
                    <dt>{t("ai.endpoint")}</dt><dd className="mono ellipsis" title={p.base_url ?? ""}>{p.base_url ?? t("ai.official_endpoint")}</dd>
                    <dt>{t("ai.key")}</dt>
                    <dd>{p.key_state === "configured" ? <Badge tone="ok">{t("ai.key_configured", { last4: p.key_last4 ?? "" })}</Badge>
                      : p.key_state === "env" ? <Badge tone="info">{t("ai.key_env")}</Badge>
                      : <Badge tone="warn">{t("ai.key_missing")}</Badge>}</dd>
                    <dt>{t("ai.last_test")}</dt>
                    <dd>{p.last_test_at ? <>{formatDateTime(p.last_test_at, lang, settings.timezone)} · {p.last_test_status === "ok"
                      ? <Badge tone="ok">{t("ai.test_passed")}</Badge> : <Badge tone="bad">{t(`error.${p.last_test_status}`)}</Badge>}</> : "—"}</dd>
                    <dt>{t("ai.limits")}</dt>
                    <dd>{t("ai.limits_summary", { calls: p.limits.daily_call_limit ?? "∞", batch: p.limits.batch_max, conc: p.limits.concurrency })}</dd>
                  </dl>
                  {tr?.ok && tr.checks && (
                    <p className="small">{Object.entries(tr.checks).map(([k, v]) => <Badge key={k} tone={v === "ok" ? "ok" : "bad"}>{t(`ai.check.${k}`)}</Badge>)}
                      {" "}{tr.model_version && <span className="muted mono">{tr.model_version}</span>}</p>
                  )}
                  <div className="row-actions wrap">
                    <button className="btn btn-sm btn-primary" disabled={testing === p.id} onClick={() => void test(p)}>
                      {testing === p.id ? <><span className="spinner" /> {t("ai.testing")}</> : t("ai.test")}</button>
                    {!p.active && <button className="btn btn-sm" onClick={() => void act(() => api.post(`/ai/providers/${p.id}/activate`), "ai.activated")}>{t("ai.activate")}</button>}
                    <button className="btn btn-sm" onClick={() => setEditing(p)}>{t("common.edit")}</button>
                    {p.key_state === "configured" && (
                      <button className="btn btn-sm" onClick={() => void act(() => api.del(`/ai/providers/${p.id}/secret`), "ai.key_deleted")}>{t("ai.delete_key")}</button>
                    )}
                    <button className="btn btn-sm btn-ghost-danger" onClick={() => setRemoving(p)}>{t("common.delete")}</button>
                  </div>
                </section>
              );
            })}
          </div>
          <button className="btn" onClick={() => setEditing("new")}>+ {t("ai.add_provider")}</button>
          <UsagePanel usage={usage.data} providers={info.data.items} />
          {editing && <ProviderEditor info={info.data} provider={editing === "new" ? null : editing} onClose={() => setEditing(null)} onSaved={reload} />}
          {removing && (
            <ConfirmDialog danger title={t("ai.remove_title")} message={t("ai.remove_body", { name: removing.display_name })} confirmLabel={t("common.delete")}
              onClose={() => setRemoving(null)} onConfirm={async () => { await api.del(`/ai/providers/${removing.id}`); reload(); }} />
          )}
        </div>
      )}
    </StateView>
  );
}

function ProviderEditor({ info, provider, onClose, onSaved }: { info: AIProvidersInfo; provider: AIProvider | null; onClose: () => void; onSaved: () => void }) {
  const { t } = useI18n();
  const { toast } = useApp();
  const errText = useErrorText();
  const [ptype, setPtype] = useState<ProviderType>(provider?.provider_type ?? "anthropic");
  const recommended = info.recommended[ptype] ?? [];
  const [name, setName] = useState(provider?.display_name ?? "");
  const [model, setModel] = useState(provider?.model_id ?? recommended.find((m) => m.recommended)?.id ?? "");
  const [advanced, setAdvanced] = useState(!!provider?.custom_url || ptype === "compatible");
  const [baseUrl, setBaseUrl] = useState(provider?.base_url ?? "");
  const [apiKey, setApiKey] = useState("");
  const [limits, setLimits] = useState<Record<string, string>>(
    Object.fromEntries(LIMIT_KEYS.map((k) => [k, String((provider?.limits ?? info.default_limits)[k] ?? "")])));
  const [fields, setFields] = useState<string[]>(provider?.allowed_fields ?? info.sendable_fields.filter((f) => f !== "address" && f !== "title"));
  const initialPricing = provider?.pricing ?? recommended.find((m) => m.recommended)?.pricing ?? {};
  const [pricing, setPricing] = useState({
    input_per_mtok: String(initialPricing.input_per_mtok ?? ""), output_per_mtok: String(initialPricing.output_per_mtok ?? ""),
    currency: initialPricing.currency ?? "USD", updated_at: initialPricing.updated_at ?? "",
  });
  const [confirmModel, setConfirmModel] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const pickType = (v: ProviderType) => {
    setPtype(v);
    const rec = info.recommended[v]?.find((m) => m.recommended) ?? info.recommended[v]?.[0];
    setModel(rec?.id ?? "");
    const p = rec?.pricing ?? {};
    setPricing({ input_per_mtok: String(p.input_per_mtok ?? ""), output_per_mtok: String(p.output_per_mtok ?? ""), currency: p.currency ?? "USD", updated_at: p.updated_at ?? "" });
    if (v === "compatible") setAdvanced(true);
  };
  const pickModel = (id: string) => {
    setModel(id);
    const rec = recommended.find((m) => m.id === id);
    if (rec?.pricing.input_per_mtok != null) {
      setPricing({ input_per_mtok: String(rec.pricing.input_per_mtok), output_per_mtok: String(rec.pricing.output_per_mtok ?? ""), currency: rec.pricing.currency ?? "USD", updated_at: rec.pricing.updated_at ?? "" });
    }
  };

  const save = async (confirmed = false) => {
    if (provider && model !== provider.model_id && !confirmed) { setConfirmModel(true); return; }
    if (ptype === "compatible" && !baseUrl.trim()) { setErr(t("error.ai_url_required")); return; }
    setBusy(true);
    try {
      const body: Record<string, unknown> = {
        display_name: name, model_id: model, base_url: advanced ? baseUrl.trim() || null : null,
        limits: Object.fromEntries(Object.entries(limits).map(([k, v]) => [k, v === "" ? null : Number(v)])),
        allowed_fields: fields,
        pricing: { ...pricing, input_per_mtok: pricing.input_per_mtok === "" ? null : Number(pricing.input_per_mtok),
          output_per_mtok: pricing.output_per_mtok === "" ? null : Number(pricing.output_per_mtok) },
      };
      if (provider) body.id = provider.id; else body.provider_type = ptype;
      if (apiKey.trim()) body.api_key = apiKey.trim();
      const saved = await api.post<AIProvider>("/ai/providers", body);
      setApiKey("");
      toast("success", t("settings.saved"));
      if (provider && provider.key_state === "configured" && saved.key_state === "missing") toast("info", t("ai.key_dropped_host_changed"));
      onSaved();
      onClose();
    } catch (e) { setErr(errText(e)); } finally { setBusy(false); }
  };

  return (
    <Modal wide title={t(provider ? "ai.edit_provider" : "ai.add_provider")} onClose={onClose}
      footer={<><button className="btn" onClick={onClose}>{t("common.cancel")}</button>
        <button className="btn btn-primary" disabled={busy} onClick={() => void save()}>{t("common.save")}</button></>}>
      <div className="form-grid two">
        <Field label={t("ai.provider")} required>
          <select value={ptype} disabled={!!provider} onChange={(e) => pickType(e.target.value as ProviderType)}>
            {TYPES.map((x) => <option key={x} value={x}>{t(`ai.type.${x}`)}</option>)}
          </select>
        </Field>
        <Field label={t("ai.display_name")}><input value={name} onChange={(e) => setName(e.target.value)} placeholder={t(`ai.type.${ptype}`)} /></Field>
        <Field label={t("ai.model")} required hint={t("ai.model_hint")}>
          <input list={`models-${ptype}`} value={model} onChange={(e) => pickModel(e.target.value)} />
          <datalist id={`models-${ptype}`}>{recommended.map((m) => <option key={m.id} value={m.id}>{m.recommended ? t("ai.recommended") : ""}</option>)}</datalist>
        </Field>
        <Field label={t("ai.key")} hint={provider?.key_state === "configured" ? t("ai.key_keep_hint", { last4: provider.key_last4 ?? "" }) : t("ai.key_hint")}>
          <input type="password" autoComplete="new-password" value={apiKey} onChange={(e) => setApiKey(e.target.value)}
            placeholder={provider?.key_state === "configured" ? `•••• ${provider.key_last4 ?? ""}` : ""} />
        </Field>
      </div>
      <details open={advanced} onToggle={(e) => setAdvanced((e.target as HTMLDetailsElement).open)}>
        <summary>{t("ai.advanced")}</summary>
        <Field label={t("ai.base_url")} required={ptype === "compatible"} hint={t("ai.base_url_hint")}>
          <input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} placeholder={ptype === "compatible" ? "https://gateway.example.com/v1" : t("ai.official_endpoint")} />
        </Field>
        {info.allowed_hosts.length > 0 && <p className="muted small">{t("ai.allowed_hosts", { hosts: info.allowed_hosts.join(", ") })}</p>}
      </details>
      <h4>{t("ai.limits")}</h4>
      <div className="form-grid three">
        {LIMIT_KEYS.map((k) => (
          <Field key={k} label={t(`ai.limit.${k}`)}>
            <input type="number" min={0} step={k === "daily_cost_limit" ? "0.01" : "1"} value={limits[k]} onChange={(e) => setLimits({ ...limits, [k]: e.target.value })} />
          </Field>
        ))}
      </div>
      <h4>{t("ai.fields")}</h4>
      <p className="muted small">{t("ai.fields_hint")}</p>
      <div className="chips">
        {info.sendable_fields.map((f) => {
          const on = fields.includes(f);
          return <label key={f} className={`chip ${on ? "on" : ""}`}><input type="checkbox" checked={on} onChange={() => setFields(on ? fields.filter((x) => x !== f) : [...fields, f])} />{t(`ai_field.${f}`)}</label>;
        })}
      </div>
      <h4>{t("ai.pricing")}</h4>
      <div className="form-grid four">
        <Field label={t("ai.price_in")}><input type="number" min={0} step="0.01" value={pricing.input_per_mtok} onChange={(e) => setPricing({ ...pricing, input_per_mtok: e.target.value })} /></Field>
        <Field label={t("ai.price_out")}><input type="number" min={0} step="0.01" value={pricing.output_per_mtok} onChange={(e) => setPricing({ ...pricing, output_per_mtok: e.target.value })} /></Field>
        <Field label={t("ai.currency")}><input value={pricing.currency} onChange={(e) => setPricing({ ...pricing, currency: e.target.value })} /></Field>
        <Field label={t("ai.price_updated")}><input value={pricing.updated_at} placeholder="2026-09" onChange={(e) => setPricing({ ...pricing, updated_at: e.target.value })} /></Field>
      </div>
      <p className="muted small">{t("ai.pricing_hint")}</p>
      {err && <p className="form-error">{err}</p>}
      {confirmModel && provider && (
        <ConfirmDialog title={t("ai.confirm_model_title")} message={t("ai.confirm_model_body", { from: provider.model_id, to: model })}
          onClose={() => setConfirmModel(false)} onConfirm={async () => { setConfirmModel(false); await save(true); }} />
      )}
    </Modal>
  );
}

function UsagePanel({ usage, providers }: { usage: AIUsage | null; providers: AIProvider[] }) {
  const { t, lang } = useI18n();
  const { settings } = useApp();
  if (!usage) return null;
  const name = (id: number | null) => providers.find((p) => p.id === id)?.display_name ?? (id ? `#${id}` : "—");
  const cost = (v: number | null, cur: string | null) => (v == null ? t("ai.cost_unknown") : `${cur ?? ""} ${v.toFixed(4)}`);
  return (
    <details className="usage-panel">
      <summary>{t("ai.usage_title", { days: usage.days })}</summary>
      <p className="muted small">{t("ai.usage_note")}</p>
      <div className="small">
        {providers.map((p) => {
          const d = usage.today[String(p.id)];
          return d ? <p key={p.id}>{p.display_name}: {t("ai.today", { calls: d.calls, limit: d.limits.daily_call_limit ?? "∞" })}</p> : null;
        })}
      </div>
      {usage.groups.length > 0 && (
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th>{t("ai.provider")}</th><th>{t("ai.model")}</th><th>{t("ai.purpose")}</th><th>{t("ai.status")}</th><th className="num">{t("ai.calls")}</th><th className="num">{t("ai.units")}</th><th className="num">{t("ai.est_cost")}</th></tr></thead>
            <tbody>
              {usage.groups.map((g, i) => (
                <tr key={i}>
                  <td>{name(g.provider_config_id)}</td><td className="mono small">{g.model_id}</td><td>{t(`ai.purpose.${g.purpose}`)}</td>
                  <td>{g.status === "ok" ? <Badge tone="ok">OK</Badge> : <Badge tone="bad">{t(`error.${g.status}`)}</Badge>}</td>
                  <td className="num">{formatNumber(g.calls, lang)}</td>
                  <td className="num">{formatNumber(g.input_units, lang)} / {formatNumber(g.output_units, lang)}</td>
                  <td className="num">{cost(g.estimated_cost, g.currency)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {usage.recent.length > 0 && (
        <p className="muted small">{t("ai.last_call", { at: formatDateTime(usage.recent[0].requested_at, lang, settings.timezone), id: usage.recent[0].correlation_id })}</p>
      )}
    </details>
  );
}
