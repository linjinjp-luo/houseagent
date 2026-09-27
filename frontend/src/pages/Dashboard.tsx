import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { Dashboard as DashboardData, Lang } from "../api/types";
import { EventBadge, Modal, PageHeader, RunStatusBadge, Stat, StateView } from "../components/common";
import { RunProgress } from "../components/RunProgress";
import { IconAlert } from "../components/Icons";
import { LANGS, useI18n } from "../i18n";
import { useApp } from "../lib/app";
import { formatDateTime, formatPrice, formatPriceDiff, todayIn } from "../lib/format";
import { useLoad } from "../lib/hooks";

const LANG_LABEL: Record<Lang, string> = { ja: "日本語", zh: "简体中文", en: "English" };

export default function Dashboard() {
  const { t, lang, setLang } = useI18n();
  const { settings, saveSettings, siteName } = useApp();
  const nav = useNavigate();
  const tz = settings.timezone;
  const [fast, setFast] = useState(false);
  const { data, error, loading, reload } = useLoad(() => api.get<DashboardData>("/dashboard"), [], fast ? 2000 : 15000);
  useEffect(() => setFast(!!data && data.queue.length > 0), [data]);
  const today = todayIn(tz);

  const resolve = async (id: number) => {
    await api.post(`/notifications/${id}/resolve`);
    reload();
  };

  const attentionLink = (kind: string, taskId: number | null, runId: number | null): string => {
    if (kind === "login_required" || kind === "permission_required" || kind === "page_changed") return "/sites";
    if (runId) return `/logs?run=${runId}`;
    if (taskId) return "/tasks";
    return "/logs";
  };

  return (
    <div className="page">
      <PageHeader title={t("menu.dashboard")} description={t("dash.desc")} />
      {!settings.onboarding_done && (
        <Modal title={t("welcome.title")} onClose={() => void saveSettings({ onboarding_done: true })}
          footer={<>
            <button className="btn" onClick={() => void saveSettings({ onboarding_done: true })}>{t("welcome.later")}</button>
            <button className="btn btn-primary" onClick={() => { void saveSettings({ onboarding_done: true }); nav("/sites"); }}>{t("welcome.start")}</button>
          </>}>
          <p>{t("welcome.body")}</p>
          <div className="lang-choice">
            {LANGS.map((l) => (
              <button key={l} className={`btn ${l === lang ? "btn-primary" : ""}`} onClick={() => setLang(l)}>{LANG_LABEL[l]}</button>
            ))}
          </div>
          <ol className="steps-list">
            <li>{t("welcome.step1")}</li>
            <li>{t("welcome.step2")}</li>
            <li>{t("welcome.step3")}</li>
          </ol>
          <p className="muted">{t("welcome.principles")}</p>
        </Modal>
      )}
      <StateView loading={loading} error={error} onRetry={reload}>
        {data && (
          <>
            <div className="meta-row">
              <span>{t("dash.last_success")}: <b>{formatDateTime(data.last_success_at, lang, tz)}</b></span>
              <span>{t("dash.next_run")}: <b>{formatDateTime(data.next_run_at, lang, tz)}</b></span>
            </div>
            {data.task_count === 0 && (
              <div className="state-panel state-empty">
                <p>{t("dash.first_use")}</p>
                <Link className="btn btn-primary" to="/tasks/new">{t("tasks.new")}</Link>
              </div>
            )}
            <div className="stat-grid">
              <Stat label={t("dash.card.new")} value={data.cards.new} tone="accent" onClick={() => nav(`/properties?event=NEW&event_date=${today}`)} />
              <Stat label={t("dash.card.price_down")} value={data.cards.price_down} tone="ok" onClick={() => nav(`/properties?event=PRICE_DOWN&event_date=${today}`)} />
              <Stat label={t("dash.card.price_up")} value={data.cards.price_up} tone="bad" onClick={() => nav(`/properties?event=PRICE_UP&event_date=${today}`)} />
              <Stat label={t("dash.card.reappeared")} value={data.cards.reappeared} onClick={() => nav(`/properties?event=REAPPEARED&event_date=${today}`)} />
              <Stat label={t("dash.card.pending_matches")} value={data.cards.pending_matches} tone="warn" onClick={() => nav("/properties?tab=matches")} />
              <Stat label={t("dash.card.failed")} value={data.cards.failed} tone={data.cards.failed ? "bad" : undefined} onClick={() => nav("/logs?status=failed,paused,interrupted")} />
            </div>

            <div className="grid-2">
              <section className="card">
                <h2 className="card-title"><IconAlert /> {t("dash.needs_attention")}</h2>
                {data.attention.length === 0 ? <p className="muted">{t("dash.nothing_to_do")}</p> : (
                  <ul className="attention-list">
                    {data.attention.map((n) => (
                      <li key={n.id}>
                        <div>
                          <b>{t(`notice_kind.${n.kind}`)}</b>
                          <span className="muted"> · {formatDateTime(n.created_at, lang, tz)}</span>
                          <div>{t(n.message_key, n.params as Record<string, string>)}</div>
                        </div>
                        <div className="row-actions">
                          <Link className="btn btn-sm" to={attentionLink(n.kind, n.task_id, n.run_id)}>{t("common.handle")}</Link>
                          <button className="btn btn-sm btn-ghost" onClick={() => void resolve(n.id)}>{t("common.dismiss")}</button>
                        </div>
                      </li>
                    ))}
                  </ul>
                )}
              </section>
              <section className="card">
                <h2 className="card-title">{t("dash.queue")}</h2>
                {data.queue.length === 0 ? <p className="muted">{t("dash.queue_empty")}</p> : (
                  <table className="table">
                    <tbody>
                      {data.queue.map((q) => (
                        <tr key={q.run_id}>
                          <td><Link to={`/logs?run=${q.run_id}`}>{q.task_name ?? q.run_no}</Link><div className="muted small">{siteName(q.site_id)}</div></td>
                          <td><RunProgress compact run={{ id: q.run_id, status: q.status, progress_stage: q.progress_stage, progress_pct: q.progress_pct, wait_reason: q.wait_reason, result_count: q.result_count }} /></td>
                          <td className="muted small nowrap">{formatDateTime(q.started_at ?? q.queued_at, lang, tz)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </section>
            </div>

            <section className="card">
              <h2 className="card-title">{t("dash.activity")}</h2>
              {data.activity.length === 0 ? <p className="muted">{t("state.empty")}</p> : (
                <ul className="activity">
                  {data.activity.map((a, i) => (
                    <li key={i}>
                      <span className="muted time">{formatDateTime(a.at, lang, tz)}</span>
                      {a.kind === "event" && (
                        <>
                          <EventBadge type={a.event_type ?? ""} />
                          <Link to={`/properties/${a.listing_id}`}>{a.title ?? `#${a.listing_id}`}</Link>
                          <span className="muted">{siteName(a.site_id)}</span>
                          {(a.event_type === "PRICE_DOWN" || a.event_type === "PRICE_UP") && (
                            <span>{formatPrice(a.old_price_yen, lang, a.deal_type ?? "buy")} → {formatPrice(a.new_price_yen, lang, a.deal_type ?? "buy")} {formatPriceDiff(a.old_price_yen, a.new_price_yen, lang)}</span>
                          )}
                        </>
                      )}
                      {a.kind === "run" && (
                        <>
                          <RunStatusBadge status={a.status ?? ""} />
                          <Link to={`/logs?run=${a.run_id}`}>{a.run_no}</Link>
                          <span>{t("dash.run_new", { n: a.new_count ?? 0 })}</span>
                          {a.error_code && <span className="muted">{t(`error_short.${a.error_code}`)}</span>}
                        </>
                      )}
                      {a.kind === "favorite" && (
                        <>
                          <span className="badge badge-accent">★</span>
                          <Link to={`/properties/${a.listing_id}`}>#{a.listing_id}</Link>
                          <span>{t(`fav_status.${a.status}`)}</span>
                        </>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </section>
          </>
        )}
      </StateView>
    </div>
  );
}
