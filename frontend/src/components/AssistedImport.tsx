import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, ApiError } from "../api/client";
import type { AssistedImportResult, AssistedSession, Task } from "../api/types";
import { useI18n } from "../i18n";
import { useApp } from "../lib/app";
import { formatDateTime } from "../lib/format";
import { Modal, useErrorText } from "./common";

/**
 * Browser-assisted import: HouseAgent opens a visible browser; the user browses (and completes any verification);
 * "import this page" reads the result list currently shown. HouseAgent never solves verifications or pages on its own.
 */
export function AssistedImportDialog({ task, siteId, onClose }: { task: Task; siteId: string; onClose: () => void }) {
  const { t, lang } = useI18n();
  const { settings, siteName, toast } = useApp();
  const errText = useErrorText();
  const [session, setSession] = useState<AssistedSession | null>(null);
  const [busy, setBusy] = useState(true);
  const [notice, setNotice] = useState<{ kind: "warn" | "error"; text: string } | null>(null);

  useEffect(() => {
    let alive = true;
    api.post<AssistedSession>(`/search-tasks/${task.id}/assisted`, { site_id: siteId })
      .then((s) => alive && setSession(s))
      .catch((e) => alive && setNotice({ kind: "error", text: errText(e) }))
      .finally(() => alive && setBusy(false));
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [task.id, siteId]);

  // Notice when the user closes the browser window themselves.
  useEffect(() => {
    if (!session?.open) return;
    const id = window.setInterval(() => {
      api.get<AssistedSession>(`/assisted/${session.id}`).then(setSession).catch(() => setSession((s) => (s ? { ...s, open: false } : s)));
    }, 3000);
    return () => window.clearInterval(id);
  }, [session?.id, session?.open]);

  const importPage = async () => {
    if (!session) return;
    setBusy(true);
    setNotice(null);
    try {
      const r = await api.post<AssistedImportResult>(`/assisted/${session.id}/import`);
      setSession({ ...session, imports: [...session.imports, r], next_url: r.next_url });
      toast("success", t("assisted.imported", { kept: r.kept, new: r.new, changed: r.changed }));
    } catch (e) {
      const pending = e instanceof ApiError && e.messageKey === "error.assisted_verification_pending";
      setNotice({ kind: pending ? "warn" : "error", text: errText(e) });
    } finally {
      setBusy(false);
    }
  };

  const close = async () => {
    if (session?.open) await api.post(`/assisted/${session.id}/close`).catch(() => undefined);
    onClose();
  };

  const totals = (session?.imports ?? []).reduce((a, r) => ({ kept: a.kept + r.kept, new: a.new + r.new, changed: a.changed + r.changed }), { kept: 0, new: 0, changed: 0 });

  return (
    <Modal wide title={t("assisted.title", { site: siteName(siteId), task: task.name })} onClose={() => void close()}
      footer={<>
        <button className="btn" onClick={() => void close()}>{session?.open ? t("assisted.close_browser") : t("common.close")}</button>
        <button className="btn btn-primary" disabled={busy || !session?.open} onClick={() => void importPage()}>{t("assisted.import_page")}</button>
      </>}>
      <ol className="steps-list">
        <li>{t("assisted.step1")}</li>
        <li>{t("assisted.step2")}</li>
        <li>{t("assisted.step3")}</li>
        <li>{t("assisted.step4")}</li>
      </ol>
      <p className="muted small">{t("assisted.principle")}</p>
      {busy && !session && <p><span className="spinner" /> {t("assisted.opening")}</p>}
      {session && !session.open && <p className="hint-box hint-warn">{t("assisted.window_closed")}</p>}
      {session && session.start_urls.length > 1 && (
        <p className="small">{t("assisted.other_start_pages")}{" "}
          {session.start_urls.map((u) => <a key={u.url} className="site-link" href={u.url} target="_blank" rel="noopener noreferrer">{t(`ptype.${u.property_type}`)}</a>)}
        </p>
      )}
      {notice && <p className={notice.kind === "warn" ? "hint-box hint-warn" : "form-error"}>{notice.text}</p>}
      {session && session.imports.length > 0 && (
        <>
          <h3>{t("assisted.imports", { n: session.imports.length, kept: totals.kept, new: totals.new, changed: totals.changed })}</h3>
          <table className="table">
            <thead><tr><th>{t("assisted.col.time")}</th><th>{t("assisted.col.page")}</th><th>{t("assisted.col.counts")}</th><th /></tr></thead>
            <tbody>
              {session.imports.map((r) => (
                <tr key={r.run_id}>
                  <td className="nowrap small">{formatDateTime(r.at, lang, settings.timezone)}</td>
                  <td className="small ellipsis" title={r.url}>{r.url.replace(/^https?:\/\/[^/]+/, "")}</td>
                  <td className="small nowrap">{t("assisted.counts", { items: r.items, kept: r.kept, new: r.new, changed: r.changed })}{r.total != null ? ` · ${t("assisted.site_total", { n: r.total })}` : ""}</td>
                  <td><Link className="small" to={`/properties?run_id=${r.run_id}`}>{t("logs.view_results")}</Link></td>
                </tr>
              ))}
            </tbody>
          </table>
          {session.next_url && <p className="muted small">{t("assisted.next_hint")}</p>}
        </>
      )}
    </Modal>
  );
}
