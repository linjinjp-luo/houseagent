import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { RulesCheck } from "../api/types";
import { useI18n } from "../i18n";
import { useApp } from "../lib/app";
import { formatDateTime } from "../lib/format";
import { Badge, useErrorText } from "./common";

const CAT_TONE = { automation: "bad", commercial: "warn", private_use: "info", reproduction: "warn" } as const;

/** Findings of an automatic robots.txt / terms-of-use check. */
export function RulesFindings({ check }: { check: RulesCheck }) {
  const { t, lang } = useI18n();
  const { settings } = useApp();
  const blocked = check.robots_findings.filter((f) => !f.allowed);
  return (
    <div className="rules-findings">
      <p className="small muted">
        {t("rules.checked_at", { at: formatDateTime(check.checked_at, lang, settings.timezone) })}
        {" · "}<Badge tone={check.status === "ok" ? "ok" : check.status === "warning" ? "warn" : "bad"}>{t(`rules.status.${check.status}`)}</Badge>
      </p>
      <h4>robots.txt <a className="small" href={check.robots_url ?? "#"} target="_blank" rel="noopener noreferrer">{check.robots_url}</a></h4>
      {check.robots_findings.length === 0 ? <p className="muted small">{t("rules.robots_unknown")}</p> : (
        <>
          <p className="small">{blocked.length ? t("rules.robots_disallow", { n: blocked.length, total: check.robots_findings.length }) : t("rules.robots_ok")}</p>
          <ul className="rules-list">
            {check.robots_findings.map((f) => (
              <li key={f.path}><Badge tone={f.allowed ? "ok" : "bad"}>{f.allowed ? t("rules.allowed") : t("rules.disallowed")}</Badge>
                <span className="small">{t(`rules.purpose.${f.purpose}`)}</span> <code className="small">{f.path}</code></li>
            ))}
          </ul>
        </>
      )}
      <h4>{t("rules.terms")}</h4>
      {check.terms_findings.length === 0 ? <p className="muted small">{t("rules.terms_none")}</p> : (
        <ul className="rules-list">
          {check.terms_findings.map((f, i) => (
            <li key={i}>
              <Badge tone={CAT_TONE[f.category as keyof typeof CAT_TONE] ?? "muted"}>{t(`rules.cat.${f.category}`)}</Badge>
              <span className="small">「{f.keyword}」</span>
              <blockquote className="rules-quote">{f.snippet}</blockquote>
              <a className="small" href={f.url} target="_blank" rel="noopener noreferrer">{t("rules.open_source")}</a>
            </li>
          ))}
        </ul>
      )}
      {check.fetch_errors.length > 0 && (
        <p className="form-error small">{t("rules.fetch_errors", { urls: check.fetch_errors.map((e) => e.url).join(", ") })}</p>
      )}
      <p className="muted small">{t("rules.disclaimer")}</p>
    </div>
  );
}

/**
 * Runs the automatic check for a site (or shows the latest one) and lets the user accept it.
 * onAccepted is called after the acceptance is recorded.
 */
export function RulesCheckPanel({ siteId, autoRun, onAccepted, acceptLabel }: {
  siteId: string;
  autoRun?: boolean;
  onAccepted?: () => void;
  acceptLabel?: string;
}) {
  const { t } = useI18n();
  const { siteName, reloadSites, toast } = useApp();
  const errText = useErrorText();
  const [check, setCheck] = useState<RulesCheck | null>(null);
  const [busy, setBusy] = useState(false);
  const [agree, setAgree] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = async () => {
    setBusy(true);
    setError(null);
    setAgree(false);
    try {
      setCheck(await api.post<RulesCheck>(`/sites/${siteId}/rules-check`));
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    if (autoRun) void run();
    else api.get<RulesCheck | null>(`/sites/${siteId}/rules-check`).then(setCheck).catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [siteId]);

  const accept = async () => {
    if (!check) return;
    setBusy(true);
    try {
      await api.post(`/sites/${siteId}/rules-check/${check.id}/accept`);
      await reloadSites();
      toast("success", t("rules.accepted", { site: siteName(siteId) }));
      onAccepted?.();
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="rules-panel">
      {busy && !check && <p><span className="spinner" /> {t("rules.checking")}</p>}
      {check && <RulesFindings check={check} />}
      {error && <p className="form-error">{error}</p>}
      <div className="row-actions wrap">
        <button className="btn btn-sm" disabled={busy} onClick={() => void run()}>{check ? t("rules.recheck") : t("rules.check_now")}</button>
      </div>
      {check && check.status !== "unreachable" && !check.accepted_at && (
        <div className={`rules-accept ${check.status === "warning" ? "hint-warn" : ""}`}>
          <label className="check">
            <input type="checkbox" checked={agree} onChange={(e) => setAgree(e.target.checked)} />
            <span>{t(check.status === "warning" ? "rules.agree_warning" : "rules.agree_ok")}</span>
          </label>
          <button className="btn btn-primary btn-sm" disabled={!agree || busy} onClick={() => void accept()}>{acceptLabel ?? t("rules.accept")}</button>
        </div>
      )}
      {check?.accepted_at && <p className="small"><Badge tone="ok">{t("rules.accepted_badge")}</Badge></p>}
      {check?.status === "unreachable" && <p className="form-error small">{t("rules.unreachable")}</p>}
    </div>
  );
}
