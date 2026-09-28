import { useState } from "react";
import { InvestmentPanel } from "../components/Investment";
import { Link, useParams } from "react-router-dom";
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api } from "../api/client";
import type { History, ListingDetail, Note } from "../api/types";
import {
  Badge, ConfirmDialog, EventBadge, Field, ListingStatusBadge, PageHeader, SourceLink, StateView, useErrorText,
} from "../components/common";
import { useI18n } from "../i18n";
import { useApp } from "../lib/app";
import { formatArea, formatDateTime, formatPrice, formatPriceDiff, formatYen } from "../lib/format";
import { useLoad } from "../lib/hooks";

const SERIES_COLORS = ["var(--series-1)", "var(--series-2)", "var(--series-3)"];
const RESEARCH_FIELDS = ["sunlight", "transport", "surroundings", "building_condition", "price_evaluation"] as const;

export default function PropertyDetail() {
  const { id } = useParams();
  const { t, lang } = useI18n();
  const { settings, siteName, toast } = useApp();
  const errText = useErrorText();
  const tz = settings.timezone;
  const detail = useLoad(() => api.get<ListingDetail>(`/properties/${id}`), [id]);
  const history = useLoad(() => api.get<History>(`/properties/${id}/history`), [id]);
  const [unlinkId, setUnlinkId] = useState<number | null>(null);
  const [ai, setAi] = useState<{ summary: string; model: string } | null>(null);
  const [aiBusy, setAiBusy] = useState(false);

  const reload = () => { detail.reload(); history.reload(); };
  const d = detail.data;

  const setFav = async (patch: Partial<{ status: string; research_status: string; visit_date: string | null }>) => {
    if (!d) return;
    const cur = d.favorite ?? { status: "watching", research_status: "pending_research", visit_date: null };
    try {
      await api.put(`/properties/${d.id}/favorite`, { ...cur, ...patch });
      detail.reload();
    } catch (e) { toast("error", errText(e)); }
  };

  // Price timeline: one series per source site, from snapshots (written only on new / change).
  const chartData = (() => {
    const snaps = history.data?.snapshots ?? [];
    return snaps.filter((s) => s.price_yen != null).map((s) => ({
      at: formatDateTime(s.captured_at, lang, tz),
      [s.site_id]: Math.round((s.price_yen ?? 0) / 10_000),
    }));
  })();
  const seriesSites = [...new Set((history.data?.snapshots ?? []).map((s) => s.site_id))];

  return (
    <div className="page">
      <StateView loading={detail.loading} error={detail.error} onRetry={reload}>
        {d && (
          <>
            <PageHeader title={d.title ?? `#${d.id}`} description={t("detail.desc")}
              actions={<>
                <Link className="btn" to="/properties">← {t("menu.properties")}</Link>
                {d.sources[0] && <SourceLink url={d.sources[0].source_url} status={d.sources[0].url_status} />}
              </>} />
            <div className="grid-2">
              <section className="card">
                <h2 className="card-title">{t("detail.summary")}</h2>
                <dl className="kv">
                  <dt>{t("props.col.status")}</dt><dd><ListingStatusBadge status={d.current_status} /> {d.current_status === "not_found" && <span className="muted small">{t("detail.not_found_note")}</span>}</dd>
                  <dt>{t("cond.types")}</dt><dd>{t(`ptype.${d.property_type ?? "unknown"}`)}</dd>
                  <dt>{t("props.address")}</dt><dd>{d.address ?? "—"}</dd>
                  <dt>{t("props.building")}</dt><dd>{d.building_name ?? "—"}</dd>
                  <dt>{t("props.deal")}</dt><dd><span className={`deal-tag deal-${d.deal_type}`}>{t(`deal.${d.deal_type}`)}</span></dd>
                  <dt>{t(d.deal_type === "rent" ? "cond.rent" : "props.col.price")}</dt><dd><b>{formatPrice(d.price_yen, lang, d.deal_type)}</b></dd>
                  {d.deal_type === "rent" && d.sources[0] && <>
                    <dt>{t("rent.mgmt_fee")}</dt><dd>{formatYen(d.sources[0].management_fee_yen, lang)}</dd>
                    <dt>{t("rent.deposit")} / {t("rent.key_money")}</dt><dd>{formatYen(d.sources[0].deposit_yen, lang)} / {formatYen(d.sources[0].key_money_yen, lang)}</dd>
                    <dt>{t("rent.monthly_total")}</dt><dd>{formatPrice((d.price_yen ?? 0) + (d.sources[0].management_fee_yen ?? 0), lang, "rent")}</dd>
                  </>}
                  <dt>{t("props.col.area")}</dt><dd>{formatArea(d.area_m2, lang)}{d.land_area_m2 ? ` (${t("detail.land")} ${formatArea(d.land_area_m2, lang)})` : ""}</dd>
                  <dt>{t("props.col.layout")}</dt><dd>{d.layout ?? "—"}{d.floor ? ` · ${t("props.floor", { n: d.floor })}` : ""}</dd>
                  <dt>{t("props.built_year")}</dt><dd>{d.built_year || "—"}</dd>
                  <dt>{t("props.col.first_seen")}</dt><dd>{formatDateTime(d.first_seen_at, lang, tz)}</dd>
                  <dt>{t("props.last_seen")}</dt><dd>{formatDateTime(d.last_seen_at, lang, tz)}</dd>
                  <dt>{t("detail.found_by")}</dt>
                  <dd>{d.found_by.length ? d.found_by.map((f) => <div key={`${f.task_id}-${f.condition_version}`}><Link to={`/logs?task_id=${f.task_id}`}>{t("detail.task_version", { id: f.task_id, v: f.condition_version })}</Link></div>) : t("detail.manual_only")}</dd>
                  <dt>{t("detail.tags")}</dt><dd>{d.tags.length ? d.tags.map((x) => <span key={x} className="tag">#{x}</span>) : "—"}</dd>
                </dl>
                <p className="muted small">{t("props.summary_only_note")}</p>
              </section>
              <section className="card">
                <h2 className="card-title">{t("detail.my_status")}</h2>
                <Field label={t("fav.status")}>
                  <select value={d.favorite?.status ?? ""} onChange={(e) => e.target.value ? void setFav({ status: e.target.value }) : void api.del(`/properties/${d.id}/favorite`).then(detail.reload)}>
                    <option value="">{t("fav.not_favorite")}</option>
                    {["watching", "planning_visit", "visited", "not_considering", "ended"].map((s) => <option key={s} value={s}>{t(`fav_status.${s}`)}</option>)}
                  </select>
                </Field>
                {d.favorite && (
                  <>
                    <Field label={t("fav.research_status")}>
                      <select value={d.favorite.research_status} onChange={(e) => void setFav({ research_status: e.target.value })}>
                        {["pending_research", "researched", "pending_visit", "ended"].map((s) => <option key={s} value={s}>{t(`research_status.${s}`)}</option>)}
                      </select>
                    </Field>
                    <Field label={t("fav.visit_date")}>
                      <input type="date" value={d.favorite.visit_date ?? ""} onChange={(e) => void setFav({ visit_date: e.target.value || null })} />
                    </Field>
                  </>
                )}
                <Field label={t("detail.review")}>
                  <select value={d.review_status} onChange={async (e) => { await api.patch(`/properties/${d.id}`, { review_status: e.target.value }); detail.reload(); }}>
                    {["new", "viewed", "watching", "excluded"].map((s) => <option key={s} value={s}>{t(`review.${s}`)}</option>)}
                  </select>
                </Field>
                {settings.ai_enabled && (
                  <div className="ai-box">
                    <button className="btn btn-sm" disabled={aiBusy} onClick={async () => {
                      setAiBusy(true);
                      try { setAi(await api.post(`/properties/${d.id}/ai-summary`)); } catch (e) { toast("error", errText(e)); } finally { setAiBusy(false); }
                    }}>{aiBusy ? t("state.loading") : t("detail.ai_summary")}</button>
                    {ai && <div className="ai-output"><Badge tone="info">{t("detail.ai_generated")}</Badge><p>{ai.summary}</p><p className="muted small">{t("detail.ai_disclaimer")}</p></div>}
                  </div>
                )}
              </section>
            </div>

            <section className="card">
              <h2 className="card-title">{t("detail.sources")}</h2>
              <table className="table">
                <thead><tr><th>{t("props.col.sites")}</th><th>ID</th><th>{t("props.col.price")}</th><th>{t("props.col.status")}</th><th>{t("props.col.first_seen")}</th><th>{t("props.last_seen")}</th><th>{t("detail.last_checked")}</th><th /></tr></thead>
                <tbody>
                  {d.sources.map((s) => (
                    <tr key={s.id}>
                      <td>{siteName(s.site_id)}</td><td className="mono">{s.external_listing_id}</td>
                      <td>{formatPrice(s.price_yen, lang, s.deal_type)}</td><td><ListingStatusBadge status={s.observation_status} /></td>
                      <td>{formatDateTime(s.first_seen_at, lang, tz)}</td><td>{formatDateTime(s.last_seen_at, lang, tz)}</td><td>{formatDateTime(s.last_checked_at, lang, tz)}</td>
                      <td className="row-actions">
                        <SourceLink url={s.source_url} status={s.url_status} />
                        <button className="btn btn-sm btn-ghost" onClick={async () => { await api.post(`/sources/${s.id}/check-url`); detail.reload(); }}>{t("detail.check_link")}</button>
                        {d.sources.length > 1 && <button className="btn btn-sm btn-ghost" onClick={() => setUnlinkId(s.id)}>{t("detail.unlink")}</button>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {d.match_candidates.length > 0 && <p className="hint-box">{t("detail.has_candidates", { n: d.match_candidates.length })} <Link to="/properties?tab=matches">{t("common.handle")}</Link></p>}
            </section>

            <section className="card" id="investment">
              <h2 className="card-title">{t("inv.title")}</h2>
              <InvestmentPanel listingId={d.id} isBuy={d.deal_type === "buy"} />
            </section>

            <section className="card">
              <h2 className="card-title">{t("detail.price_timeline")}</h2>
              <StateView loading={history.loading} error={history.error} onRetry={history.reload} empty={chartData.length === 0} emptyText={t("detail.no_prices")}>
                <div className="chart-box">
                  <ResponsiveContainer width="100%" height={240}>
                    <LineChart data={chartData}>
                      <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                      <XAxis dataKey="at" fontSize={12} />
                      <YAxis fontSize={12} width={64} unit={lang === "en" ? "" : t("unit.man")} domain={[(min: number) => Math.floor(min * 0.97), (max: number) => Math.ceil(max * 1.03)]} />
                      <Tooltip />
                      <Legend formatter={(v: string) => <span style={{ color: "var(--text-2)" }}>{siteName(v)}</span>} />
                      {seriesSites.map((sid, i) => <Line key={sid} type="stepAfter" dataKey={sid} stroke={SERIES_COLORS[i % SERIES_COLORS.length]} strokeWidth={2} connectNulls dot={{ r: 4 }} />)}
                    </LineChart>
                  </ResponsiveContainer>
                </div>
              </StateView>
              <h3>{t("detail.events")}</h3>
              <ul className="timeline">
                {(history.data?.events ?? []).slice().reverse().map((e) => (
                  <li key={e.id}>
                    <span className="muted time">{formatDateTime(e.created_at, lang, tz)}</span>
                    <EventBadge type={e.event_type} />
                    <span>{siteName(e.site_id)}</span>
                    {(e.event_type === "PRICE_DOWN" || e.event_type === "PRICE_UP") && <span>{formatPrice(e.old_price_yen, lang, d.deal_type)} → {formatPrice(e.new_price_yen, lang, d.deal_type)} {formatPriceDiff(e.old_price_yen, e.new_price_yen, lang)}</span>}
                    {e.event_type === "NEW" && <span>{formatPrice(e.new_price_yen, lang, d.deal_type)}</span>}
                    {e.run_no ? <Link to={`/logs?run=${e.run_id}`} className="muted">{e.run_no}</Link> : <span className="muted">{t("detail.manual_entry")}</span>}
                    {e.event_type === "NOT_FOUND" && <span className="muted small">{t("detail.not_found_note")}</span>}
                  </li>
                ))}
              </ul>
            </section>

            <NotesSection listingId={d.id} notes={d.notes} onChange={detail.reload} />
            {unlinkId !== null && (
              <ConfirmDialog title={t("detail.unlink")} message={t("detail.unlink_body")} onClose={() => setUnlinkId(null)}
                onConfirm={async () => { await api.post(`/sources/${unlinkId}/unlink`); toast("success", t("detail.unlinked")); reload(); }} />
            )}
          </>
        )}
      </StateView>
    </div>
  );
}

function NotesSection({ listingId, notes, onChange }: { listingId: number; notes: Note[]; onChange: () => void }) {
  const { t, lang } = useI18n();
  const { settings, toast } = useApp();
  const errText = useErrorText();
  const [kind, setKind] = useState<"note" | "research">("note");
  const [body, setBody] = useState("");
  const [fields, setFields] = useState<Record<string, string>>({});
  const [deleteId, setDeleteId] = useState<number | null>(null);

  const save = async () => {
    if (!body.trim() && Object.values(fields).every((v) => !v)) return;
    try {
      await api.post(`/properties/${listingId}/notes`, { kind, body, fields });
      setBody("");
      setFields({});
      toast("success", t("notes.saved"));
      onChange();
    } catch (e) { toast("error", errText(e)); }
  };

  return (
    <section className="card">
      <h2 className="card-title">{t("notes.title")}</h2>
      <p className="muted small">{t("notes.help")}</p>
      <div className="tabs small-tabs">
        <button className={kind === "note" ? "active" : ""} onClick={() => setKind("note")}>{t("notes.kind.note")}</button>
        <button className={kind === "research" ? "active" : ""} onClick={() => setKind("research")}>{t("notes.kind.research")}</button>
      </div>
      {kind === "research" && (
        <div className="form-grid two">
          {RESEARCH_FIELDS.map((f) => (
            <Field key={f} label={t(`research.${f}`)}>
              <select value={fields[f] ?? ""} onChange={(e) => setFields({ ...fields, [f]: e.target.value })}>
                <option value="">—</option>{[5, 4, 3, 2, 1].map((n) => <option key={n} value={n}>{"★".repeat(n)}</option>)}
              </select>
            </Field>
          ))}
          <Field label={t("research.visit_date")}><input type="date" value={fields.visit_date ?? ""} onChange={(e) => setFields({ ...fields, visit_date: e.target.value })} /></Field>
          <Field label={t("research.pros")}><input value={fields.pros ?? ""} onChange={(e) => setFields({ ...fields, pros: e.target.value })} /></Field>
          <Field label={t("research.cons")}><input value={fields.cons ?? ""} onChange={(e) => setFields({ ...fields, cons: e.target.value })} /></Field>
          <Field label={t("research.next_action")}><input value={fields.next_action ?? ""} onChange={(e) => setFields({ ...fields, next_action: e.target.value })} /></Field>
          <Field label={t("research.prep")}><input value={fields.prep ?? ""} onChange={(e) => setFields({ ...fields, prep: e.target.value })} /></Field>
        </div>
      )}
      <textarea rows={3} value={body} onChange={(e) => setBody(e.target.value)} placeholder={t("notes.placeholder")} />
      <div className="row-actions"><button className="btn btn-primary btn-sm" onClick={() => void save()}>{t("common.save")}</button></div>
      <ul className="notes">
        {notes.map((n) => (
          <li key={n.id}>
            <div className="note-head">
              <Badge tone={n.kind === "research" ? "accent" : "muted"}>{t(`notes.kind.${n.kind}`)}</Badge>
              <span className="muted small">{formatDateTime(n.created_at, lang, settings.timezone)}</span>
              <button className="btn btn-sm btn-ghost-danger" onClick={() => setDeleteId(n.id)}>{t("common.delete")}</button>
            </div>
            {Object.entries(n.fields).filter(([, v]) => v !== "" && v != null).length > 0 && (
              <div className="note-fields">
                {Object.entries(n.fields).filter(([, v]) => v !== "" && v != null).map(([k, v]) => (
                  <span key={k}>{t(`research.${k}`)}: {RESEARCH_FIELDS.includes(k as (typeof RESEARCH_FIELDS)[number]) ? "★".repeat(Number(v)) : String(v)}</span>
                ))}
              </div>
            )}
            {n.body && <p className="note-body">{n.body}</p>}
          </li>
        ))}
      </ul>
      {deleteId !== null && (
        <ConfirmDialog danger title={t("notes.delete_title")} message={t("notes.delete_body")} confirmLabel={t("common.delete")} onClose={() => setDeleteId(null)}
          onConfirm={async () => { await api.del(`/notes/${deleteId}`); onChange(); }} />
      )}
    </section>
  );
}
