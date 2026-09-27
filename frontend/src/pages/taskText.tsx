import type { Conditions, Schedule, ScheduleType } from "../api/types";
import { useI18n, type Params } from "../i18n";
import { useApp } from "../lib/app";

type T = (key: string, params?: Params) => string;

export function scheduleText(t: T, type: ScheduleType, s: Schedule): string {
  const wd = (s.weekdays ?? []).map((d) => t(`weekday.${d}`)).join("・");
  switch (type) {
    case "daily":
      return t("schedule.text.daily", { time: s.time ?? "08:00" });
    case "weekly":
      return t("schedule.text.weekly", { days: wd, time: s.time ?? "08:00" });
    case "interval":
      return t("schedule.text.interval", { h: s.interval_hours ?? 24 });
    case "startup":
      return t("schedule.text.startup");
    case "reminder":
      return t("schedule.text.reminder", { time: s.time ?? "08:00" });
    default:
      return t("schedule.text.manual");
  }
}

export function ConditionSummary({ conditions: c, short }: { conditions: Conditions; short?: boolean }) {
  const { t, lang } = useI18n();
  const { regions } = useApp();
  const pref = c.prefectures.map((p) => regions.prefectures.find((x) => x.code === p)?.name[lang] ?? p);
  const cities = c.cities.map((code) => regions.cities.find((x) => x.code === code)?.name[lang] ?? code);
  const parts: string[] = [];
  parts.push(cities.length ? cities.join(", ") : pref.join(", "));
  if (short) return <span>{parts[0] || "—"}</span>;
  const rent = c.deal_type === "rent";
  parts.push(c.transaction_type.length ? c.transaction_type.map((x) => t(`ptype.${x}`)).join("/") : t(`deal.${c.deal_type ?? "buy"}`));
  if (c.price_min != null || c.price_max != null) {
    parts.push(`${t(rent ? "cond.rent" : "cond.price")} ${c.price_min ?? ""}〜${c.price_max ?? ""}${t(rent ? "unit.man_yen_month" : "unit.man_yen")}${c.price_includes_fees && rent ? ` (${t("cond.incl_mgmt_short")})` : ""}`);
  }
  if (c.area_min != null || c.area_max != null) parts.push(`${t("cond.area")} ${c.area_min ?? ""}〜${c.area_max ?? ""}㎡`);
  if (c.layouts.length) parts.push(c.layouts.join("/"));
  if (c.building_age_max != null) parts.push(t("cond.age_within", { n: c.building_age_max }));
  if (c.walk_minutes_max != null) parts.push(t("cond.walk_within", { n: c.walk_minutes_max }));
  if (c.stations.length) parts.push(c.stations.map((s) => s.name).join("/"));
  if (c.keywords_include.length) parts.push(`+${c.keywords_include.join(",")}`);
  if (c.keywords_exclude.length) parts.push(`−${c.keywords_exclude.join(",")}`);
  return <span>{parts.join(" · ")}</span>;
}
