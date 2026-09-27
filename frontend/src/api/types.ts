export type Lang = "ja" | "zh" | "en";
export type Deal = "buy" | "rent";
export type Localized = Record<Lang, string>;

export interface Settings {
  language: Lang;
  timezone: string;
  startup_open_browser: boolean;
  tray_enabled: boolean;
  close_behavior: "tray" | "exit";
  allowed_window_start: string;
  allowed_window_end: string;
  network_retry_enabled: boolean;
  backup_auto_daily: boolean;
  backup_keep: number;
  notifications_in_app: boolean;
  ai_enabled: boolean;
  ai_provider: string;
  ai_model: string;
  ai_allowed_fields: string[];
  ai_send_notes: boolean;
  log_level: string;
  data_retention_days: number;
  onboarding_done: boolean;
  [key: string]: unknown;
}

export interface Regions {
  prefectures: { code: string; name: Localized }[];
  cities: { code: string; prefecture: string; name: Localized }[];
  property_types: string[];
  deals: Record<Deal, string[]>;
  layouts: string[];
  sort_orders: string[];
}

export type PermissionStatus = "allowed" | "denied" | "unknown";
export interface PermissionInfo {
  status: PermissionStatus;
  source: string | null;
  reviewed_at: string | null;
  note: string | null;
}
export type FieldSupport = "supported" | "local_filter" | "unsupported";

export interface Site {
  id: string;
  name: string;
  base_url: string;
  enabled: boolean;
  adapter_status: "ok" | "stopped";
  adapter_diagnostic: Record<string, string> | null;
  permissions: Record<string, PermissionInfo>;
  capabilities: {
    site_id: string;
    property_types: string[];
    fields: Record<string, FieldSupport>;
    retention_fields: string[];
    login_required: boolean;
    max_pages: number;
    notes_key: string | null;
    automation_available: boolean;
    approximate_fields?: string[];
    min_page_interval_s?: number;
    deals: Deal[];
    links: Partial<Record<Deal, string>>;
    /** Effective: offered on the task screens (dedicated reader, mode "on", or verification detected in "auto"). */
    assisted: boolean;
    dedicated_assisted?: boolean;
  };
  account_count: number;
  is_mock: boolean;
  is_custom: boolean;
  assisted_mode: "auto" | "on" | "off";
  verification_detected_at: string | null;
  verification_probe: { checked_at: string; source: "run" | "probe"; detected: boolean; url?: string | null; signal?: string | null; reachable?: boolean } | null;
  rules_check_available: boolean;
}

export interface RulesCheck {
  id: number;
  site_id: string;
  checked_at: string;
  status: "ok" | "warning" | "unreachable";
  robots_url: string | null;
  robots_findings: { path: string; purpose: string; allowed: boolean }[];
  terms_findings: { url: string; category: string; keyword: string; snippet: string }[];
  fetch_errors: { url: string; error: string }[];
  accepted_at: string | null;
  terms_urls: string[];
}

export type LoginStatus = "not_logged_in" | "valid" | "expiring" | "relogin_required" | "check_failed";

export interface Account {
  id: number;
  site_id: string;
  site_name: string;
  account_alias: string;
  profile_path: string;
  login_status: LoginStatus;
  auto_search_enabled: boolean;
  last_checked_at: string | null;
  note: string | null;
  permissions: Record<string, PermissionInfo>;
  task_count: number;
  busy: boolean;
  login_window: string | null;
  created_at: string;
}

export interface Station {
  line_code?: string | null;
  station_code?: string | null;
  name: string;
}

export interface Conditions {
  deal_type: Deal;
  transaction_type: string[];
  prefectures: string[];
  cities: string[];
  stations: Station[];
  price_min: number | null;
  price_max: number | null;
  price_includes_fees: boolean;
  area_min: number | null;
  area_max: number | null;
  building_age_max: number | null;
  walk_minutes_max: number | null;
  layouts: string[];
  keywords_include: string[];
  keywords_exclude: string[];
  sort_order: string | null;
  result_limit: number;
  region_logic: "AND" | "OR";
}

export interface Schedule {
  time?: string;
  weekdays?: number[];
  interval_hours?: number;
  start_date?: string | null;
  end_date?: string | null;
  window_start?: string | null;
  window_end?: string | null;
  min_interval_hours?: number;
}

export type ScheduleType = "manual" | "daily" | "weekly" | "interval" | "startup" | "reminder";
export type Priority = "high" | "normal" | "low";

export interface TaskSite {
  site_id: string;
  account_id: number | null;
  account_alias?: string | null;
  site_specific_config?: Record<string, unknown>;
}

export interface RunProgressFields {
  progress_stage: string;
  progress_pct: number;
  wait_reason: string | null;
  error_detail: string | null;
  expected_total?: number | null;
}

export interface RunSummary extends RunProgressFields {
  id: number;
  run_no: string;
  site_id: string;
  status: RunStatus;
  error_code: string | null;
  result_count: number;
  new_count: number;
  changed_count: number;
  finished_at: string | null;
}

export interface Task {
  id: number;
  name: string;
  description: string | null;
  status: "draft" | "active" | "paused";
  priority: Priority;
  schedule_type: ScheduleType;
  schedule: Schedule;
  timezone: string;
  condition_version: number;
  conditions: Conditions;
  sites: TaskSite[];
  next_run_at: string | null;
  last_run_at: string | null;
  last_success_at: string | null;
  consecutive_failures: number;
  paused_reason: string | null;
  active_runs: number;
  last_run: RunSummary | null;
  last_runs: RunSummary[];
  versions?: { version: number; conditions: Conditions; created_at: string }[];
  created_at: string;
  updated_at: string;
}

export interface Validation {
  submit: Record<string, unknown>;
  local_filters: string[];
  unsupported: string[];
  messages: { key: string; params: Record<string, unknown> }[];
  manual_only?: boolean;
}

export type RunStatus = "queued" | "running" | "completed" | "paused" | "failed" | "cancelled" | "interrupted";

export interface Run extends RunProgressFields {
  id: number;
  run_no: string;
  task_id: number;
  task_name: string | null;
  site_id: string;
  account_id: number | null;
  account_alias: string | null;
  condition_version: number;
  trigger_type: string;
  status: RunStatus;
  queued_at: string;
  not_before: string | null;
  started_at: string | null;
  finished_at: string | null;
  attempt: number;
  retry_count: number;
  error_code: string | null;
  correlation_id: string | null;
  cancel_requested: boolean;
  cancel_reason: string | null;
  result_count: number;
  new_count: number;
  changed_count: number;
  skipped_count: number;
  not_found_count: number;
  pages: number;
  unsupported_conditions: string[];
}

export interface RunDetail extends Run {
  conditions: Conditions | null;
  logs: { level: string; message_key: string; params: Record<string, unknown>; created_at: string }[];
  results: { source_id: number; rank: number; is_new: boolean; is_changed: boolean }[];
}

export interface Source {
  id: number;
  listing_id: number;
  site_id: string;
  external_listing_id: string;
  source_url: string;
  deal_type: Deal;
  management_fee_yen: number | null;
  deposit_yen: number | null;
  key_money_yen: number | null;
  title: string | null;
  property_type: string | null;
  prefecture: string | null;
  city: string | null;
  address: string | null;
  building_name: string | null;
  price_yen: number | null;
  area_m2: number | null;
  land_area_m2: number | null;
  layout: string | null;
  floor: number | null;
  built_year: number | null;
  station: string | null;
  walk_minutes: number | null;
  observation_status: "active" | "not_found" | "unavailable" | "ended";
  url_status: "ok" | "unreachable";
  first_seen_at: string;
  last_seen_at: string;
  last_checked_at: string;
  manual_import: boolean;
}

export interface FavoriteInfo {
  status: string;
  research_status: string;
  visit_date: string | null;
}

export interface Listing {
  id: number;
  deal_type: Deal;
  title: string | null;
  property_type: string | null;
  prefecture: string | null;
  city: string | null;
  address: string | null;
  building_name: string | null;
  layout: string | null;
  area_m2: number | null;
  land_area_m2: number | null;
  floor: number | null;
  built_year: number | null;
  price_yen: number | null;
  current_status: string;
  review_status: string;
  first_seen_at: string | null;
  last_seen_at: string | null;
  site_ids: string[];
  sources: Source[];
  tags: string[];
  favorite: FavoriteInfo | null;
  last_event: { event_type: string; created_at: string; old_price_yen: number | null; new_price_yen: number | null } | null;
  note_count?: number;
}

export interface Note {
  id: number;
  listing_id: number;
  kind: "note" | "research";
  body: string;
  fields: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface MatchCandidate {
  id: number;
  score: number;
  reasons: string[];
  status: string;
  created_at: string;
  listing_a: Listing | null;
  listing_b: Listing | null;
}

export interface ListingDetail extends Listing {
  notes: Note[];
  match_candidates: MatchCandidate[];
  found_by: { task_id: number; condition_version: number; first_run_id: number }[];
}

export interface History {
  events: {
    id: number;
    event_type: string;
    source_id: number;
    site_id: string;
    run_id: number | null;
    run_no: string | null;
    task_id: number | null;
    old_price_yen: number | null;
    new_price_yen: number | null;
    evidence: string | null;
    created_at: string;
  }[];
  snapshots: {
    id: number;
    source_id: number;
    site_id: string;
    run_id: number | null;
    captured_at: string;
    price_yen: number | null;
    status: string;
  }[];
}

export interface Paged<T> {
  total: number;
  items: T[];
  page?: number;
  page_size?: number;
}

export interface Notice {
  id: number;
  kind: string;
  message_key: string;
  task_id: number | null;
  run_id: number | null;
  site_id: string | null;
  account_id: number | null;
  params: Record<string, unknown>;
  created_at: string;
  resolved_at: string | null;
}

export interface Dashboard {
  last_success_at: string | null;
  next_run_at: string | null;
  cards: { new: number; price_down: number; price_up: number; reappeared: number; pending_matches: number; failed: number };
  queue: { run_id: number; run_no: string; task_id: number; task_name: string | null; site_id: string; status: string; queued_at: string; started_at: string | null; progress_stage: string; progress_pct: number; wait_reason: string | null; result_count: number }[];
  attention: Notice[];
  activity: {
    kind: "event" | "run" | "favorite";
    at: string;
    event_type?: string;
    listing_id?: number;
    title?: string | null;
    site_id?: string;
    old_price_yen?: number | null;
    new_price_yen?: number | null;
    deal_type?: Deal;
    run_id?: number;
    run_no?: string;
    status?: string;
    task_id?: number;
    new_count?: number;
    error_code?: string | null;
  }[];
  task_count: number;
}

export interface Stats {
  scope_note_key: string;
  generated_at: string;
  filters: Record<string, string | number | null>;
  sample_size: number;
  current_observable: number;
  daily: { date: string; new: number; price_down: number; price_up: number; not_found: number; reappeared: number; unavailable: number }[];
  totals: { new: number; price_down: number; price_up: number; avg_down_yen: number | null; avg_up_yen: number | null; avg_down_pct: number | null };
  price: { min: number | null; max: number | null; median: number | null; distribution: { bucket: string; count: number }[] };
  area: { min: number | null; max: number | null; distribution: { bucket: string; count: number }[] };
  by_site: { site_id: string; count: number }[];
  by_type: { property_type: string; count: number }[];
  by_city: { city: string; count: number }[];
  cross_site: { overlapping_listings: number; pending_candidates: number };
  tasks: { task_id: number; name: string; total: number; completed: number; failed: number; paused: number; cancelled: number; interrupted: number; success_rate: number | null }[];
  runs: { total: number; completed: number; success_rate: number | null };
  errors: { error_code: string; count: number }[];
}

export interface SystemInfo {
  version: string;
  data_dir: string;
  database: string;
  profiles_dir: string;
  logs_dir: string;
  backups_dir: string;
  port: number;
  read_only: string | null;
  browser_engine: string;
  env: string;
  ai_key_stored: boolean;
  queue: { running: number[]; max_parallel: number; accepting: boolean };
}

export interface Backup {
  name: string;
  size: number;
  created_at: string;
}

export interface AssistedImportResult {
  run_id: number;
  run_no: string;
  url: string;
  items: number;
  kept: number;
  new: number;
  changed: number;
  total: number | null;
  next_url: string | null;
  at: string;
}

export interface AssistedSession {
  id: string;
  task_id: number;
  site_id: string;
  open: boolean;
  start_urls: { property_type: string; url: string }[];
  imports: AssistedImportResult[];
  next_url: string | null;
}
