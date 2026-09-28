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
  assess_after_run: boolean;
  assess_profile_id: number | null;
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
  investment?: InvestmentSummary | null;
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

// ---- AI services (FR-12) and investment assessment (FR-11) ---------------------------------------------

export type ProviderType = "openai" | "anthropic" | "compatible";
export interface AIPricing { input_per_mtok?: number; output_per_mtok?: number; currency?: string; updated_at?: string }
export interface AILimits {
  timeout_s: number;
  max_retries: number;
  daily_call_limit: number | null;
  daily_cost_limit: number | null;
  batch_max: number;
  concurrency: number;
}
export interface AIProvider {
  id: number;
  provider_type: ProviderType;
  display_name: string;
  base_url: string | null;
  custom_url: boolean;
  model_id: string;
  enabled: boolean;
  active: boolean;
  /** configured | env | missing - the key itself never reaches the frontend */
  key_state: "configured" | "env" | "missing";
  key_last4: string | null;
  limits: AILimits;
  allowed_fields: string[];
  pricing: AIPricing;
  last_test_at: string | null;
  last_test_status: string | null;
  last_test_detail: Record<string, unknown> | null;
}
export interface AIProvidersInfo {
  enabled: boolean;
  active_id: number | null;
  items: AIProvider[];
  recommended: Record<ProviderType, { id: string; pricing: AIPricing; recommended?: boolean }[]>;
  sendable_fields: string[];
  default_limits: AILimits;
  secret_store: string;
  secret_store_available: boolean;
  allowed_hosts: string[];
}
export interface AITestResult {
  ok: boolean;
  code?: string;
  message_key?: string;
  duration_ms?: number;
  model_version?: string;
  checks?: Record<string, string>;
  provider: AIProvider;
}
export interface AIUsage {
  days: number;
  groups: { provider_config_id: number | null; model_id: string; purpose: string; status: string; calls: number;
    input_units: number; output_units: number; estimated_cost: number | null; currency: string | null }[];
  today: Record<string, { calls: number; cost: number; limits: AILimits }>;
  recent: { id: number; requested_at: string; provider_config_id: number | null; model_id: string; purpose: string;
    status: string; input_units: number | null; output_units: number | null; estimated_cost: number | null;
    currency: string | null; duration_ms: number | null; correlation_id: string }[];
}

export type InvLabel = "resale_candidate" | "rental_candidate" | "owner_candidate" | "low_value" | "insufficient_data";
export type InvTarget = "any" | "resale" | "rental" | "owner";
export interface InvestmentProfile {
  id: number;
  name: string;
  target_type: InvTarget;
  thresholds: Record<string, number>;
  assumptions: Record<string, number>;
  preferences: { cities?: string[]; layouts?: string[]; min_area_m2?: number; max_commute_minutes?: number };
  is_default: boolean;
  updated_at: string;
}
export interface InvestmentVocabulary {
  targets: InvTarget[];
  thresholds: Record<string, string>;
  assumptions: Record<string, string>;
  labels: InvLabel[];
  tags: string[];
  inputs: Record<string, string>;
  conditions: string[];
  risk_flags: string[];
}
export interface InvestmentInputValue { value: number | string | string[]; source: string; updated_at: string; note?: string }
/** A rules item is {key, params, refs}; an AI item is {text, refs}. */
export interface InvText { key?: string; params?: Record<string, unknown>; text?: string; refs: string[] }
export interface InvCalc { value: number; unit: string; formula: string; inputs: Record<string, unknown> }
export interface InvestmentAssessment {
  id: number;
  listing_id: number;
  profile_id: number | null;
  status: "ai" | "rules_only" | "ai_rejected";
  primary_label: InvLabel;
  rule_label: InvLabel;
  final_label: InvLabel;
  final_tags: string[];
  secondary_labels: string[];
  score: number | null;
  confidence: "high" | "medium" | "low" | null;
  reasons: InvText[];
  risks: InvText[];
  missing_fields: string[];
  calculations: {
    values: Record<string, InvCalc>;
    paths: Record<string, { outcome: string; missing: string[] }>;
    assumptions: Record<string, number>;
    next_steps: InvText[];
    ai_next_steps?: string[];
  };
  sources: {
    listing?: { site_id: string | null; url: string | null; last_seen_at: string | null; fields: string[] };
    user_inputs?: Record<string, { source: string; updated_at: string }>;
    profile?: { id: number; name: string; updated_at: string };
    comparables?: { n: number; unit_price_yen_m2: number; from: string; to: string };
  };
  input_snapshot_hash: string;
  rule_version: string;
  prompt_version: string | null;
  model_provider: string | null;
  model_name: string | null;
  model_version: string | null;
  ai_error: string | null;
  trigger: string;
  created_at: string;
  overrides: { id: number; user_label: InvLabel; user_tags: string[]; reason: string; created_at: string }[];
  stale: boolean | null;
}
export interface InvestmentSummary {
  id: number;
  status: string;
  label: InvLabel;
  overridden: boolean;
  tags: string[];
  score: number | null;
  confidence: string | null;
  missing_count: number;
  created_at: string;
}
export interface BatchEstimate {
  total: number;
  ai_calls: number;
  rules_only: number;
  ai_unavailable_reason: string | null;
  input_units: number;
  output_units: number;
  estimated_cost: number | null;
  currency: string | null;
  pricing_updated_at: string | null;
  batch_max: number | null;
  remaining_calls_today: number | null;
  profile_id: number;
}
export interface BatchJob {
  id: string;
  total: number;
  done: number;
  failed: number;
  ai_calls: number;
  labels: Record<string, number>;
  status: "running" | "completed" | "cancelled" | "paused_limit";
  stop_reason: string | null;
  trigger: string;
  started_at: string;
  finished_at: string | null;
}
