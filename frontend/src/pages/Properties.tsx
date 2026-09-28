import { Fragment, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type { Deal, Listing, MatchCandidate, Paged } from "../api/types";
import {
  Badge, ConfirmDialog, EventBadge, Field, ListingStatusBadge, Modal, PageHeader, Pager, SourceLink, StateView, useErrorText,
} from "../components/common";
import { useI18n } from "../i18n";
import { useApp } from "../lib/app";
import { formatArea, formatDate, formatPrice, formatYen } from "../lib/format";
import { useLoad } from "../lib/hooks";
import { LabelBadge } from "../components/Investment";
import { BatchAssessButton } from "../components/InvestmentBatch";

const INV_LABELS = ["resale_candidate", "rental_candidate", "owner_candidate", "low_value", "insufficient_data"] as const;

const FILTER_KEYS = ["q", "deal_type", "site_id", "prefecture", "property_type", "price_min", "price_max", "area_min", "area_max",
  "status", "event", "event_date", "task_id", "run_id", "favorite", "tag", "inv_label", "inv_score_min", "inv_confidence", "inv_missing", "sort"] as const;

export default function Properties() {
  const { t, lang } = useI18n();
  const { settings, sites, regions, siteName, toast } = useApp();
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") === "matches" ? "matches" : "list";
  const page = Number(params.get("page") ?? "1");
  const filters = Object.fromEntries(FILTER_KEYS.map((k) => [k, params.get(k) ?? ""])) as Record<(typeof FILTER_KEYS)[number], string>;
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [tagText, setTagText] = useState("");
  const [importOpen, setImportOpen] = useState(false);
  const errText = useErrorText();
  const tz = settings.timezone;

  const list = useLoad(() => api.get<Paged<Listing>>("/properties", { ...filters, page, page_size: 50 }), [params.toString()]);
  const matches = useLoad(() => (tab === "matches" ? api.get<MatchCandidate[]>("/match-candidates") : Promise.resolve([])), [tab]);

  const setFilter = (k: string, v: string) => {
    const p = new URLSearchParams(params);
    if (v) p.set(k, v); else p.delete(k);
    p.delete("page");
    setParams(p);
  };
  const clearFilters = () => setParams(tab === "matches" ? { tab: "matches" } : {});
  const toggle = (set: Set<number>, id: number, fn: (s: Set<number>) => void) => {
    const n = new Set(set);
    if (n.has(id)) n.delete(id); else n.add(id);
    fn(n);
  };

  const applyTags = async (remove = false) => {
    const names = tagText.split(/[,、]/).map((x) => x.trim()).filter(Boolean);
    if (!names.length || !selected.size) return;
    try {
      await api.post("/tags/apply", { listing_ids: [...selected], add: remove ? [] : names, remove: remove ? names : [] });
      toast("success", t("props.tags_applied", { n: selected.size }));
      setTagText("");
      list.reload();
    } catch (e) {
      toast("error", errText(e));
    }
  };

  const toggleFavorite = async (l: Listing) => {
    try {
      if (l.favorite) await api.del(`/properties/${l.id}/favorite`);
      else await api.put(`/properties/${l.id}/favorite`, { status: "watching" });
      list.reload();
    } catch (e) {
      toast("error", errText(e));
    }
  };

  const activeFilters = FILTER_KEYS.filter((k) => filters[k] && k !== "sort");

  return (
    <div className="page">
      <PageHeader title={t("menu.properties")} description={t("props.desc")}
        actions={<button className="btn" onClick={() => setImportOpen(true)}>+ {t("props.manual_import")}</button>} />
      <div className="tabs">
        <button className={tab === "list" ? "active" : ""} onClick={() => setParams({})}>{t("props.tab.list")}</button>
        <button className={tab === "matches" ? "active" : ""} onClick={() => setParams({ tab: "matches" })}>{t("props.tab.matches")}</button>
      </div>

      {tab === "list" && (
        <div className="segmented deal-switch">
          {(["", "buy", "rent"] as const).map((d) => (
            <button key={d || "all"} type="button" className={filters.deal_type === d ? "on" : ""} onClick={() => setFilter("deal_type", d)}>
              {d ? t(`deal.${d}`) : t("common.all")}
            </button>
          ))}
        </div>
      )}
      {tab === "list" && (
        <>
          <div className="filter-bar">
            <input placeholder={t("props.search_ph")} defaultValue={filters.q} onKeyDown={(e) => e.key === "Enter" && setFilter("q", (e.target as HTMLInputElement).value)} />
            <select value={filters.site_id} onChange={(e) => setFilter("site_id", e.target.value)}>
              <option value="">{t("props.all_sites")}</option>
              {sites.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
            <select value={filters.prefecture} onChange={(e) => setFilter("prefecture", e.target.value)}>
              <option value="">{t("props.all_regions")}</option>
              {regions.prefectures.map((p) => <option key={p.code} value={p.code}>{p.name[lang]}</option>)}
            </select>
            <select value={filters.property_type} onChange={(e) => setFilter("property_type", e.target.value)}>
              <option value="">{t("props.all_types")}</option>
              {(filters.deal_type ? regions.deals[filters.deal_type as Deal] : regions.property_types).map((p) => <option key={p} value={p}>{t(`ptype.${p}`)}</option>)}
            </select>
            <input className="num" type="number" placeholder={t("cond.price_min")} defaultValue={filters.price_min} onBlur={(e) => setFilter("price_min", e.target.value)} />
            <input className="num" type="number" placeholder={t("cond.price_max")} defaultValue={filters.price_max} onBlur={(e) => setFilter("price_max", e.target.value)} />
            <input className="num" type="number" placeholder={t("cond.area_min")} defaultValue={filters.area_min} onBlur={(e) => setFilter("area_min", e.target.value)} />
            <select value={filters.status} onChange={(e) => setFilter("status", e.target.value)}>
              <option value="">{t("props.all_status")}</option>
              {["active", "not_found", "unavailable"].map((s) => <option key={s} value={s}>{t(`obs.${s}`)}</option>)}
            </select>
            <select value={filters.event} onChange={(e) => setFilter("event", e.target.value)}>
              <option value="">{t("props.any_change")}</option>
              {["NEW", "PRICE_DOWN", "PRICE_UP", "NOT_FOUND", "UNAVAILABLE", "REAPPEARED"].map((s) => <option key={s} value={s}>{t(`event.${s}`)}</option>)}
            </select>
            <input type="date" value={filters.event_date} onChange={(e) => setFilter("event_date", e.target.value)} title={t("props.event_date")} />
            <label className="check"><input type="checkbox" checked={filters.favorite === "true"} onChange={(e) => setFilter("favorite", e.target.checked ? "true" : "")} />{t("props.favorites_only")}</label>
            <select value={filters.sort} onChange={(e) => setFilter("sort", e.target.value)}>
              <option value="">{t("sort.newest")}</option>
              {["price_asc", "price_desc", "area_desc", "first_seen", "score_desc"].map((s) => <option key={s} value={s}>{t(`sort.${s}`)}</option>)}
            </select>
            <select value={filters.inv_label} onChange={(e) => setFilter("inv_label", e.target.value)} aria-label={t("inv.filter_label")}>
              <option value="">{t("inv.filter_label")}</option>
              {INV_LABELS.map((l) => <option key={l} value={l}>{t(`inv.label.${l}`)}</option>)}
            </select>
            <select value={filters.inv_confidence} onChange={(e) => setFilter("inv_confidence", e.target.value)} aria-label={t("inv.confidence")}>
              <option value="">{t("inv.filter_confidence")}</option>
              {["high", "medium", "low"].map((c) => <option key={c} value={c}>{t(`inv.conf.${c}`)}</option>)}
            </select>
            <input className="num" type="number" min={0} max={100} placeholder={t("inv.filter_score")} defaultValue={filters.inv_score_min} onBlur={(e) => setFilter("inv_score_min", e.target.value)} />
            <select value={filters.inv_missing} onChange={(e) => setFilter("inv_missing", e.target.value)} aria-label={t("inv.filter_missing")}>
              <option value="">{t("inv.filter_missing")}</option>
              <option value="true">{t("inv.missing_yes")}</option>
              <option value="false">{t("inv.missing_no")}</option>
            </select>
            {activeFilters.length > 0 && <button className="btn btn-sm btn-ghost" onClick={clearFilters}>{t("props.clear_filters")}</button>}
          </div>
          {(filters.task_id || filters.run_id || filters.tag) && (
            <p className="muted">{filters.task_id && t("props.filter_task", { id: filters.task_id })} {filters.run_id && t("props.filter_run", { id: filters.run_id })} {filters.tag && `#${filters.tag}`}</p>
          )}
          {selected.size > 0 && (
            <div className="bulk-bar">
              <span>{t("props.selected", { n: selected.size })}</span>
              <input placeholder={t("props.tag_ph")} value={tagText} onChange={(e) => setTagText(e.target.value)} />
              <button className="btn btn-sm" onClick={() => void applyTags()}>{t("props.add_tags")}</button>
              <button className="btn btn-sm" onClick={() => void applyTags(true)}>{t("props.remove_tags")}</button>
              <BatchAssessButton scope="ids" listingIds={[...selected].filter((id) => list.data?.items.find((x) => x.id === id)?.deal_type === "buy")} onDone={list.reload} />
              <button className="btn btn-sm btn-ghost" onClick={() => setSelected(new Set())}>{t("common.clear")}</button>
              <span className="muted small">{t("props.merge_note")}</span>
            </div>
          )}
          <StateView loading={list.loading} error={list.error} onRetry={list.reload} empty={!!list.data && list.data.total === 0}
            emptyText={activeFilters.length ? t("props.no_match") : t("props.empty")}>
            {list.data && list.data.total > 0 && (
              <>
                <div className="table-wrap">
                  <table className="table">
                    <thead>
                      <tr>
                        <th /><th /><th>{t("props.col.property")}</th><th>{t("props.col.price")}</th><th>{t("props.col.area")}</th>
                        <th>{t("props.col.layout")}</th><th>{t("props.col.sites")}</th><th>{t("props.col.change")}</th>
                        <th>{t("props.col.first_seen")}</th><th>{t("props.col.status")}</th><th>{t("inv.col.label")}</th><th />
                      </tr>
                    </thead>
                    <tbody>
                      {list.data.items.map((l) => (
                        <Fragment key={l.id}>
                          <tr className={l.review_status === "new" ? "row-new" : ""}>
                            <td><input type="checkbox" checked={selected.has(l.id)} onChange={() => toggle(selected, l.id, setSelected)} aria-label={t("common.select")} /></td>
                            <td><button className={`star ${l.favorite ? "on" : ""}`} onClick={() => void toggleFavorite(l)} title={t("props.favorite")}>★</button></td>
                            <td>
                              <Link to={`/properties/${l.id}`}><b>{l.title ?? `#${l.id}`}</b></Link>
                              <div className="muted small">{l.address ?? ""} {l.built_year ? `· ${t("props.built", { y: l.built_year })}` : ""}</div>
                              {l.tags.map((tg) => <button key={tg} className="tag" onClick={() => setFilter("tag", tg)}>#{tg}</button>)}
                            </td>
                            <td className="nowrap">
                              {formatPrice(l.price_yen, lang, l.deal_type)}
                              {l.deal_type === "rent" && l.sources[0]?.management_fee_yen ? <div className="muted small">{t("rent.mgmt_short")} {formatYen(l.sources[0].management_fee_yen, lang)}</div> : null}
                            </td>
                            <td className="nowrap">{formatArea(l.area_m2, lang)}</td>
                            <td>{l.layout ?? "—"}</td>
                            <td>
                              <button className="btn btn-sm btn-ghost" onClick={() => toggle(expanded, l.id, setExpanded)}>
                                {l.site_ids.map(siteName).join(" / ")} {l.sources.length > 1 && <Badge tone="info">{l.sources.length}</Badge>} {expanded.has(l.id) ? "▴" : "▾"}
                              </button>
                            </td>
                            <td>{l.last_event && <EventBadge type={l.last_event.event_type} />}</td>
                            <td className="nowrap">{formatDate(l.first_seen_at, lang, tz)}</td>
                            <td><ListingStatusBadge status={l.current_status} /></td>
                            <td>{l.investment ? (
                              <Link to={`/properties/${l.id}#investment`} className="inv-cell" title={l.investment.tags.map((g) => t(`inv.tag.${g}`)).join(" / ")}>
                                <LabelBadge label={l.investment.label} overridden={l.investment.overridden} />
                                <span className="muted small">{l.investment.score ?? ""}{l.investment.confidence ? ` · ${t(`inv.conf.${l.investment.confidence}`)}` : ""}{l.investment.missing_count ? ` · ${t("inv.missing_n", { n: l.investment.missing_count })}` : ""}</span>
                              </Link>
                            ) : <span className="muted">—</span>}</td>
                            <td>{l.sources[0] && <SourceLink url={l.sources[0].source_url} status={l.sources[0].url_status} />}</td>
                          </tr>
                          {expanded.has(l.id) && l.sources.map((s) => (
                            <tr key={`s${s.id}`} className="sub-row">
                              <td /><td />
                              <td>{siteName(s.site_id)} · <span className="mono">{s.external_listing_id}</span>{s.manual_import && <Badge tone="muted">{t("props.manual")}</Badge>}</td>
                              <td>{formatPrice(s.price_yen, lang, s.deal_type)}</td>
                              <td>{formatArea(s.area_m2, lang)}</td>
                              <td>{s.layout ?? "—"}</td>
                              <td className="muted small">{t("props.last_seen")}: {formatDate(s.last_seen_at, lang, tz)}</td>
                              <td /><td className="nowrap">{formatDate(s.first_seen_at, lang, tz)}</td>
                              <td><ListingStatusBadge status={s.observation_status} /></td>
                              <td />
                              <td><SourceLink url={s.source_url} status={s.url_status} /></td>
                            </tr>
                          ))}
                        </Fragment>
                      ))}
                    </tbody>
                  </table>
                </div>
                <Pager page={page} pageSize={50} total={list.data.total} onPage={(p) => { const n = new URLSearchParams(params); n.set("page", String(p)); setParams(n); }} />
                <p className="muted small">{t("props.summary_only_note")}</p>
              </>
            )}
          </StateView>
        </>
      )}

      {tab === "matches" && (
        <StateView loading={matches.loading} error={matches.error} onRetry={matches.reload} empty={!!matches.data && matches.data.length === 0} emptyText={t("props.no_candidates")}>
          <p className="hint-box">{t("props.matches_help")}</p>
          {matches.data?.map((m) => <MatchCard key={m.id} m={m} onDone={matches.reload} />)}
        </StateView>
      )}
      {importOpen && <ManualImport onClose={() => setImportOpen(false)} onDone={list.reload} />}
    </div>
  );
}

function MatchCard({ m, onDone }: { m: MatchCandidate; onDone: () => void }) {
  const { t, lang } = useI18n();
  const { siteName, toast } = useApp();
  const [confirm, setConfirm] = useState(false);
  const errText = useErrorText();
  const side = (l: MatchCandidate["listing_a"]) => l && (
    <div className="match-side">
      <Link to={`/properties/${l.id}`}><b>{l.title}</b></Link>
      <div>{l.site_ids.map(siteName).join(" / ")}</div>
      <div>{l.address}</div>
      <div>{formatPrice(l.price_yen, lang, l.deal_type)} · {formatArea(l.area_m2, lang)} · {l.layout ?? "—"} · {l.floor ? t("props.floor", { n: l.floor }) : ""} {l.built_year ?? ""}</div>
    </div>
  );
  return (
    <div className="card match-card">
      <div className="match-head">
        <Badge tone="warn">{t("props.suspected")}</Badge>
        <span>{t("props.score", { s: Math.round(m.score * 100) })}</span>
        <span className="muted">{t("props.reasons")}: {m.reasons.map((r) => t(`match_reason.${r}`)).join("、")}</span>
      </div>
      <div className="match-body">{side(m.listing_a)}<span className="match-vs">⇄</span>{side(m.listing_b)}</div>
      <div className="row-actions">
        <button className="btn btn-primary btn-sm" onClick={() => setConfirm(true)}>{t("props.confirm_match")}</button>
        <button className="btn btn-sm" onClick={async () => {
          try { await api.post(`/match-candidates/${m.id}/reject`); toast("info", t("props.rejected")); onDone(); } catch (e) { toast("error", errText(e)); }
        }}>{t("props.reject_match")}</button>
      </div>
      {confirm && (
        <ConfirmDialog title={t("props.confirm_match")} message={t("props.confirm_match_body")} onClose={() => setConfirm(false)}
          onConfirm={async () => { await api.post(`/match-candidates/${m.id}/confirm`); toast("success", t("props.merged")); onDone(); }} />
      )}
    </div>
  );
}

function ManualImport({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const { t } = useI18n();
  const { sites, regions, toast } = useApp();
  const errText = useErrorText();
  const [deal, setDeal] = useState<Deal>("buy");
  const dealSites = sites.filter((s) => s.capabilities.deals.includes(deal));
  const [f, setF] = useState({ site_id: sites.find((s) => !s.is_mock)?.id ?? sites[0]?.id ?? "", source_url: "", title: "", property_type: "", address: "", building_name: "", price_man: "", management_fee_yen: "", deposit_yen: "", key_money_yen: "", area_m2: "", layout: "", built_year: "" });
  const switchDeal = (d: Deal) => {
    setDeal(d);
    const keep = sites.find((s) => s.id === f.site_id)?.capabilities.deals.includes(d);
    setF({ ...f, property_type: "", site_id: keep ? f.site_id : (sites.find((s) => !s.is_mock && s.capabilities.deals.includes(d))?.id ?? "") });
  };
  const [err, setErr] = useState<string | null>(null);
  const set = (k: keyof typeof f, v: string) => setF({ ...f, [k]: v });
  const num = (v: string) => (v.trim() === "" ? null : Number(v));
  const submit = async () => {
    if (!f.source_url.trim()) { setErr(t("props.import_url_required")); return; }
    try {
      await api.post("/properties/manual-import", {
        site_id: f.site_id, source_url: f.source_url.trim(), title: f.title || null, property_type: f.property_type || null,
        address: f.address || null, building_name: f.building_name || null, price_man: num(f.price_man), area_m2: num(f.area_m2),
        layout: f.layout || null, built_year: num(f.built_year),
        ...(deal === "rent" ? {
          property_type: f.property_type || "rent_apartment",
          management_fee_yen: num(f.management_fee_yen), deposit_yen: num(f.deposit_yen), key_money_yen: num(f.key_money_yen),
        } : {}),
      });
      toast("success", t("props.imported"));
      onDone();
      onClose();
    } catch (e) {
      setErr(errText(e));
    }
  };
  return (
    <Modal title={t("props.manual_import")} onClose={onClose} wide
      footer={<><button className="btn" onClick={onClose}>{t("common.cancel")}</button><button className="btn btn-primary" onClick={() => void submit()}>{t("common.save")}</button></>}>
      <p className="muted">{t("props.import_help")}</p>
      <div className="segmented">
        {(["buy", "rent"] as const).map((d) => (
          <button key={d} type="button" className={deal === d ? "on" : ""} onClick={() => switchDeal(d)}>{t(`deal.${d}`)}</button>
        ))}
      </div>
      <div className="form-grid two">
        <Field label={t("props.col.sites")} required>
          <select value={f.site_id} onChange={(e) => set("site_id", e.target.value)}>{dealSites.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</select>
        </Field>
        <Field label={t("props.source_url")} required><input value={f.source_url} onChange={(e) => set("source_url", e.target.value)} placeholder="https://" /></Field>
        <Field label={t("props.title")}><input value={f.title} onChange={(e) => set("title", e.target.value)} /></Field>
        <Field label={t("cond.types")}>
          <select value={f.property_type} onChange={(e) => set("property_type", e.target.value)}>
            <option value="">—</option>{regions.deals[deal].map((p) => <option key={p} value={p}>{t(`ptype.${p}`)}</option>)}
          </select>
        </Field>
        <Field label={t("props.address")}><input value={f.address} onChange={(e) => set("address", e.target.value)} /></Field>
        <Field label={t("props.building")}><input value={f.building_name} onChange={(e) => set("building_name", e.target.value)} /></Field>
        <Field label={`${t(deal === "rent" ? "cond.rent" : "props.col.price")} (${t(deal === "rent" ? "unit.man_yen_month" : "unit.man_yen")})`}><input type="number" step={deal === "rent" ? 0.1 : 1} value={f.price_man} onChange={(e) => set("price_man", e.target.value)} /></Field>
        {deal === "rent" && <>
          <Field label={`${t("rent.mgmt_fee")} (${t("unit.yen")})`}><input type="number" value={f.management_fee_yen} onChange={(e) => set("management_fee_yen", e.target.value)} /></Field>
          <Field label={`${t("rent.deposit")} (${t("unit.yen")})`}><input type="number" value={f.deposit_yen} onChange={(e) => set("deposit_yen", e.target.value)} /></Field>
          <Field label={`${t("rent.key_money")} (${t("unit.yen")})`}><input type="number" value={f.key_money_yen} onChange={(e) => set("key_money_yen", e.target.value)} /></Field>
        </>}
        <Field label={`${t("props.col.area")} (㎡)`}><input type="number" value={f.area_m2} onChange={(e) => set("area_m2", e.target.value)} /></Field>
        <Field label={t("props.col.layout")}><input value={f.layout} onChange={(e) => set("layout", e.target.value)} /></Field>
        <Field label={t("props.built_year")}><input type="number" value={f.built_year} onChange={(e) => set("built_year", e.target.value)} /></Field>
      </div>
      {err && <p className="form-error">{err}</p>}
    </Modal>
  );
}
