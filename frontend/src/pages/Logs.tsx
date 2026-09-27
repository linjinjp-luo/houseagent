import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type { Account, Paged, Run, RunDetail, Task } from "../api/types";
import { Badge, Modal, PageHeader, Pager, StateView, useErrorText } from "../components/common";
import { useI18n } from "../i18n";
import { useApp } from "../lib/app";
import { formatDateTime } from "../lib/format";
import { useLoad } from "../lib/hooks";
import { ConditionSummary } from "./taskText";
import { ACTIVE, RunProgress } from "../components/RunProgress";

const LEVEL_TONE = { info: "muted", warning: "warn", failure: "bad", action_required: "accent" } as const;

export default function Logs() {
  const { t, lang } = useI18n();
  const { settings, sites, siteName } = useApp();
  const [params, setParams] = useSearchParams();
  const tz = settings.timezone;
  const page = Number(params.get("page") ?? "1");
  const keys = ["task_id", "site_id", "account_id", "status", "trigger_type", "level", "date_from", "date_to"];
  const filters = Object.fromEntries(keys.map((k) => [k, params.get(k) ?? ""]));
  const runId = params.get("run");
  const tasks = useLoad(() => api.get<Task[]>("/search-tasks", { include_deleted: true }), []);
  const accounts = useLoad(() => api.get<Account[]>("/accounts"), []);
  const runs = useLoad(() => api.get<Paged<Run>>("/search-runs", { ...filters, page, page_size: 50 }),
    [JSON.stringify(filters), page], 5000);

  const set = (k: string, v: string) => {
    const p = new URLSearchParams(params);
    if (v) p.set(k, v); else p.delete(k);
    p.delete("page");
    setParams(p);
  };

  return (
    <div className="page">
      <PageHeader title={t("menu.logs")} description={t("logs.desc")} />
      <div className="filter-bar">
        <select value={filters.task_id} onChange={(e) => set("task_id", e.target.value)} aria-label={t("menu.tasks")}>
          <option value="">{t("logs.all_tasks")}</option>{(tasks.data ?? []).map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}
        </select>
        <select value={filters.site_id} onChange={(e) => set("site_id", e.target.value)} aria-label={t("props.col.sites")}>
          <option value="">{t("props.all_sites")}</option>{sites.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
        </select>
        <select value={filters.account_id} onChange={(e) => set("account_id", e.target.value)} aria-label={t("sites.alias")}>
          <option value="">{t("logs.all_accounts")}</option>{(accounts.data ?? []).map((a) => <option key={a.id} value={a.id}>{a.account_alias}</option>)}
        </select>
        <select value={filters.status} onChange={(e) => set("status", e.target.value)} aria-label={t("props.col.status")}>
          <option value="">{t("logs.all_status")}</option>
          {["queued", "running", "completed", "paused", "failed", "cancelled", "interrupted"].map((s) => <option key={s} value={s}>{t(`run_status.${s}`)}</option>)}
          <option value="failed,paused,interrupted">{t("logs.problems")}</option>
        </select>
        <select value={filters.trigger_type} onChange={(e) => set("trigger_type", e.target.value)} aria-label={t("logs.trigger")}>
          <option value="">{t("logs.all_triggers")}</option>
          {["manual", "daily", "weekly", "interval", "startup", "catchup"].map((s) => <option key={s} value={s}>{t(`trigger.${s}`)}</option>)}
        </select>
        <select value={filters.level} onChange={(e) => set("level", e.target.value)} aria-label={t("logs.level")}>
          <option value="">{t("logs.all_levels")}</option>
          {["info", "warning", "failure", "action_required"].map((s) => <option key={s} value={s}>{t(`level.${s}`)}</option>)}
        </select>
        <input type="date" value={filters.date_from} onChange={(e) => set("date_from", e.target.value)} aria-label={t("stats.from")} />
        <input type="date" value={filters.date_to} onChange={(e) => set("date_to", e.target.value)} aria-label={t("stats.to")} />
      </div>
      <StateView loading={runs.loading} error={runs.error} onRetry={runs.reload} empty={!!runs.data && runs.data.total === 0} emptyText={t("logs.empty")}>
        {runs.data && runs.data.total > 0 && (
          <>
            <div className="table-wrap">
              <table className="table">
                <thead><tr>
                  <th>{t("logs.run_no")}</th><th>{t("tasks.col.name")}</th><th>{t("props.col.sites")}</th><th>{t("logs.trigger")}</th>
                  <th>{t("logs.started")}</th><th>{t("logs.finished")}</th><th>{t("logs.counts")}</th><th>{t("props.col.status")}</th>
                </tr></thead>
                <tbody>
                  {runs.data.items.map((r) => (
                    <tr key={r.id} className="clickable" onClick={() => set("run", String(r.id))}>
                      <td className="mono">{r.run_no}</td>
                      <td>{r.task_name}<span className="muted small"> v{r.condition_version}</span></td>
                      <td>{siteName(r.site_id)}<div className="muted small">{r.account_alias ?? "—"}</div></td>
                      <td>{t(`trigger.${r.trigger_type}`)}</td>
                      <td className="nowrap">{formatDateTime(r.started_at ?? r.queued_at, lang, tz)}</td>
                      <td className="nowrap">{formatDateTime(r.finished_at, lang, tz)}</td>
                      <td className="nowrap">{t("logs.counts_value", { n: r.result_count, new: r.new_count, changed: r.changed_count })}</td>
                      <td><RunProgress run={r} compact /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <Pager page={page} pageSize={50} total={runs.data.total} onPage={(p) => { const n = new URLSearchParams(params); n.set("page", String(p)); setParams(n); }} />
            <p className="muted small">{t("logs.no_secrets_note")}</p>
          </>
        )}
      </StateView>
      {runId && <RunDetailModal id={Number(runId)} onClose={() => set("run", "")} onChange={runs.reload} />}
    </div>
  );
}

function RunDetailModal({ id, onClose, onChange }: { id: number; onClose: () => void; onChange: () => void }) {
  const { t, lang } = useI18n();
  const { settings, siteName, toast } = useApp();
  const errText = useErrorText();
  const tz = settings.timezone;
  const [live, setLive] = useState(true);
  const d = useLoad(() => api.get<RunDetail>(`/search-runs/${id}`), [id], live ? 1500 : 0);
  useEffect(() => { if (d.data) setLive(ACTIVE.has(d.data.status)); }, [d.data]);
  const r = d.data;
  const act = async (path: string, okKey: string) => {
    try { await api.post(`/search-runs/${id}/${path}`); toast("success", t(okKey)); d.reload(); onChange(); } catch (e) { toast("error", errText(e)); }
  };
  return (
    <Modal wide title={r ? `${t("logs.detail")} ${r.run_no}` : t("logs.detail")} onClose={onClose}
      footer={r && <>
        {(r.status === "queued" || r.status === "running") && <button className="btn" onClick={() => void act("cancel", "tasks.cancel_requested")}>{t("tasks.cancel_run")}</button>}
        {["interrupted", "failed", "paused", "cancelled"].includes(r.status) && <button className="btn btn-primary" onClick={() => void act("rerun", "logs.rerun_queued")}>{t("logs.rerun")}</button>}
        <Link className="btn" to={`/properties?run_id=${r.id}`}>{t("logs.view_results")}</Link>
      </>}>
      <StateView loading={d.loading} error={d.error} onRetry={d.reload}>
        {r && (
          <>
            <section className="run-summary">
              <RunProgress run={r} />
              {r.cancel_requested && r.status === "running" && <Badge tone="warn">{t("logs.cancelling")}</Badge>}
            </section>
            <dl className="kv">
              {r.error_code && r.status !== "queued" && <><dt>{t("logs.error")}</dt><dd><b>{t(`error_short.${r.error_code}`)}</b> <span className="mono small">{r.error_code}</span>{r.error_detail && <span className="mono small muted"> · {r.error_detail}</span>}<p>{t(`error.${r.error_code}`)}</p>{r.correlation_id && <span className="muted small">{t("common.correlation_id")}: {r.correlation_id}</span>}</dd></>}
              <dt>{t("logs.trigger")}</dt><dd>{t(`trigger.${r.trigger_type}`)}</dd>
              <dt>{t("tasks.col.name")}</dt><dd>{r.task_name} · {t("logs.condition_version", { v: r.condition_version })}</dd>
              <dt>{t("tasks.conditions")}</dt><dd>{r.conditions ? <ConditionSummary conditions={r.conditions} /> : "—"}</dd>
              <dt>{t("props.col.sites")}</dt><dd>{siteName(r.site_id)} / {r.account_alias ?? "—"}</dd>
              <dt>{t("logs.queued")}</dt><dd>{formatDateTime(r.queued_at, lang, tz)}</dd>
              <dt>{t("logs.started")}</dt><dd>{formatDateTime(r.started_at, lang, tz)}</dd>
              <dt>{t("logs.finished")}</dt><dd>{formatDateTime(r.finished_at, lang, tz)}</dd>
              <dt>{t("logs.counts")}</dt><dd>{t("logs.counts_full", { n: r.result_count, new: r.new_count, changed: r.changed_count, skipped: r.skipped_count, nf: r.not_found_count, pages: r.pages })}</dd>
              <dt>{t("logs.retries")}</dt><dd>{r.retry_count} {r.not_before && r.status === "queued" && `(${t("logs.next_retry")} ${formatDateTime(r.not_before, lang, tz)})`}</dd>
              {r.cancel_reason && <><dt>{t("logs.cancel_reason")}</dt><dd>{t(`cancel_reason.${r.cancel_reason}`)}</dd></>}
              {r.unsupported_conditions.length > 0 && <><dt>{t("support.unsupported")}</dt><dd>{r.unsupported_conditions.map((f) => t(`field.${f}`)).join("、")}</dd></>}
            </dl>
            <h3>{t("logs.entries")}</h3>
            <ul className="log-entries">
              {r.logs.map((e, i) => (
                <li key={i}>
                  <span className="muted time">{formatDateTime(e.created_at, lang, tz)}</span>
                  <Badge tone={LEVEL_TONE[e.level as keyof typeof LEVEL_TONE] ?? "muted"}>{t(`level.${e.level}`)}</Badge>
                  <span>{t(e.message_key, Object.fromEntries(Object.entries(e.params).map(([k, v]) => [k, Array.isArray(v) ? v.map((x) => t(`field.${x}`)).join("、") : k === "code" ? t(`error_short.${v}`) : String(v)])))}</span>
                </li>
              ))}
            </ul>
          </>
        )}
      </StateView>
    </Modal>
  );
}
