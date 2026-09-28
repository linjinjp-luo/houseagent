import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { BatchEstimate, BatchJob } from "../api/types";
import { useI18n } from "../i18n";
import { useApp } from "../lib/app";
import { formatNumber } from "../lib/format";
import { Badge, Modal, useErrorText } from "./common";

/**
 * Batch investment assessment (spec 4.7.2): show how many properties, AI calls and the estimated usage first;
 * start only on confirmation; show progress; stop on the daily limit; allow cancelling.
 */
export function BatchAssessButton({ scope, listingIds, onDone, className }: { scope: "ids" | "favorites"; listingIds?: number[]; onDone?: () => void; className?: string }) {
  const { t } = useI18n();
  const { toast } = useApp();
  const errText = useErrorText();
  const [estimate, setEstimate] = useState<BatchEstimate | null>(null);
  const [busy, setBusy] = useState(false);
  const open = async () => {
    setBusy(true);
    try {
      setEstimate(await api.post<BatchEstimate>("/properties/investment-assessments:batch", { scope, listing_ids: listingIds, estimate_only: true }));
    } catch (e) { toast("error", errText(e)); } finally { setBusy(false); }
  };
  return (
    <>
      <button className={className ?? "btn btn-sm"} disabled={busy || (scope === "ids" && !listingIds?.length)} onClick={() => void open()}>
        {scope === "favorites" ? t("inv.batch_favorites") : t("inv.batch_selected", { n: listingIds?.length ?? 0 })}
      </button>
      {estimate && <BatchDialog scope={scope} listingIds={listingIds} estimate={estimate} onClose={() => { setEstimate(null); onDone?.(); }} />}
    </>
  );
}

function BatchDialog({ scope, listingIds, estimate, onClose }: { scope: "ids" | "favorites"; listingIds?: number[]; estimate: BatchEstimate; onClose: () => void }) {
  const { t, lang } = useI18n();
  const errText = useErrorText();
  const [job, setJob] = useState<BatchJob | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const tooMany = estimate.batch_max != null && estimate.total > estimate.batch_max;
  const overLimit = estimate.remaining_calls_today != null && estimate.ai_calls > estimate.remaining_calls_today;

  useEffect(() => {
    if (!job || job.status !== "running") return;
    const h = window.setInterval(() => {
      void api.get<BatchJob | null>("/investment-batches/current").then((j) => j && j.id === job.id && setJob(j)).catch(() => undefined);
    }, 1000);
    return () => window.clearInterval(h);
  }, [job]);

  const start = async () => {
    try { setJob(await api.post<BatchJob>("/properties/investment-assessments:batch", { scope, listing_ids: listingIds })); } catch (e) { setErr(errText(e)); }
  };
  const cancel = async () => { if (job) setJob(await api.post<BatchJob>(`/investment-batches/${job.id}/cancel`)); };
  const pct = job ? Math.round(((job.done + job.failed) / Math.max(1, job.total)) * 100) : 0;

  return (
    <Modal title={t("inv.batch_title")} onClose={onClose}
      footer={job ? (
        <>{job.status === "running" && <button className="btn" onClick={() => void cancel()}>{t("inv.batch_cancel")}</button>}
          <button className="btn btn-primary" onClick={onClose}>{t("common.close")}</button></>
      ) : (
        <><button className="btn" onClick={onClose}>{t("common.cancel")}</button>
          <button className="btn btn-primary" disabled={tooMany || estimate.total === 0} onClick={() => void start()}>{t("inv.batch_start")}</button></>
      )}>
      {!job && (
        <>
          <dl className="kv">
            <dt>{t("inv.batch_total")}</dt><dd>{formatNumber(estimate.total, lang)}</dd>
            <dt>{t("inv.batch_ai_calls")}</dt><dd>{formatNumber(estimate.ai_calls, lang)} {estimate.ai_unavailable_reason && <span className="muted small">({t(`error.${estimate.ai_unavailable_reason}`)})</span>}</dd>
            <dt>{t("inv.batch_rules_only")}</dt><dd>{formatNumber(estimate.rules_only, lang)}</dd>
            <dt>{t("inv.batch_units")}</dt><dd>{formatNumber(estimate.input_units, lang)} / {formatNumber(estimate.output_units, lang)}</dd>
            <dt>{t("inv.batch_cost")}</dt>
            <dd>{estimate.estimated_cost != null ? `${estimate.currency ?? ""} ${estimate.estimated_cost.toFixed(4)}` : t("ai.cost_unknown")}
              {estimate.pricing_updated_at && <span className="muted small"> ({t("inv.price_as_of", { at: estimate.pricing_updated_at })})</span>}</dd>
            {estimate.remaining_calls_today != null && <><dt>{t("inv.batch_remaining")}</dt><dd>{formatNumber(estimate.remaining_calls_today, lang)}</dd></>}
          </dl>
          <p className="muted small">{t("inv.batch_note")}</p>
          {tooMany && <p className="form-error">{t("error.ai_batch_too_large", { count: estimate.total, max: estimate.batch_max ?? 0 })}</p>}
          {overLimit && <p className="hint-box hint-warn">{t("inv.batch_over_limit")}</p>}
        </>
      )}
      {job && (
        <>
          <div className="progress progress-wide"><span style={{ width: `${pct}%` }} /></div>
          <p>{t("inv.batch_progress", { done: job.done, total: job.total, failed: job.failed, ai: job.ai_calls })} · <Badge tone={job.status === "completed" ? "ok" : job.status === "running" ? "info" : "warn"}>{t(`inv.batch_status.${job.status}`)}</Badge></p>
          {job.status === "paused_limit" && <p className="hint-box hint-warn">{t("inv.batch_paused_limit")}</p>}
          {Object.keys(job.labels).length > 0 && (
            <p className="small">{Object.entries(job.labels).map(([l, n]) => `${t(`inv.label.${l}`)} ${n}`).join(" · ")}</p>
          )}
        </>
      )}
      {err && <p className="form-error">{err}</p>}
    </Modal>
  );
}
