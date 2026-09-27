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
    "UNKNOWN_ERROR", "VALIDATION_ERROR", "NOT_FOUND", "CONFLICT", "UNAUTHORIZED", "READ_ONLY", "BACKEND_UNREACHABLE"],
  "site_assist.mode": ["auto", "on", "off"],
  "site_assist.source": ["run", "probe"],
  notice: ["reminder", "login_required", "permission_required", "captcha", "rate_limited", "page_changed", "task_paused", "run_interrupted"],
};
DYNAMIC.error_short = DYNAMIC.error;
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
