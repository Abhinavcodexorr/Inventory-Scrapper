"""Pydantic request/response models for the API."""
from __future__ import annotations

from datetime import datetime, date
from decimal import Decimal
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ---------------------------------------------------------------- Dealers
class DealerBase(BaseModel):
    name: str = Field(..., max_length=200)
    base_url: str = Field(..., max_length=500)
    address: str | None = Field(None, max_length=300)
    city: str | None = Field(None, max_length=120)
    province: str | None = Field(None, max_length=80)
    postal_code: str | None = Field(None, max_length=20)
    phone: str | None = Field(None, max_length=40)
    website_provider: str | None = Field(None, max_length=120)
    active: bool = True

    @field_validator("base_url")
    @classmethod
    def _normalise_base_url(cls, v: str) -> str:
        """Canonical form, so the duplicate check actually catches duplicates.

        `https://x.ca`, `https://x.ca/` and `HTTPS://X.ca/` are one dealership,
        but as raw strings they are three distinct rows — each passing the
        duplicate-base_url 409 and each scraping the same lot into separate
        inventories. Scheme and host are lowercased (both case-insensitive per
        RFC 3986); the path is left alone apart from a trailing slash, since
        paths are case-sensitive.
        """
        v = (v or "").strip()
        if not v:
            raise ValueError("base_url is required")
        parts = urlsplit(v)
        if parts.scheme and parts.netloc:
            path = parts.path.rstrip("/")
            v = urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path,
                            parts.query, parts.fragment))
        else:
            v = v.rstrip("/")
        return v


class DealerCreate(DealerBase):
    adapter_mode: str = "browser"          # json_api | browser
    scrape_config: dict = Field(default_factory=dict)
    # A dealer whose site discovery could not actually extract vehicles from is
    # rejected (409) rather than onboarded into a permanent zero-inventory state.
    # Set true to override deliberately — e.g. a site being fixed by hand, or one
    # discovery misjudged. The override is explicit so it can never happen by accident.
    force: bool = Field(
        False,
        description="Add the dealer even though the scraper could not verify this site.",
    )


class DiscoverIn(BaseModel):
    """Auto-discovery input — the dealer's website URL is all that's needed.

    base_url is fetched by the server (httpx) AND rendered in a real
    Chromium instance (discovery's browser phase) — an unvalidated value here
    is a fully-readable SSRF: point it at a cloud metadata endpoint or an
    internal service and the run report leaks whatever comes back. Rejected
    here at the API boundary means no DiscoveryRun row is ever created for a
    bad URL, not just a downstream failure.
    """
    base_url: str = Field(..., max_length=500, examples=["https://example-motors.ca"])
    dealer_id: int | None = Field(
        None, description="Attach this discovery run to an existing dealer, if re-detecting one.")

    @field_validator("base_url")
    @classmethod
    def _reject_unsafe_url(cls, v: str) -> str:
        # Not pydantic.HttpUrl: it doesn't block RFC1918/loopback/metadata
        # addresses and it normalizes the URL shape, which would silently
        # change what gets stored. This validates without rewriting.
        from dealer_scraper.discovery.net.safe_http import UnsafeUrlError, validate_public_url

        candidate = v.strip()
        if candidate and not urlsplit(candidate).scheme:
            # Mirrors DiscoveryCoordinator.run()'s own scheme-prepend
            # (coordinator.py) so what gets checked here matches what
            # actually gets fetched, even for a bare "example-motors.ca" input.
            candidate = "https://" + candidate
        try:
            validate_public_url(candidate)
        except UnsafeUrlError as exc:
            raise ValueError(str(exc)) from exc
        return v


class DiscoveryOut(BaseModel):
    """Proposed scrape_config plus the evidence behind it, so the UI can show
    the admin what was found instead of asking them to trust a JSON blob."""
    base_url: str
    ok: bool
    confidence: str                      # high | medium | low | failed
    # What the admin should do: ready | needs_review | unsupported
    verdict: str
    # True when this website structure needs chargeable custom scraper development
    requires_custom_work: bool
    summary: str                         # one plain-English line to show the admin
    adapter_mode: str
    scrape_config: dict
    vehicles_found: int
    reported_total: int | None = None
    listing_url: str | None = None
    pagination: str | None = None        # url | scroll | click | page | offset
    extract: str | None = None           # jsonld | dom | json_api
    notes: list[str] = Field(default_factory=list)
    run_id: int | None = None
    report_available: bool = False


class DiscoveryJobOut(BaseModel):
    """Returned by POST /api/dealers/discover (background mode, the default)."""
    status: str          # queued | running | no_worker
    job_id: str
    run_id: int
    mode: str            # celery | local


class SiteCompatibilityOut(BaseModel):
    """One host in the fleet-wide compatibility report.

    Answers the two commercial questions in one row: can our engine scrape this
    site as-is, and if not, is it billable as new-website-structure work.
    """
    host: str
    base_url: str
    cms_platform: str | None = None
    verdict: str | None = None                # ready | needs_review | unsupported
    confidence: str | None = None
    requires_custom_work: bool = False
    adapter_mode: str | None = None
    vehicles_verified: int = 0
    last_checked_at: datetime | None = None
    # Set once the host is a live dealer, so the report distinguishes "evaluated"
    # from "onboarded".
    dealer_id: int | None = None
    dealer_name: str | None = None
    onboarded: bool = False
    # How many other hosts share this CMS — the reuse/pricing signal: one scraper
    # already covers this platform, so additional dealers on it are not new work.
    hosts_sharing_cms: int = 0
    attempts: int = 0


class SiteCompatibilitySummaryOut(BaseModel):
    """Totals for the compatibility report header."""
    hosts_total: int
    ready: int
    needs_review: int
    unsupported: int
    billable: int                              # requires_custom_work == True
    onboarded: int
    distinct_cms_platforms: int


class SiteCompatibilityReportOut(BaseModel):
    summary: SiteCompatibilitySummaryOut
    items: list[SiteCompatibilityOut]


class DiscoveryRunSummaryOut(BaseModel):
    """One row of GET /api/dealers/discover/runs — no report blob."""
    model_config = ConfigDict(from_attributes=True)
    id: int
    base_url: str
    dealer_id: int | None
    status: str
    phase: str | None
    confidence: str | None
    verdict: str | None
    requires_custom_work: bool
    adapter_mode: str | None
    vehicles_verified: int
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime | None
    error: str | None


class DiscoveryRunStatusOut(BaseModel):
    """Returned by GET /api/dealers/discover/runs/{run_id} — the polling contract."""
    run_id: int
    status: str           # queued | running | completed | failed | cancelled
    phase: str | None
    phases_done: int
    phases_total: int
    started_at: datetime | None
    finished_at: datetime | None
    error: str | None
    result: DiscoveryOut | None = None
    report: dict | None = None


class DiscoveryCancelOut(BaseModel):
    """Result of asking an in-flight discovery run to stop."""
    status: str            # cancelling | not_running
    job_id: str
    run_id: int
    mode: str               # celery | local
    detail: str


class DealerUpdate(BaseModel):
    """All fields optional — PATCH semantics."""
    name: str | None = None
    base_url: str | None = None
    address: str | None = None
    city: str | None = None
    province: str | None = None
    postal_code: str | None = None
    phone: str | None = None
    website_provider: str | None = None
    active: bool | None = None
    adapter_mode: str | None = None
    scrape_config: dict | None = None


class SyncCancelOut(BaseModel):
    """Result of asking a dealer's in-flight sync to stop."""
    status: str            # cancelling | not_running
    job_id: str
    dealer_id: int
    mode: str              # celery | local
    detail: str


class DealerOut(DealerBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    adapter_mode: str
    scrape_config: dict
    inventory_count: int
    last_synced_at: datetime | None
    last_sync_status: str | None = None
    last_sync_duration_seconds: float | None = None
    created_at: datetime | None
    # Platform discovery fingerprinted (dealer_inspire, dealeron, cdk, …).
    # Never typed by an admin, never overwrites website_provider.
    detected_platform: str | None = None
    # True when this dealer was created with a scrape_config copied from an
    # existing dealer on the same platform rather than discovered from scratch.
    # The per-website-structure pricing model turns on this distinction, so the
    # UI has to be able to say which happened.
    scraper_reused: bool = False
    scraper_reused_from: str | None = None


# --------------------------------------------------------------- Vehicles
class VehicleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    dealer_id: int
    external_id: str | None          # NULL for units with no stock # (matched on VIN/URL)
    vin: str | None
    year: int | None
    make: str | None
    model: str | None
    trim: str | None
    mileage: int | None
    body_type: str | None
    condition: str | None
    exterior_color: str | None
    interior_color: str | None
    transmission: str | None
    fuel_type: str | None
    drivetrain: str | None
    price: Decimal | None
    original_price: Decimal | None   # pre-discount figure; None when not on sale
    currency: str
    url: str | None
    status: str
    first_seen_at: datetime          # Created date
    last_seen_at: datetime           # Last checked — moves on every sync
    updated_at: datetime | None      # Last updated — moves only when the listing changes
    removed_at: datetime | None
    sold_at: datetime | None


class PageMeta(BaseModel):
    total: int
    page: int
    page_size: int


class VehiclePage(BaseModel):
    meta: PageMeta
    items: list[VehicleOut]


class PriceHistoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    price: Decimal
    currency: str
    recorded_at: datetime


class AuditLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    car_id: int
    dealer_id: int
    event_type: str
    old_status: str | None
    new_status: str | None
    note: str | None
    old_value: Decimal | None = None
    new_value: Decimal | None = None
    occurred_at: datetime


class ChangeOut(BaseModel):
    """One change, with enough vehicle detail to be read on its own.

    The dashboard can say "12 removed today"; this is what lets an operator
    see WHICH twelve. Joining the vehicle in here rather than making the UI
    fetch each car separately keeps a 500-row day to one request.
    """
    id: int
    occurred_at: datetime
    event_type: str
    dealer_id: int
    dealer_name: str
    car_id: int
    year: int | None = None
    make: str | None = None
    model: str | None = None
    trim: str | None = None
    external_id: str | None = None      # stock #
    vin: str | None = None
    url: str | None = None
    note: str | None = None
    old_value: Decimal | None = None
    new_value: Decimal | None = None
    # new_value - old_value, computed server-side so every client agrees on
    # the sign and the UI never has to do arithmetic on possibly-null money.
    delta: Decimal | None = None


class ChangeListOut(BaseModel):
    items: list[ChangeOut]
    total: int
    page: int
    page_size: int


class ChangeSummaryOut(BaseModel):
    """Counts per event type over the selected window — the figures the
    dashboard shows, from the same query that backs the list, so the two can
    never disagree."""
    added: int = 0
    removed: int = 0
    sold: int = 0
    price_changed: int = 0
    price_drops: int = 0
    price_increases: int = 0
    reactivated: int = 0


# -------------------------------------------------------------- Dashboard
class DealerStats(BaseModel):
    dealer_id: int
    dealer_name: str
    total_today: int
    total_yesterday: int
    added_today: int
    removed_today: int
    sold_today: int
    last_sync_at: datetime | None
    sync_status: str | None
    #: Rows the dealer's feed returned in the latest finished run, by the feed's
    #: own status word (lower-cased): {"instock": 59, "sold": 45, "archive": 952,
    #: "unpublished": 34}. None for dealers whose site states no status (browser
    #: scrapes) — there is nothing to break down.
    feed_statuses: dict[str, int] | None = None
    feed_total: int | None = None
    feed_run_status: str | None = None


class DashboardSummary(BaseModel):
    """SOW: 'Overall Inventory Summary' across all dealerships."""
    dealers_total: int
    dealers_active: int
    vehicles_active: int
    vehicles_removed: int
    vehicles_sold: int
    added_today: int
    removed_today: int
    sold_today: int
    last_sync_at: datetime | None
    dealers_failed_last_run: int


class SyncRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    dealer_id: int
    status: str
    started_at: datetime
    finished_at: datetime | None
    reported_total: int | None
    scraped_count: int
    new_count: int
    updated_count: int
    price_changed_count: int
    removed_count: int
    reappeared_count: int
    sold_count: int
    error: str | None
    stats: dict | None = None


class SyncJobOut(BaseModel):
    """Returned by POST /api/dealers/{id}/sync."""
    status: str          # queued | running  ("running" = one was already in flight)
    job_id: str
    dealer_id: int
    mode: str            # celery | local — which backend picked the job up
    run_id: int | None = None


class SyncProgressOut(BaseModel):
    """Live progress of the in-flight (or most recent) scrape run."""
    run_id: int
    status: str                      # running | completed | incomplete | failed
    scraped_count: int
    reported_total: int | None = None
    new_count: int
    updated_count: int
    price_changed_count: int
    removed_count: int
    percent: float | None = None     # None when the site reports no total
    elapsed_seconds: float


class SyncJobStatusOut(BaseModel):
    """Task state + latest scrape run for the dealer (if any)."""
    job_id: str
    dealer_id: int
    job_status: str  # PENDING | STARTED | SUCCESS | FAILURE | RETRY | REVOKED
    mode: str = "celery"
    result: dict | None = None
    progress: SyncProgressOut | None = None
    latest_run: SyncRunOut | None = None


class SnapshotOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    snapshot_date: date
    total_active: int
    added: int
    removed: int
    sold: int
    price_changed: int
    sync_status: str
