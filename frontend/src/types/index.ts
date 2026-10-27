/** Types mirroring api/schemas.py */

export interface Dealer {
  id: number;
  name: string;
  base_url: string;
  address: string | null;
  city: string | null;
  province: string | null;
  postal_code: string | null;
  phone: string | null;
  website_provider: string | null;
  active: boolean;
  adapter_mode: string;
  scrape_config: Record<string, unknown>;
  inventory_count: number;
  last_synced_at: string | null;
  last_sync_status: string | null;
  last_sync_duration_seconds: number | null;
  created_at: string | null;
  /** Platform discovery fingerprinted (dealeron, cdk, …). Never admin-typed. */
  detected_platform?: string | null;
  /** True when this dealer was created from an existing same-platform config
   *  rather than a fresh discovery run. Drives per-structure billing. */
  scraper_reused?: boolean;
  scraper_reused_from?: string | null;
}

export interface DealerCreate {
  name: string;
  base_url: string;
  address?: string | null;
  city?: string | null;
  province?: string | null;
  postal_code?: string | null;
  phone?: string | null;
  website_provider?: string | null;
  active?: boolean;
  adapter_mode?: string;
  scrape_config?: Record<string, unknown>;
  /**
   * Add the dealership even though the scraper could not verify its website.
   * Create-only; deliberately opt-in so an unreadable site is never onboarded
   * by accident into a permanent zero-inventory state.
   */
  force?: boolean;
}

export type DealerUpdate = Partial<Omit<DealerCreate, 'force'>>;

/** One host in GET /api/dealers/compatibility. */
export interface SiteCompatibility {
  host: string;
  base_url: string;
  cms_platform: string | null;
  verdict: 'ready' | 'needs_review' | 'unsupported' | null;
  confidence: string | null;
  /** true => chargeable new-website-structure development */
  requires_custom_work: boolean;
  adapter_mode: string | null;
  vehicles_verified: number;
  last_checked_at: string | null;
  dealer_id: number | null;
  dealer_name: string | null;
  onboarded: boolean;
  /** How many hosts share this CMS — one scraper already covers them. */
  hosts_sharing_cms: number;
  attempts: number;
}

export interface SiteCompatibilitySummary {
  hosts_total: number;
  ready: number;
  needs_review: number;
  unsupported: number;
  billable: number;
  onboarded: number;
  distinct_cms_platforms: number;
}

export interface SiteCompatibilityReport {
  summary: SiteCompatibilitySummary;
  items: SiteCompatibility[];
}

/** Result of POST /api/dealers/discover — auto-derived scraper settings. */
export interface Discovery {
  base_url: string;
  ok: boolean;
  /** high | medium | low | failed */
  confidence: string;
  /** ready | needs_review | unsupported — what the admin should do */
  verdict: 'ready' | 'needs_review' | 'unsupported';
  /** true => this website structure needs chargeable custom development */
  requires_custom_work: boolean;
  /** one plain-English line to show the admin */
  summary: string;
  adapter_mode: string;
  scrape_config: Record<string, unknown>;
  vehicles_found: number;
  reported_total: number | null;
  listing_url: string | null;
  /** url | scroll | click | page | offset */
  pagination: string | null;
  /** jsonld | dom | json_api */
  extract: string | null;
  notes: string[];
  /** Set when this result came from a persisted discovery run (background mode). */
  run_id?: number | null;
  report_available?: boolean;
  /** Structured diagnostics when fetched via include_report=true */
  report?: DiscoveryReport | null;
}

export interface DiscoveryPhaseRecord {
  name: string;
  duration_seconds: number;
}

export interface DiscoveryReport {
  duration_seconds?: number;
  budget_seconds?: number;
  phases?: DiscoveryPhaseRecord[];
  stage_timings?: Record<string, unknown>;
  evidence_counts?: Record<string, number>;
  notes?: string[];
}

/** GET/POST /api/dealers/discover/runs — background discovery job tracking. */
export interface DiscoveryJob {
  status: string; // queued | running | no_worker
  job_id: string;
  run_id: number;
  mode: 'celery' | 'local';
}

export interface DiscoveryRunSummary {
  id: number;
  base_url: string;
  dealer_id: number | null;
  status: string; // queued | running | completed | failed | cancelled
  phase: string | null;
  confidence: string | null;
  verdict: string | null;
  requires_custom_work: boolean;
  adapter_mode: string | null;
  vehicles_verified: number;
  started_at: string | null;
  finished_at: string | null;
  created_at: string | null;
  error: string | null;
}

export interface DiscoveryRunStatus {
  run_id: number;
  status: string;
  phase: string | null;
  phases_done: number;
  phases_total: number;
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
  result: Discovery | null;
  report: DiscoveryReport | null;
}

export interface Vehicle {
  id: number;
  dealer_id: number;
  external_id: string;
  vin: string | null;
  year: number | null;
  make: string | null;
  model: string | null;
  trim: string | null;
  mileage: number | null;
  body_type: string | null;
  condition: string | null;
  exterior_color: string | null;
  interior_color: string | null;
  transmission: string | null;
  fuel_type: string | null;
  drivetrain: string | null;
  price: string | null;
  /** Pre-discount figure. Non-null means the listing is advertised on sale. */
  original_price: string | null;
  currency: string;
  url: string | null;
  status: string;
  /** Created date */
  first_seen_at: string;
  /** Last checked — moves on every sync, so it proves freshness only. */
  last_seen_at: string;
  /** Last updated — moves only when the listing itself changed. */
  updated_at: string | null;
  removed_at: string | null;
  sold_at: string | null;
}

export interface PageMeta {
  total: number;
  page: number;
  page_size: number;
}

export interface VehiclePage {
  meta: PageMeta;
  items: Vehicle[];
}

export interface PriceHistory {
  price: string;
  currency: string;
  recorded_at: string;
}

export interface AuditLog {
  id: number;
  car_id: number;
  dealer_id: number;
  event_type: string;
  old_status: string | null;
  new_status: string | null;
  note: string | null;
  occurred_at: string;
}

export interface DealerStats {
  dealer_id: number;
  dealer_name: string;
  total_today: number;
  total_yesterday: number;
  added_today: number;
  removed_today: number;
  sold_today: number;
  last_sync_at: string | null;
  sync_status: string | null;
  /** Latest run's rows by the feed's own status word, lower-cased; null when the site states no status. */
  feed_statuses?: Record<string, number> | null;
  feed_total?: number | null;
  feed_run_status?: string | null;
}

export interface DashboardSummary {
  dealers_total: number;
  dealers_active: number;
  vehicles_active: number;
  vehicles_removed: number;
  vehicles_sold: number;
  added_today: number;
  removed_today: number;
  sold_today: number;
  last_sync_at: string | null;
  dealers_failed_last_run: number;
}

export interface SyncRun {
  id: number;
  dealer_id: number;
  status: string;
  started_at: string;
  finished_at: string | null;
  reported_total: number | null;
  scraped_count: number;
  new_count: number;
  updated_count: number;
  price_changed_count: number;
  removed_count: number;
  reappeared_count: number;
  sold_count: number;
  error: string | null;
  /** Stage timings and counters from the run; `counters.hidden` = feed rows ignored on purpose. */
  stats?: { counters?: Record<string, number> } | null;
}

export interface SyncJob {
  /** queued | running — "running" means a sync was already in flight */
  status: string;
  job_id: string;
  dealer_id: number;
  /** celery | local — which backend picked the job up */
  mode: string;
  run_id: number | null;
}

export interface SyncCancel {
  /** cancelling | not_running */
  status: string;
  job_id: string;
  dealer_id: number;
  mode: string;
  detail: string;
}

export interface SyncProgress {
  run_id: number;
  status: string;
  scraped_count: number;
  reported_total: number | null;
  new_count: number;
  updated_count: number;
  price_changed_count: number;
  removed_count: number;
  percent: number | null;
  elapsed_seconds: number;
}

export interface SyncJobStatus {
  job_id: string;
  dealer_id: number;
  /** PENDING | STARTED | SUCCESS | FAILURE | RETRY | REVOKED */
  job_status: string;
  mode: string;
  result: Record<string, unknown> | null;
  progress: SyncProgress | null;
  latest_run: SyncRun | null;
}

export interface Snapshot {
  snapshot_date: string;
  total_active: number;
  added: number;
  removed: number;
  sold: number;
  price_changed: number;
  sync_status: string;
}

export interface VehicleListParams {
  dealer_id?: number;
  status?: string;
  make?: string;
  model?: string;
  year_min?: number;
  year_max?: number;
  price_min?: number;
  price_max?: number;
  q?: string;
  sort?: string;
  order?: 'asc' | 'desc';
  page?: number;
  page_size?: number;
}

/** One recorded change, with the vehicle attached so a row reads on its own. */
export interface Change {
  id: number;
  occurred_at: string;
  event_type: string;
  dealer_id: number;
  dealer_name: string;
  car_id: number;
  year: number | null;
  make: string | null;
  model: string | null;
  trim: string | null;
  external_id: string | null;
  vin: string | null;
  url: string | null;
  note: string | null;
  /** Money and mileage arrive as strings so no precision is lost in JSON. */
  old_value: string | null;
  new_value: string | null;
  /** new - old, computed server-side so the sign is consistent everywhere. */
  delta: string | null;
}

export interface ChangeList {
  items: Change[];
  total: number;
  page: number;
  page_size: number;
}

export interface ChangeSummary {
  added: number;
  removed: number;
  sold: number;
  price_changed: number;
  price_drops: number;
  price_increases: number;
  reactivated: number;
}

export interface ChangeListParams {
  hours?: number;
  dealer_id?: number;
  event_type?: string;
  /** Only changes where the value moved at least this much, either direction. */
  min_abs_delta?: number;
  page?: number;
  page_size?: number;
}

export interface ApiError {
  detail?: string | { msg: string }[];
}
