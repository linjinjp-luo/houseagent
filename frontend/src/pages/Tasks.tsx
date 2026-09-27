import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { Deal, Paged, Run, Task } from "../api/types";
import { Badge, ConfirmDialog, PageHeader, StateView, useErrorText } from "../components/common";
import { ACTIVE, RunProgress, useFailureText } from "../components/RunProgress";
import { RulesCheckPanel } from "../components/RulesCheck";
import { AssistedImportDialog } from "../components/AssistedImport";
import { IconPlay } from "../components/Icons";
import { useI18n } from "../i18n";
import { useApp } from "../lib/app";
import { formatDateTime } from "../lib/format";
import { useLoad } from "../lib/hooks";
import { ConditionSummary, scheduleText } from "./taskText";

type Pending = { kind: "run" | "delete" | "cancel"; task: Task } | null;

export default function Tasks() {
  const { t, lang } = useI18n();
  const { settings, sites, regions, siteName, toast } = useApp();
  const nav = useNavigate();
  const errText = useErrorText();
  const failureText = useFailureText();
  const [fastPoll, setFastPoll] = useState(false);
  // Poll every 2 s while a run is queued or running, otherwise every 10 s.
  const { data, error, loading, reload } = useLoad(() => api.get<Task[]>("/search-tasks"), [], fastPoll ? 2000 : 10000);
  const seenActive = useRef<Map<number, string>>(new Map());

  // Notify when a run this page saw in progress reaches its result (success counts or failure reason).
  useEffect(() => {
    if (!data) return;
    const runs = data.flatMap((task) => task.last_runs.map((r) => ({ r, task })));
    setFastPoll(runs.some(({ r }) => ACTIVE.has(r.status)));
    for (const { r, task } of runs) {
      const before = seenActive.current.get(r.id);
      if (ACTIVE.has(r.status)) {
        seenActive.current.set(r.id, r.status);
      } else if (before) {
        seenActive.current.delete(r.id);
        if (r.status === "completed") {
          toast("success", t("tasks.run_done", { task: task.name, site: siteName(r.site_id), n: r.result_count, new: r.new_count, changed: r.changed_count }));
        } else {
          toast("error", t("tasks.run_failed", { task: task.name, site: siteName(r.site_id), status: t(`run_status.${r.status}`), reason: failureText(r) }));
        }
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data]);
  const [pending, setPending] = useState<Pending>(null);
  const [assistedFor, setAssistedFor] = useState<{ task: Task; siteId: string } | null>(null);
  const assistedSite = (task: Task) => task.sites.find((ts) => sites.find((x) => x.id === ts.site_id)?.capabilities.assisted)?.site_id;
  const tz = settings.timezone;

  const act = async (fn: () => Promise<unknown>, okKey: string) => {
    try {
      await fn();
      toast("success", t(okKey));
      reload();
    } catch (e) {
      toast("error", errText(e));
    }
  };

  const needsRulesCheck = (siteId: string, deal: Deal = "buy") => {
    const site = sites.find((x) => x.id === siteId);
    if (!site || !site.capabilities.automation_available) return false;
    if (!regions.deals[deal].some((ty) => site.capabilities.property_types.includes(ty))) return false;
    return site.rules_check_available && site.permissions.browser_automation?.status !== "allowed";
  };

  const statusTone = (task: Task) => (task.status === "active" ? "ok" : task.status === "paused" ? "warn" : "muted");

  return (
    <div className="page">
      <PageHeader title={t("menu.tasks")} description={t("tasks.desc")}
        actions={<Link className="btn btn-primary" to="/tasks/new">+ {t("tasks.new")}</Link>} />
      <StateView loading={loading} error={error} onRetry={reload} empty={!!data && data.length === 0}
        emptyText={<><p>{t("tasks.empty")}</p><Link className="btn btn-primary" to="/tasks/new">{t("tasks.new")}</Link></>}>
        {data && data.length > 0 && (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>{t("tasks.col.name")}</th><th>{t("tasks.col.types")}</th><th>{t("tasks.col.region")}</th>
                  <th>{t("tasks.col.sites")}</th><th>{t("tasks.col.schedule")}</th><th>{t("tasks.col.next")}</th>
                  <th>{t("tasks.col.last")}</th><th>{t("tasks.col.status")}</th><th />
                </tr>
              </thead>
              <tbody>
                {data.map((task) => (
                  <tr key={task.id}>
                    <td>
                      <b>{task.name}</b>
                      {task.priority !== "normal" && <Badge tone={task.priority === "high" ? "accent" : "muted"}>{t(`priority.${task.priority}`)}</Badge>}
                      <div className="muted small">v{task.condition_version}{task.description ? ` · ${task.description}` : ""}</div>
                    </td>
                    <td><span className={`deal-tag deal-${task.conditions.deal_type ?? "buy"}`}>{t(`deal.${task.conditions.deal_type ?? "buy"}`)}</span><div>{task.conditions.transaction_type.length ? task.conditions.transaction_type.map((x) => t(`ptype.${x}`)).join(" / ") : t("common.any")}</div></td>
                    <td><ConditionSummary conditions={task.conditions} short /></td>
                    <td>{task.sites.map((s) => <div key={s.site_id}>{siteName(s.site_id)} <span className="muted small">{s.account_alias ?? (sites.find((x) => x.id === s.site_id)?.capabilities.login_required === false ? t("tasks.no_login_needed") : t("tasks.no_account"))}</span></div>)}</td>
                    <td>{scheduleText(t, task.schedule_type, task.schedule)}</td>
                    <td className="nowrap">{formatDateTime(task.next_run_at, lang, tz)}</td>
                    <td className="result-cell">
                      {task.last_runs.length ? task.last_runs.map((r) => (
                        <Link key={r.id} to={`/logs?run=${r.id}`} className="result-link" title={t("tasks.open_run_detail")}>
                          <RunProgress run={r} compact siteLabel={task.sites.length > 1 ? siteName(r.site_id) : undefined} />
                        </Link>
                      )) : <span className="muted">{t("tasks.never_run")}</span>}
                    </td>
                    <td>
                      <Badge tone={statusTone(task)}>{t(`task_status.${task.status}`)}</Badge>
                      {task.paused_reason && <div className="muted small">{t(`paused_reason.${task.paused_reason}`)}</div>}
                    </td>
                    <td className="row-actions">
                      <button className="btn btn-sm btn-primary" disabled={task.status !== "active"} onClick={() => setPending({ kind: "run", task })}><IconPlay /> {t("tasks.run_now")}</button>
                      {assistedSite(task) && <button className="btn btn-sm" disabled={task.status !== "active"} onClick={() => setAssistedFor({ task, siteId: assistedSite(task)! })}>{t("assisted.button")}</button>}
                      <button className="btn btn-sm" onClick={() => nav(`/tasks/${task.id}/edit`)}>{t("common.edit")}</button>
                      <button className="btn btn-sm" onClick={() => void act(() => api.post(`/search-tasks/${task.id}/copy`), "tasks.copied")}>{t("tasks.copy")}</button>
                      {task.status === "paused"
                        ? <button className="btn btn-sm" onClick={() => void act(() => api.post(`/search-tasks/${task.id}/resume`), "tasks.resumed")}>{t("tasks.resume")}</button>
                        : <button className="btn btn-sm" onClick={() => void act(() => api.post(`/search-tasks/${task.id}/pause`), "tasks.paused")}>{t("tasks.pause")}</button>}
                      {task.active_runs > 0 && <button className="btn btn-sm" onClick={() => setPending({ kind: "cancel", task })}>{t("tasks.cancel_run")}</button>}
                      <button className="btn btn-sm btn-ghost-danger" onClick={() => setPending({ kind: "delete", task })}>{t("common.delete")}</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </StateView>

      {pending?.kind === "run" && (
        <ConfirmDialog wide title={t("tasks.run_confirm_title")} confirmLabel={t("tasks.run_now")} onClose={() => setPending(null)}
          confirmDisabled={pending.task.sites.some((ts) => needsRulesCheck(ts.site_id, pending.task.conditions.deal_type))
            || pending.task.sites.every((ts) => sites.find((x) => x.id === ts.site_id)?.capabilities.assisted)}
          message={
            <div>
              <p>{t("tasks.run_confirm_body")}</p>
              <dl className="kv">
                <dt>{t("tasks.col.name")}</dt><dd>{pending.task.name} (v{pending.task.condition_version})</dd>
                <dt>{t("tasks.col.sites")}</dt><dd>{pending.task.sites.map((s) => `${siteName(s.site_id)} / ${s.account_alias ?? (sites.find((x) => x.id === s.site_id)?.capabilities.login_required === false ? t("tasks.no_login_needed") : t("tasks.no_account"))}`).join(", ")}</dd>
                <dt>{t("tasks.conditions")}</dt><dd><ConditionSummary conditions={pending.task.conditions} /></dd>
              </dl>
              {pending.task.sites.map((ts) => {
                const site = sites.find((x) => x.id === ts.site_id);
                if (site && needsRulesCheck(site.id, pending.task.conditions.deal_type)) {
                  // The site's automated access is not confirmed yet: look up its rules and let the user decide.
                  return (
                    <div key={ts.site_id} className="hint-box hint-warn">
                      <p><b>{siteName(ts.site_id)}</b>: {t("rules.run_intro")}</p>
                      <RulesCheckPanel siteId={site.id} autoRun acceptLabel={t("rules.accept_and_continue")} onAccepted={() => undefined} />
                    </div>
                  );
                }
                const deal = pending.task.conditions.deal_type ?? "buy";
                if (site?.capabilities.assisted) {
                  return (
                    <div key={ts.site_id} className="hint-box hint-warn">
                      <p><b>{siteName(ts.site_id)}</b>: {t("error_detail.assisted_only")}</p>
                      <button className="btn btn-sm btn-primary" onClick={() => { const task = pending.task; setPending(null); setAssistedFor({ task, siteId: ts.site_id }); }}>{t("assisted.button")}</button>
                    </div>
                  );
                }
                const reason = !site ? null
                  : !site.capabilities.automation_available ? "adapter_not_implemented"
                  : !regions.deals[deal].some((ty) => site.capabilities.property_types.includes(ty)) ? "deal_not_automated"
                  : site.permissions.browser_automation?.status !== "allowed" ? "browser_automation"
                  : !ts.account_id && site.capabilities.login_required ? "no_account" : null;
                const acceptedByRules = (site?.permissions.browser_automation?.source ?? "").startsWith("rules_check:");
                return reason ? (
                  <p key={ts.site_id} className="hint-box hint-warn">
                    <b>{siteName(ts.site_id)}</b>: {t(`error_detail.${reason}`)}
                  </p>
                ) : acceptedByRules ? (
                  <p key={ts.site_id} className="muted small">
                    {siteName(ts.site_id)}: {t("rules.previously_accepted", { s: site?.capabilities.min_page_interval_s ?? 0 })}
                  </p>
                ) : null;
              })}
              {pending.task.sites.some((ts) => ts.account_id) && <p className="muted small">{t("tasks.login_window_autoclose")}</p>}
              <p className="muted small">{t("tasks.run_confirm_note")}</p>
            </div>
          }
          onConfirm={async () => {
            const res = await api.post<{ queued: Run[]; closed_login_windows: string[]; skipped: Record<string, string> }>(`/search-tasks/${pending.task.id}/run`);
            const skippedSites = Object.entries(res.skipped ?? {});
            if (skippedSites.length && res.queued.length) {
              toast("info", t("tasks.sites_skipped", { sites: skippedSites.map(([sid, reason]) => `${siteName(sid)}（${t(`skip_reason.${reason}`)}）`).join("、") }));
            }
            if (res.closed_login_windows.length) toast("info", t("tasks.login_window_closed", { aliases: res.closed_login_windows.join(", ") }));
            for (const r of res.queued) seenActive.current.set(r.id, r.status);
            toast("info", res.queued.length ? t("tasks.queued", { n: res.queued.length }) : t("tasks.already_queued"));
            setFastPoll(true);
            reload();
          }} />
      )}
      {assistedFor && <AssistedImportDialog task={assistedFor.task} siteId={assistedFor.siteId} onClose={() => { setAssistedFor(null); reload(); }} />}
      {pending?.kind === "delete" && (
        <ConfirmDialog danger title={t("tasks.delete_title")} confirmLabel={t("common.delete")} onClose={() => setPending(null)}
          message={t("tasks.delete_body", { name: pending.task.name })}
          onConfirm={async () => { await api.del(`/search-tasks/${pending.task.id}`); toast("success", t("tasks.deleted")); reload(); }} />
      )}
      {pending?.kind === "cancel" && (
        <ConfirmDialog title={t("tasks.cancel_title")} confirmLabel={t("tasks.cancel_run")} onClose={() => setPending(null)}
          message={t("tasks.cancel_body")}
          onConfirm={async () => {
            const runs = await api.get<Paged<Run>>("/search-runs", { task_id: pending.task.id, status: "queued,running" });
            for (const r of runs.items) await api.post(`/search-runs/${r.id}/cancel`);
            toast("info", t("tasks.cancel_requested"));
            reload();
          }} />
      )}
    </div>
  );
}
