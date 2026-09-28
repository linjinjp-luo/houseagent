import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { translate } from "./index";
import en from "./locales/en";
import ja from "./locales/ja";
import zh from "./locales/zh";

function sourceFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) return name === "locales" ? [] : sourceFiles(p);
    return /\.tsx?$/.test(name) && !name.endsWith(".test.ts") ? [p] : [];
  });
}

const SRC = join(__dirname, "..");
// Families of enum values rendered via template keys, e.g. t(`ptype.${x}`).
const DYNAMIC: Record<string, string[]> = {
  ptype: ["used_mansion", "used_house", "land", "rent_apartment", "rent_house", "unknown"],
  deal: ["buy", "rent"],
  skip_reason: ["adapter_not_implemented", "deal_not_automated", "browser_automation", "rules_check_required", "assisted_only"],
  event: ["NEW", "PRICE_DOWN", "PRICE_UP", "NOT_FOUND", "UNAVAILABLE", "REAPPEARED", "SALE_ENDED"],
  obs: ["active", "not_found", "unavailable", "ended"],
  run_status: ["queued", "running", "completed", "paused", "failed", "cancelled", "interrupted"],
  login: ["not_logged_in", "valid", "expiring", "relogin_required", "check_failed"],
  perm_status: ["allowed", "denied", "unknown"],
  perm: ["browser_automation", "data_retention", "commercial_use", "image_storage", "api_access"],
  trigger: ["manual", "daily", "weekly", "interval", "startup", "catchup", "assisted"],
  level: ["info", "warning", "failure", "action_required"],
  field: ["transaction_type", "prefectures", "cities", "stations", "price_min", "price_max", "area_min", "area_max",
    "building_age_max", "walk_minutes_max", "layouts", "keywords_include", "keywords_exclude", "sort_order", "result_limit"],
  fav_status: ["watching", "planning_visit", "visited", "not_considering", "ended"],
  research_status: ["pending_research", "researched", "pending_visit", "ended"],
  schedule: ["manual", "daily", "weekly", "interval", "startup", "reminder"],
  "schedule.help": ["manual", "daily", "weekly", "interval", "startup", "reminder"],
  error: ["AUTH_REQUIRED", "PERMISSION_BLOCKED", "CAPTCHA_REQUIRED", "RATE_LIMITED", "NETWORK_ERROR", "PAGE_TIMEOUT",
    "PAGE_CHANGED", "CONDITION_UNSUPPORTED", "BROWSER_START_FAILED", "DATABASE_ERROR", "CANCELLED", "INTERRUPTED",
    "UNKNOWN_ERROR", "VALIDATION_ERROR", "NOT_FOUND", "CONFLICT", "UNAUTHORIZED", "READ_ONLY", "BACKEND_UNREACHABLE",
    "ai_disabled", "ai_not_configured", "ai_key_missing", "ai_daily_limit", "ai_rate_limited", "ai_auth", "ai_permission",
    "ai_model_not_found", "ai_invalid_request", "ai_timeout", "ai_network", "ai_redirect_blocked", "ai_server_error",
    "ai_refused", "ai_bad_response", "ai_unavailable", "insufficient_data", "not_requested"],
  "site_assist.mode": ["auto", "on", "off"],
  "site_assist.source": ["run", "probe"],
  // FR-11 / FR-12
  "inv.label": ["resale_candidate", "rental_candidate", "owner_candidate", "low_value", "insufficient_data"],
  "inv.tag": ["price_below_area", "unit_price_high", "interior_old", "move_in_ready", "renovation_cost_unconfirmed",
    "renovation_possible_needs_calc", "rental_yield_good", "holding_cost_high", "near_station", "old_building",
    "old_seismic_standard", "repair_risk", "low_liquidity", "data_insufficient"],
  "inv.conf": ["high", "medium", "low"],
  "inv.status": ["ai", "rules_only", "ai_rejected"],
  "inv.done": ["ai", "rules_only", "ai_rejected"],
  "inv.cond": ["poor", "average", "good", "renovated"],
  "inv.flag": ["structure", "title_rights", "location", "noise", "sunlight", "incident", "other"],
  "risk.flag": ["structure", "title_rights", "location", "noise", "sunlight", "incident", "other"],
  "inv.pref": ["city", "area", "layout", "walk", "commute"],
  "inv.outcome": ["candidate", "fail", "insufficient", "not_applicable"],
  "inv.batch_status": ["running", "completed", "cancelled", "paused_limit"],
  "inv.target": ["any", "resale", "rental", "owner"],
  "inv.target_help": ["any", "resale", "rental", "owner"],
  "inv.group": ["rental", "resale", "general", "assumptions", "owner"],
  "inv.field": ["monthly_rent_yen", "management_fee_monthly_yen", "repair_reserve_monthly_yen", "property_tax_annual_yen",
    "insurance_annual_yen", "renovation_budget_yen", "expected_sale_price_yen", "holding_months", "interior_condition",
    "commute_minutes", "comparable_unit_price_yen_m2", "risk_flags", "min_gross_yield_pct", "min_net_yield_pct",
    "min_resale_profit_yen", "min_resale_margin_pct", "resale_safety_margin_pct", "min_discount_pct", "max_price_premium_pct",
    "max_price_yen", "max_unit_price_yen_m2", "max_age_years", "max_renovation_budget_yen", "max_holding_months",
    "max_walk_minutes", "purchase_cost_rate_pct", "sale_cost_rate_pct", "vacancy_rate_pct", "leasing_cost_months_per_year",
    "unit_price_yen_m2", "price_vs_comparable_pct", "building_age_years", "annual_running_costs_yen", "purchase_costs_yen",
    "gross_yield_pct", "effective_annual_rent_yen", "net_yield_pct", "running_cost_share_pct", "holding_costs_yen",
    "sale_costs_yen", "resale_profit_yen", "resale_margin_pct", "price_yen", "area_m2", "built_year", "walk_minutes"],
  reason: ["resale_not_old", "resale_needs_calc", "resale_discount_short", "resale_profit_short", "resale_margin_short",
    "resale_blocking_risk", "resale_ok", "rental_gross_short", "rental_net_unknown", "rental_net_short", "rental_ok",
    "owner_price_high", "over_max_price", "over_max_unit_price", "owner_renovation_over_budget", "owner_preferences",
    "owner_good_but_expensive", "owner_ok"],
  risk: ["holding_cost_high", "far_from_station", "old_building", "old_seismic", "age_over_max"],
  "ai.type": ["openai", "anthropic", "compatible"],
  "ai.store": ["windows-dpapi", "macos-keychain", "unavailable"],
  "ai.check": ["address", "auth", "model", "structured_output"],
  "ai.limit": ["timeout_s", "max_retries", "daily_call_limit", "daily_cost_limit", "batch_max", "concurrency"],
  "ai.purpose": ["assessment", "test", "summary"],
  "ai_field": ["property_type", "prefecture", "city", "address", "price_yen", "area_m2", "land_area_m2", "layout", "floor",
    "built_year", "station", "walk_minutes", "title"],
  notice: ["reminder", "login_required", "permission_required", "captcha", "rate_limited", "page_changed", "task_paused", "run_interrupted"],
};
DYNAMIC.error_short = DYNAMIC.error.filter((e) => e === e.toUpperCase());  // run error codes only
DYNAMIC.notice_kind = DYNAMIC.notice;

describe("i18n", () => {
  it("all three languages define exactly the same keys", () => {
    const jaKeys = Object.keys(ja).sort();
    expect(Object.keys(zh).sort()).toEqual(jaKeys);
    expect(Object.keys(en).sort()).toEqual(jaKeys);
  });

  it("every literal key used in the UI exists", () => {
    const missing: string[] = [];
    for (const f of sourceFiles(SRC)) {
      const text = readFileSync(f, "utf-8");
      for (const m of text.matchAll(/\bt\("([a-zA-Z0-9_.]+)"/g)) if (!(m[1] in ja)) missing.push(`${m[1]} (${f})`);
      for (const m of text.matchAll(/(?:key|okKey|messageKey): "([a-z_]+\.[a-zA-Z0-9_.]+)"/g)) if (!(m[1] in ja)) missing.push(m[1]);
      for (const m of text.matchAll(/"(wizard\.step\.[a-z]+|menu\.[a-z]+|tasks\.[a-z_]+|sites\.[a-z_]+|logs\.[a-z_]+)"/g)) {
        if (!(m[1] in ja)) missing.push(`${m[1]} (${f})`);
      }
    }
    expect(missing).toEqual([]);
  });

  it("every enum value family is translated", () => {
    const missing = Object.entries(DYNAMIC).flatMap(([prefix, values]) =>
      values.map((v) => `${prefix}.${v}`).filter((k) => !(k in ja)));
    expect(missing).toEqual([]);
  });

  it("every backend message key is translated", () => {
    const backend = join(SRC, "..", "..", "backend", "houseagent");
    const missing: string[] = [];
    for (const f of readdirSync(backend, { recursive: true }) as string[]) {
      if (!f.endsWith(".py")) continue;
      const text = readFileSync(join(backend, f), "utf-8");
      for (const m of text.matchAll(/"((?:error|log|notice|condition|stats|site)\.[a-zA-Z_.]+)"/g)) {
        if (!(m[1] in ja)) missing.push(`${m[1]} (${f})`);
      }
    }
    expect(missing).toEqual([]);
  });

  it("interpolates and falls back to Japanese then the key", () => {
    expect(translate("en", "common.total_count", { n: 3 })).toBe("3 total");
    expect(translate("zh", "no.such.key")).toBe("no.such.key");
  });
});
