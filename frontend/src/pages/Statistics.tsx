import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api } from "../api/client";
import type { Deal, Stats, Task } from "../api/types";
import { Field, PageHeader, Stat, StateView } from "../components/common";
import { useI18n } from "../i18n";
import { useApp } from "../lib/app";
import { addDays, formatDate, formatDateTime, formatNumber, formatPrice, todayIn } from "../lib/format";
import { useLoad } from "../lib/hooks";

// Categorical slots in fixed order (never cycled); colours are CSS tokens with separate dark steps.
const SERIES = ["var(--series-1)", "var(--series-2)", "var(--series-3)"];
const AXIS = { fontSize: 12, stroke: "var(--text-muted)", tickLine: false } as const;
const GRID = <CartesianGrid vertical={false} stroke="var(--grid)" />;
const TOOLTIP = <Tooltip cursor={{ fill: "var(--hover)" }} contentStyle={{ background: "var(--surface)", border: "1px solid var(--border)", borderRadius: 8, color: "var(--text)" }} />;

function ChartCard({ title, meta, children, table }: { title: string; meta: string; children: React.ReactNode; table: React.ReactNode }) {
  const { t } = useI18n();
  const [asTable, setAsTable] = useState(false);
  return (
    <section className="card">
      <div className="card-title-row">
        <h2 className="card-title">{title}</h2>
        <button className="btn btn-sm btn-ghost" onClick={() => setAsTable(!asTable)}>{asTable ? t("stats.show_chart") : t("stats.show_table")}</button>
      </div>
      <p className="muted small">{meta}</p>
      {asTable ? <div className="table-wrap">{table}</div> : <div className="chart-box">{children}</div>}
    </section>
  );
}

export default function Statistics() {
  const { t, lang } = useI18n();
  const { settings, sites, regions, siteName } = useApp();
  const nav = useNavigate();
  const tz = settings.timezone;
  const today = todayIn(tz);
  const [f, setF] = useState({ deal_type: "buy" as Deal, date_from: addDays(today, -29), date_to: today, task_id: "", site_id: "", prefecture: "", property_type: "" });
  const deal = f.deal_type;
  const tasks = useLoad(() => api.get<Task[]>("/search-tasks", { include_deleted: true }), []);
  const { data, error, loading, reload } = useLoad(() => api.get<Stats>("/statistics", f), [JSON.stringify(f)]);

  const drillBase = () => {
    const p = new URLSearchParams();
    p.set("deal_type", deal);
    if (f.task_id) p.set("task_id", f.task_id);
    if (f.site_id) p.set("site_id", f.site_id);
    if (f.prefecture) p.set("prefecture", f.prefecture);
    if (f.property_type) p.set("property_type", f.property_type);
    return p;
  };
  const drill = (event: string, date?: string) => {
    const p = drillBase();
    p.set("event", event);
    if (date) p.set("event_date", date);
    nav(`/properties?${p.toString()}`);
  };

  const meta = (n: number) => t("stats.meta", { at: data ? formatDateTime(data.generated_at, lang, tz) : "—", n });
  const daily = (data?.daily ?? []).map((d) => ({ ...d, label: formatDate(d.date, lang, tz).slice(5) }));
  const empty = !!data && data.sample_size === 0 && data.runs.total === 0;

  return (
    <div className="page">
      <PageHeader title={t("menu.statistics")} description={t("stats.desc")} />
      <div className="segmented deal-switch">
        {(["buy", "rent"] as const).map((d) => (
          <button key={d} type="button" className={deal === d ? "on" : ""} onClick={() => setF({ ...f, deal_type: d, property_type: "" })}>{t(`deal.${d}`)}</button>
        ))}
      </div>
      <div className="filter-bar">
        <Field label={t("stats.from")}><input type="date" value={f.date_from} onChange={(e) => setF({ ...f, date_from: e.target.value })} /></Field>
        <Field label={t("stats.to")}><input type="date" value={f.date_to} onChange={(e) => setF({ ...f, date_to: e.target.value })} /></Field>
        <Field label={t("menu.tasks")}>
          <select value={f.task_id} onChange={(e) => setF({ ...f, task_id: e.target.value })}>
            <option value="">{t("common.all")}</option>
            {(tasks.data ?? []).map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}
          </select>
        </Field>
        <Field label={t("props.col.sites")}>
          <select value={f.site_id} onChange={(e) => setF({ ...f, site_id: e.target.value })}>
            <option value="">{t("common.all")}</option>{sites.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
        </Field>
        <Field label={t("cond.prefectures")}>
          <select value={f.prefecture} onChange={(e) => setF({ ...f, prefecture: e.target.value })}>
            <option value="">{t("common.all")}</option>{regions.prefectures.map((p) => <option key={p.code} value={p.code}>{p.name[lang]}</option>)}
          </select>
        </Field>
        <Field label={t("cond.types")}>
          <select value={f.property_type} onChange={(e) => setF({ ...f, property_type: e.target.value })}>
            <option value="">{t("common.all")}</option>{regions.deals[deal].map((p) => <option key={p} value={p}>{t(`ptype.${p}`)}</option>)}
          </select>
        </Field>
      </div>
      <p className="hint-box">{t("stats.scope_disclaimer")}</p>
      <StateView loading={loading} error={error} onRetry={reload} empty={empty} emptyText={<><p>{t("stats.no_data")}</p><Link className="btn" to="/tasks">{t("menu.tasks")}</Link></>}>
        {data && (
          <>
            <div className="stat-grid">
              <Stat label={t("stats.total_new")} value={data.totals.new} onClick={() => drill("NEW")} />
              <Stat label={t("stats.observable")} value={data.current_observable} onClick={() => nav(`/properties?status=active&${drillBase().toString()}`)} />
              <Stat label={t("stats.price_down")} value={data.totals.price_down} onClick={() => drill("PRICE_DOWN")} />
              <Stat label={t("stats.price_up")} value={data.totals.price_up} onClick={() => drill("PRICE_UP")} />
              <Stat label={t("stats.avg_down")} value={data.totals.avg_down_yen ? `${formatPrice(data.totals.avg_down_yen, lang, deal)} (${formatNumber(data.totals.avg_down_pct, lang, 1)}%)` : "—"} />
              <Stat label={t("stats.success_rate")} value={data.runs.success_rate != null ? `${data.runs.success_rate}%` : "—"} onClick={() => nav("/logs")} />
              <Stat label={t("stats.overlap")} value={data.cross_site.overlapping_listings} onClick={() => nav("/properties?tab=matches")} />
            </div>

            <ChartCard title={t("stats.daily_title")} meta={meta(data.sample_size)}
              table={<table className="table"><thead><tr><th>{t("stats.date")}</th><th>{t("event.NEW")}</th><th>{t("event.PRICE_DOWN")}</th><th>{t("event.PRICE_UP")}</th><th>{t("event.NOT_FOUND")}</th><th>{t("event.REAPPEARED")}</th></tr></thead>
                <tbody>{data.daily.filter((d) => d.new + d.price_down + d.price_up + d.not_found + d.reappeared > 0).map((d) => (
                  <tr key={d.date}><td>{d.date}</td>
                    <td><button className="link" onClick={() => drill("NEW", d.date)}>{d.new}</button></td>
                    <td><button className="link" onClick={() => drill("PRICE_DOWN", d.date)}>{d.price_down}</button></td>
                    <td><button className="link" onClick={() => drill("PRICE_UP", d.date)}>{d.price_up}</button></td>
                    <td><button className="link" onClick={() => drill("NOT_FOUND", d.date)}>{d.not_found}</button></td>
                    <td>{d.reappeared}</td></tr>))}</tbody></table>}>
              <ResponsiveContainer width="100%" height={260}>
                <BarChart data={daily} barGap={2} onClick={(s) => { const p = (s as { activePayload?: { payload: { date: string } }[] })?.activePayload?.[0]?.payload; if (p) drill("NEW", p.date); }}>
                  {GRID}
                  <XAxis dataKey="label" {...AXIS} interval="preserveStartEnd" />
                  <YAxis {...AXIS} allowDecimals={false} width={32} />
                  {TOOLTIP}
                  <Legend itemSorter="dataKey" formatter={(v: string) => <span style={{ color: "var(--text-2)" }}>{v}</span>} />
                  <Bar dataKey="new" name={t("event.NEW")} fill={SERIES[0]} radius={[4, 4, 0, 0]} maxBarSize={14} />
                  <Bar dataKey="price_down" name={t("event.PRICE_DOWN")} fill={SERIES[1]} radius={[4, 4, 0, 0]} maxBarSize={14} />
                  <Bar dataKey="price_up" name={t("event.PRICE_UP")} fill={SERIES[2]} radius={[4, 4, 0, 0]} maxBarSize={14} />
                </BarChart>
              </ResponsiveContainer>
              <p className="muted small">{t("stats.click_drill")}</p>
            </ChartCard>

            <div className="grid-2">
              <ChartCard title={t(deal === "rent" ? "stats.rent_dist" : "stats.price_dist")} meta={meta(data.current_observable)}
                table={<table className="table"><tbody>{data.price.distribution.map((b) => <tr key={b.bucket}><td>{b.bucket}{t(deal === "rent" ? "unit.man_yen_month" : "unit.man_yen")}</td><td>{b.count}</td></tr>)}</tbody></table>}>
                <ResponsiveContainer width="100%" height={220}>
                  <BarChart data={data.price.distribution}>
                    {GRID}<XAxis dataKey="bucket" {...AXIS} /><YAxis {...AXIS} allowDecimals={false} width={32} />{TOOLTIP}
                    <Bar dataKey="count" name={t("stats.count")} fill={SERIES[0]} radius={[4, 4, 0, 0]} maxBarSize={28} />
                  </BarChart>
                </ResponsiveContainer>
                <p className="muted small">{t("stats.price_range", { min: formatPrice(data.price.min, lang, deal), med: formatPrice(data.price.median, lang, deal), max: formatPrice(data.price.max, lang, deal) })}</p>
              </ChartCard>
              <ChartCard title={t("stats.area_dist")} meta={meta(data.current_observable)}
                table={<table className="table"><tbody>{data.area.distribution.map((b) => <tr key={b.bucket}><td>{b.bucket}㎡</td><td>{b.count}</td></tr>)}</tbody></table>}>
                <ResponsiveContainer width="100%" height={220}>
                  <BarChart data={data.area.distribution}>
                    {GRID}<XAxis dataKey="bucket" {...AXIS} /><YAxis {...AXIS} allowDecimals={false} width={32} />{TOOLTIP}
                    <Bar dataKey="count" name={t("stats.count")} fill={SERIES[0]} radius={[4, 4, 0, 0]} maxBarSize={28} />
                  </BarChart>
                </ResponsiveContainer>
              </ChartCard>
            </div>

            <div className="grid-2">
              <section className="card">
                <h2 className="card-title">{t("stats.by_site_type")}</h2>
                <p className="muted small">{meta(data.current_observable)}</p>
                <table className="table">
                  <tbody>
                    {data.by_site.map((s) => <tr key={s.site_id}><td>{siteName(s.site_id)}</td><td><Bar1 v={s.count} max={data.current_observable} /></td><td className="num">{s.count}</td></tr>)}
                    {data.by_type.map((s) => <tr key={s.property_type}><td>{t(`ptype.${s.property_type}`)}</td><td><Bar1 v={s.count} max={data.current_observable} /></td><td className="num">{s.count}</td></tr>)}
                  </tbody>
                </table>
                <h3>{t("stats.by_city")}</h3>
                <table className="table"><tbody>{data.by_city.map((c) => <tr key={c.city}><td>{regions.cities.find((x) => x.code === c.city)?.name[lang] ?? c.city}</td><td><Bar1 v={c.count} max={data.current_observable} /></td><td className="num">{c.count}</td></tr>)}</tbody></table>
              </section>
              <section className="card">
                <h2 className="card-title">{t("stats.task_success")}</h2>
                <p className="muted small">{meta(data.runs.total)}</p>
                {data.tasks.length === 0 ? <p className="muted">{t("state.empty")}</p> : (
                  <table className="table">
                    <thead><tr><th>{t("tasks.col.name")}</th><th>{t("stats.runs")}</th><th>{t("run_status.completed")}</th><th>{t("run_status.failed")}</th><th>{t("run_status.paused")}</th><th>{t("stats.success_rate")}</th></tr></thead>
                    <tbody>{data.tasks.map((x) => (
                      <tr key={x.task_id}><td><Link to={`/logs?task_id=${x.task_id}`}>{x.name}</Link></td><td>{x.total}</td><td>{x.completed}</td><td>{x.failed}</td><td>{x.paused}</td><td>{x.success_rate != null ? `${x.success_rate}%` : "—"}</td></tr>))}
                    </tbody>
                  </table>
                )}
                <h3>{t("stats.errors")}</h3>
                {data.errors.length === 0 ? <p className="muted">{t("stats.no_errors")}</p> : (
                  <table className="table"><tbody>{data.errors.map((e) => <tr key={e.error_code}><td><Link to={`/logs?status=failed,paused`}>{t(`error_short.${e.error_code}`)}</Link> <span className="muted mono small">{e.error_code}</span></td><td className="num">{e.count}</td></tr>)}</tbody></table>
                )}
              </section>
            </div>
          </>
        )}
      </StateView>
    </div>
  );
}

/** Inline magnitude bar for breakdown tables (one hue; the number beside it carries the value). */
function Bar1({ v, max }: { v: number; max: number }) {
  return <span className="inline-bar"><span style={{ width: `${max ? Math.max(2, (v / max) * 100) : 0}%` }} /></span>;
}
