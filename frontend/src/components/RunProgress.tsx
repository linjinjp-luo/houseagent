import { Link } from "react-router-dom";
import { hasKey, useI18n } from "../i18n";
import { formatDateTime } from "../lib/format";
import { useApp } from "../lib/app";
import { RunStatusBadge } from "./common";

export interface RunLike {
  id: number;
  status: string;
  progress_stage?: string;
  progress_pct?: number;
  wait_reason?: string | null;
  error_code?: string | null;
  error_detail?: string | null;
  result_count?: number;
  new_count?: number;
  changed_count?: number;
  expected_total?: number | null;
  not_before?: string | null;
}

export const ACTIVE = new Set(["queued", "running"]);

/** One sentence explaining a failure: specific detail if we know it, otherwise the error code's message. */
export function useFailureText(): (r: RunLike) => string {
  const { t } = useI18n();
  return (r) => {
    if (r.error_detail && hasKey(`error_detail.${r.error_detail}`)) return t(`error_detail.${r.error_detail}`);
    return r.error_code ? t(`error.${r.error_code}`) : t("error.UNKNOWN_ERROR");
  };
}

export function ProgressBar({ pct, indeterminate }: { pct: number; indeterminate?: boolean }) {
  return (
    <span className={`progress ${indeterminate ? "progress-indeterminate" : ""}`} role="progressbar"
      aria-valuenow={indeterminate ? undefined : pct} aria-valuemin={0} aria-valuemax={100}>
      <span style={{ width: indeterminate ? undefined : `${Math.max(2, pct)}%` }} />
    </span>
  );
}

/**
 * Live state of one run. compact: one-line form for tables; otherwise a block with explanation.
 */
export function RunProgress({ run, compact, siteLabel }: { run: RunLike; compact?: boolean; siteLabel?: string }) {
  const { t, tv, lang } = useI18n();
  const { settings } = useApp();
  const failure = useFailureText();
  const pct = run.progress_pct ?? 0;
  const stage = run.progress_stage ?? run.status;
  const prefix = siteLabel ? <span className="muted small">{siteLabel} </span> : null;

  if (run.status === "queued") {
    const reason = run.wait_reason ? t(`wait.${run.wait_reason}`) : t("wait.starting");
    return (
      <div className={compact ? "run-progress compact" : "run-progress"}>
        {prefix}<RunStatusBadge status="queued" />
        <span className="small">{reason}</span>
        {run.wait_reason === "retry_backoff" && run.not_before && (
          <span className="muted small"> ({t("logs.next_retry")} {formatDateTime(run.not_before, lang, settings.timezone)})</span>
        )}
        {!compact && run.wait_reason && <p className="muted small">{t(`wait_help.${run.wait_reason}`)}</p>}
      </div>
    );
  }

  if (run.status === "running") {
    const counts = run.expected_total != null
      ? t("progress.counts_total", { n: run.result_count ?? 0, total: run.expected_total })
      : t("progress.counts", { n: run.result_count ?? 0 });
    return (
      <div className={compact ? "run-progress compact" : "run-progress"}>
        {prefix}<RunStatusBadge status="running" />
        <ProgressBar pct={pct} indeterminate={stage === "preflight" || stage === "login_check"} />
        <span className="small nowrap"><b>{pct}%</b> · {tv("progress.stage", stage)}</span>
        {!compact && <span className="muted small"> · {counts}</span>}
      </div>
    );
  }

  if (run.status === "completed") {
    return (
      <div className={compact ? "run-progress compact" : "run-progress"}>
        {prefix}<RunStatusBadge status="completed" />
        <span className="small">{t("progress.result", { n: run.result_count ?? 0, new: run.new_count ?? 0, changed: run.changed_count ?? 0 })}</span>
        {!compact && <Link className="small" to={`/properties?run_id=${run.id}`}> {t("logs.view_results")}</Link>}
      </div>
    );
  }

  // paused / failed / cancelled / interrupted
  return (
    <div className={compact ? "run-progress compact" : "run-progress run-failed"}>
      {prefix}<RunStatusBadge status={run.status} />
      <span className="small">{run.error_code ? t(`error_short.${run.error_code}`) : ""}</span>
      {compact ? (
        <span className="muted small failure-line" title={failure(run)}>{failure(run)}</span>
      ) : (
        <p className="failure-text">{failure(run)}</p>
      )}
    </div>
  );
}
