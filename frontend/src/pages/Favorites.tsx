import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { Listing } from "../api/types";
import { Badge, ListingStatusBadge, PageHeader, SourceLink, StateView, useErrorText } from "../components/common";
import { useI18n } from "../i18n";
import { useApp } from "../lib/app";
import { formatArea, formatPrice } from "../lib/format";
import { useLoad } from "../lib/hooks";

const STATUSES = ["watching", "planning_visit", "visited", "not_considering", "ended"];

export default function Favorites() {
  const { t, lang } = useI18n();
  const { siteName, toast } = useApp();
  const errText = useErrorText();
  const [status, setStatus] = useState("");
  const { data, error, loading, reload } = useLoad(() => api.get<Listing[]>("/favorites", { status }), [status]);

  const update = async (l: Listing, patch: Record<string, string | null>) => {
    try {
      await api.put(`/properties/${l.id}/favorite`, { ...l.favorite, ...patch });
      reload();
    } catch (e) { toast("error", errText(e)); }
  };

  return (
    <div className="page">
      <PageHeader title={t("menu.favorites")} description={t("fav.desc")} />
      <div className="tabs">
        <button className={status === "" ? "active" : ""} onClick={() => setStatus("")}>{t("common.all")}</button>
        {STATUSES.map((s) => <button key={s} className={status === s ? "active" : ""} onClick={() => setStatus(s)}>{t(`fav_status.${s}`)}</button>)}
      </div>
      <StateView loading={loading} error={error} onRetry={reload} empty={!!data && data.length === 0}
        emptyText={<><p>{t("fav.empty")}</p><Link className="btn" to="/properties">{t("menu.properties")}</Link></>}>
        <div className="fav-grid">
          {(data ?? []).map((l) => (
            <article key={l.id} className="card fav-card">
              <div className="fav-head">
                <Link to={`/properties/${l.id}`}><b>{l.title ?? `#${l.id}`}</b></Link>
                <ListingStatusBadge status={l.current_status} />
              </div>
              {l.current_status !== "active" && <p className="muted small">{t("fav.source_gone_note")}</p>}
              <div className="muted small">{l.address} · {l.site_ids.map(siteName).join(" / ")}</div>
              <div className="fav-facts"><b>{formatPrice(l.price_yen, lang, l.deal_type)}</b> · {formatArea(l.area_m2, lang)} · {l.layout ?? "—"}</div>
              <div>{l.tags.map((x) => <span key={x} className="tag">#{x}</span>)}</div>
              <div className="field-row">
                <select value={l.favorite?.status} onChange={(e) => void update(l, { status: e.target.value })} aria-label={t("fav.status")}>
                  {STATUSES.map((s) => <option key={s} value={s}>{t(`fav_status.${s}`)}</option>)}
                </select>
                <select value={l.favorite?.research_status} onChange={(e) => void update(l, { research_status: e.target.value })} aria-label={t("fav.research_status")}>
                  {["pending_research", "researched", "pending_visit", "ended"].map((s) => <option key={s} value={s}>{t(`research_status.${s}`)}</option>)}
                </select>
                <input type="date" value={l.favorite?.visit_date ?? ""} onChange={(e) => void update(l, { visit_date: e.target.value || null })} aria-label={t("fav.visit_date")} />
              </div>
              <div className="fav-foot">
                <Badge tone="muted">{t("fav.notes_count", { n: l.note_count ?? 0 })}</Badge>
                <Link className="btn btn-sm" to={`/properties/${l.id}`}>{t("fav.open_research")}</Link>
                {l.sources[0] && <SourceLink url={l.sources[0].source_url} status={l.current_status === "active" ? l.sources[0].url_status : "unreachable"} />}
              </div>
            </article>
          ))}
        </div>
        <p className="muted small">{t("fav.no_auto_contact")}</p>
      </StateView>
    </div>
  );
}
