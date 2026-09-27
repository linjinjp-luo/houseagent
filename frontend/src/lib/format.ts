import type { Deal, Lang } from "../api/types";
import { LOCALE } from "../i18n";

// Prices are stored in yen. Japanese / Chinese users read them in 万円; English shows full yen.
// For rentals the price is the monthly rent.
export function formatPrice(yen: number | null | undefined, lang: Lang, deal: Deal = "buy"): string {
  if (yen === null || yen === undefined) return "—";
  const perMonth = deal === "rent" ? { ja: "/月", zh: "/月", en: "/mo" }[lang] : "";
  if (lang === "en") return new Intl.NumberFormat("en-US", { style: "currency", currency: "JPY" }).format(yen) + perMonth;
  const man = yen / 10_000;
  const n = new Intl.NumberFormat(LOCALE[lang], { maximumFractionDigits: deal === "rent" ? 2 : 1 }).format(man);
  return (lang === "zh" ? `${n}万日元` : `${n}万円`) + perMonth;
}

/** Plain yen amount (management fee, deposit, key money). */
export function formatYen(yen: number | null | undefined, lang: Lang): string {
  if (yen === null || yen === undefined) return "—";
  const n = new Intl.NumberFormat(LOCALE[lang], { maximumFractionDigits: 0 }).format(yen);
  return lang === "en" ? `¥${n}` : lang === "zh" ? `${n}日元` : `${n}円`;
}

export function formatPriceDiff(oldYen: number | null | undefined, newYen: number | null | undefined, lang: Lang): string {
  if (oldYen == null || newYen == null) return "";
  const diff = newYen - oldYen;
  const sign = diff > 0 ? "+" : diff < 0 ? "−" : "±";
  const pct = oldYen ? ` (${sign}${Math.abs((diff / oldYen) * 100).toFixed(1)}%)` : "";
  return `${sign}${formatPrice(Math.abs(diff), lang)}${pct}`;
}

export function formatArea(m2: number | null | undefined, lang: Lang): string {
  if (m2 === null || m2 === undefined) return "—";
  return `${new Intl.NumberFormat(LOCALE[lang], { maximumFractionDigits: 1 }).format(m2)}㎡`;
}

export function formatNumber(n: number | null | undefined, lang: Lang, digits = 0): string {
  if (n === null || n === undefined) return "—";
  return new Intl.NumberFormat(LOCALE[lang], { maximumFractionDigits: digits }).format(n);
}

export function formatDateTime(iso: string | null | undefined, lang: Lang, tz: string): string {
  if (!iso) return "—";
  return new Intl.DateTimeFormat(LOCALE[lang], {
    timeZone: tz,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(iso));
}

export function formatDate(iso: string | null | undefined, lang: Lang, tz: string): string {
  if (!iso) return "—";
  const d = iso.length === 10 ? new Date(`${iso}T00:00:00`) : new Date(iso);
  return new Intl.DateTimeFormat(LOCALE[lang], {
    timeZone: iso.length === 10 ? undefined : tz,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(d);
}

export function todayIn(tz: string): string {
  return new Intl.DateTimeFormat("sv-SE", { timeZone: tz }).format(new Date());
}

export function addDays(isoDate: string, days: number): string {
  const d = new Date(`${isoDate}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

/** Mock-site links are relative to this app's origin; real sites are absolute. */
export function sourceHref(url: string): string {
  return url.startsWith("/") ? window.location.origin + url : url;
}
