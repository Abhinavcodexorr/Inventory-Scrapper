"""SQLAlchemy 2.0 ORM models.

Schema covers the SOW: dealership onboarding, current + historical inventory,
vehicle lifecycle (active/removed/sold), a per-vehicle audit trail, and daily
per-dealer snapshots for the dashboard.
"""
from __future__ import annotations

from datetime import datetime, date
from decimal import Decimal

from sqlalchemy import (
    JSON, BigInteger, Boolean, Date, DateTime, Float, ForeignKey, Integer, Numeric, String,
    UniqueConstraint, func, Index,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# JSONB in production (Postgres); portable JSON on other engines (e.g. SQLite in tests).
JsonType = JSON().with_variant(JSONB(), "postgresql")
# BigInteger PKs auto-increment on Postgres (identity); SQLite only auto-increments
# INTEGER PRIMARY KEY, so downgrade to Integer there (tests only).
BigIntPk = BigInteger().with_variant(Integer, "sqlite")


class Base(DeclarativeBase):
    pass


class Dealer(Base):
    """SOW §1 Dealership Onboarding."""
    __tablename__ = "dealers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    base_url: Mapped[str] = mapped_column(String(500), nullable=False)

    # Onboarding fields (admin-entered)
    address: Mapped[str | None] = mapped_column(String(300))
    city: Mapped[str | None] = mapped_column(String(120))
    province: Mapped[str | None] = mapped_column(String(80))
    postal_code: Mapped[str | None] = mapped_column(String(20))
    phone: Mapped[str | None] = mapped_column(String(40))
    # Platform the dealer's site runs on (e.g. "Clutch", "Dealer.com", "DealerInspire").
    # Dealers sharing a provider reuse the SAME adapter + scrape_config template.
    website_provider: Mapped[str | None] = mapped_column(String(120))
    # CMS platform the discovery engine fingerprinted automatically (cms/fingerprints.py,
    # Phase 3). Distinct from website_provider, which is admin-typed free text — this
    # column is never overwritten by hand and never overwrites the admin's value.
    detected_platform: Mapped[str | None] = mapped_column(String(120))

    # which adapter to use: 'json_api' | 'browser' | 'sitemap'
    adapter_mode: Mapped[str] = mapped_column(String(40), nullable=False, default="json_api")
    # per-dealer configuration consumed by the adapter (endpoint, field map, paging, ...)
    scrape_config: Mapped[dict] = mapped_column(JsonType, nullable=False, default=dict)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)  # Active/Inactive

    # Denormalized for the admin list ("View Last Inventory Sync Date / Inventory Count")
    # last_synced_at is the last sync ATTEMPT; pair it with last_sync_status to tell
    # a healthy dealer from one whose scrape has been failing.
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # success | incomplete | failed — mirrors the terminating ScrapeRun.
    last_sync_status: Mapped[str | None] = mapped_column(String(20))
    last_sync_duration_seconds: Mapped[float | None] = mapped_column(Float)
    inventory_count: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    cars: Mapped[list["Car"]] = relationship(back_populates="dealer")


class Car(Base):
    """SOW §2 vehicle record + §4 lifecycle dates. Current state, soft-deleted."""
    __tablename__ = "cars"
    __table_args__ = (
        UniqueConstraint("dealer_id", "external_id", name="uq_car_dealer_external"),
        Index("ix_cars_dealer_status", "dealer_id", "status"),
        Index("ix_cars_vin", "vin"),
    )

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True)
    dealer_id: Mapped[int] = mapped_column(ForeignKey("dealers.id"), nullable=False)

    # Dealer stock # (weakest dedup key). NULL when the feed has none yet —
    # factory-ordered / in-transit units; those are matched on VIN instead.
    external_id: Mapped[str | None] = mapped_column(String(200))
    vin: Mapped[str | None] = mapped_column(String(20))

    # Descriptive fields (SOW §2)
    make: Mapped[str | None] = mapped_column(String(80))
    model: Mapped[str | None] = mapped_column(String(120))
    year: Mapped[int | None] = mapped_column(Integer)
    trim: Mapped[str | None] = mapped_column(String(120))
    mileage: Mapped[int | None] = mapped_column(Integer)
    body_type: Mapped[str | None] = mapped_column(String(80))
    condition: Mapped[str | None] = mapped_column(String(20))       # New | Used
    exterior_color: Mapped[str | None] = mapped_column(String(60))
    interior_color: Mapped[str | None] = mapped_column(String(60))
    transmission: Mapped[str | None] = mapped_column(String(40))
    fuel_type: Mapped[str | None] = mapped_column(String(40))
    drivetrain: Mapped[str | None] = mapped_column(String(40))

    price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))   # Asking Price
    # The struck-through / pre-discount figure when a listing shows two prices
    # ("Retail $54,600 … Sale $49,799"). None when the dealer advertises no
    # discount, so `original_price is not None` means "this car is on sale".
    original_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(3), default="CAD")
    url: Mapped[str | None] = mapped_column(String(700))            # Vehicle Detail URL

    raw_payload: Mapped[dict | None] = mapped_column(JsonType)
    content_hash: Mapped[str | None] = mapped_column(String(64))

    # Lifecycle (SOW §4): active | removed | sold
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())  # Created/First Seen
    # "Last checked" — bumped on EVERY sighting, so it proves the data is fresh
    # but says nothing about whether the listing changed.
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # "Last updated" — bumped ONLY when the listing's content actually changed
    # (price, mileage, specs). This is the one that answers "is this listing
    # stale?"; last_seen_at would say "today" for every active car forever.
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sold_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    dealer: Mapped["Dealer"] = relationship(back_populates="cars")
    price_history: Mapped[list["CarPriceHistory"]] = relationship(back_populates="car")
    audit_logs: Mapped[list["VehicleAuditLog"]] = relationship(back_populates="car")


class CarPriceHistory(Base):
    __tablename__ = "car_price_history"
    __table_args__ = (Index("ix_price_hist_car", "car_id", "recorded_at"),)

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True)
    car_id: Mapped[int] = mapped_column(ForeignKey("cars.id"), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="CAD")
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    car: Mapped["Car"] = relationship(back_populates="price_history")


class VehicleAuditLog(Base):
    """SOW §5 audit trail: append-only history of every lifecycle event per vehicle."""
    __tablename__ = "vehicle_audit_logs"
    __table_args__ = (Index("ix_audit_car", "car_id", "occurred_at"),)

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True)
    car_id: Mapped[int] = mapped_column(ForeignKey("cars.id"), nullable=False)
    dealer_id: Mapped[int] = mapped_column(ForeignKey("dealers.id"), nullable=False)
    # created | updated | price_changed | removed | sold | reactivated
    event_type: Mapped[str] = mapped_column(String(30), nullable=False)
    old_status: Mapped[str | None] = mapped_column(String(20))
    new_status: Mapped[str | None] = mapped_column(String(20))
    note: Mapped[str | None] = mapped_column(String(300))
    # The same movement as `note`, but as numbers. `note` reads "44990 -> 42990",
    # which displays fine and sorts uselessly: "every car that dropped more than
    # $2,000 this week" cannot be asked of a string. These columns make price and
    # mileage movement sortable and filterable, which is the comparison the
    # inventory tracking exists to support. NULL for events with no numeric
    # movement (created, removed, sold).
    old_value: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    new_value: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    car: Mapped["Car"] = relationship(back_populates="audit_logs")


class DailyInventorySnapshot(Base):
    """SOW §6/§7: one row per dealer per day — powers the summary dashboard and
    'total yesterday vs today' without scanning the whole cars table."""
    __tablename__ = "daily_inventory_snapshots"
    __table_args__ = (
        UniqueConstraint("dealer_id", "snapshot_date", name="uq_snapshot_dealer_date"),
        Index("ix_snapshot_dealer_date", "dealer_id", "snapshot_date"),
    )

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True)
    dealer_id: Mapped[int] = mapped_column(ForeignKey("dealers.id"), nullable=False)
    snapshot_date: Mapped[date] = mapped_column(Date, nullable=False)

    total_active: Mapped[int] = mapped_column(Integer, default=0)
    added: Mapped[int] = mapped_column(Integer, default=0)
    removed: Mapped[int] = mapped_column(Integer, default=0)
    sold: Mapped[int] = mapped_column(Integer, default=0)
    price_changed: Mapped[int] = mapped_column(Integer, default=0)
    sync_status: Mapped[str] = mapped_column(String(20), default="success")  # success | failed | incomplete
    run_id: Mapped[int | None] = mapped_column(ForeignKey("scrape_runs.id"))


class ScrapeRun(Base):
    """One row per dealer per run — powers the safety gate and monitoring/alerting."""
    __tablename__ = "scrape_runs"

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True)
    dealer_id: Mapped[int] = mapped_column(ForeignKey("dealers.id"), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="running")  # running|completed|incomplete|failed

    reported_total: Mapped[int | None] = mapped_column(Integer)   # site's claimed count
    scraped_count: Mapped[int] = mapped_column(Integer, default=0)
    new_count: Mapped[int] = mapped_column(Integer, default=0)
    updated_count: Mapped[int] = mapped_column(Integer, default=0)
    price_changed_count: Mapped[int] = mapped_column(Integer, default=0)
    removed_count: Mapped[int] = mapped_column(Integer, default=0)
    reappeared_count: Mapped[int] = mapped_column(Integer, default=0)
    sold_count: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(String(1000))
    # Per-stage latency snapshot from dealer_scraper.timing.StageTimer — nav/wait/
    # extract/parse/db/ai seconds + counts, plus counters like requests_blocked.
    stats: Mapped[dict | None] = mapped_column(JsonType)


class DiscoveryRun(Base):
    """One row per discovery attempt (dealer_scraper/discovery/). Discovery can
    run before a dealer exists (dealer_id is nullable) — the admin discovers a
    config, reviews it, and only then saves the dealer.
    """
    __tablename__ = "discovery_runs"
    __table_args__ = (
        Index("ix_discovery_runs_base_url", "base_url"),
        Index("ix_discovery_runs_dealer", "dealer_id"),
    )

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True)
    base_url: Mapped[str] = mapped_column(String(500), nullable=False)
    dealer_id: Mapped[int | None] = mapped_column(ForeignKey("dealers.id"))

    # queued | running | completed | failed | cancelled
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    phase: Mapped[str | None] = mapped_column(String(40))         # current/last coordinator phase
    phases_done: Mapped[int] = mapped_column(Integer, default=0)
    phases_total: Mapped[int] = mapped_column(Integer, default=0)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Denormalized for cheap filtering/listing (GET .../discover/runs) without
    # parsing `result`/`report`.
    confidence: Mapped[str | None] = mapped_column(String(20))
    verdict: Mapped[str | None] = mapped_column(String(20))
    requires_custom_work: Mapped[bool] = mapped_column(Boolean, default=False)
    adapter_mode: Mapped[str | None] = mapped_column(String(40))
    vehicles_verified: Mapped[int] = mapped_column(Integer, default=0)
    scrape_config: Mapped[dict | None] = mapped_column(JsonType)

    # Full DiscoveryResult.as_dict() (the exact DiscoveryOut shape — summary,
    # listing_url, pagination, extract, reported_total, notes, ...) and the
    # richer structured diagnostics.DiscoveryReport, serialized.
    result: Mapped[dict | None] = mapped_column(JsonType)
    report: Mapped[dict | None] = mapped_column(JsonType)
    error: Mapped[str | None] = mapped_column(String(1000))
    # Per-run request options that aren't part of the discovered result itself,
    # e.g. {"ignore_cache": true} to bypass the site_patterns cache (learning.py).
    options: Mapped[dict | None] = mapped_column(JsonType)


class SitePattern(Base):
    """A pattern discovery has previously confirmed for a host (listing URL,
    API endpoint shape, pagination, field_map, card selector, CMS fingerprint)
    — read as priors on the next discovery run for the same/similar host,
    written on a successful run (learning.py, Phase 4)."""
    __tablename__ = "site_patterns"
    __table_args__ = (
        Index("ix_site_patterns_host", "host"),
        Index("ix_site_patterns_cms", "cms_platform"),
        UniqueConstraint("host", "pattern_kind", name="uq_site_pattern_host_kind"),
    )

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True)
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    cms_platform: Mapped[str | None] = mapped_column(String(80))
    # listing_url | api_endpoint | pagination | field_map | card_selector | cms_fingerprint
    pattern_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    pattern: Mapped[dict] = mapped_column(JsonType, nullable=False)
    success_count: Mapped[int] = mapped_column(Integer, default=0)
    failure_count: Mapped[int] = mapped_column(Integer, default=0)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_run_id: Mapped[int | None] = mapped_column(ForeignKey("discovery_runs.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class User(Base):
    """An operator who can sign in to the admin panel.

    Accounts live here rather than in environment variables because this
    server's `.env` is rewritten from the template on every deploy — a password
    kept there would stop working after each release, silently.

    Only the bcrypt hash is stored; the password itself is never written to the
    database, the logs, or the API responses. `is_active` disables a login
    without deleting the row, so the audit trail of who did what stays intact.
    """
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True)
    # Stored lowercase and unique — logins are matched case-insensitively so
    # "Admin@x.com" and "admin@x.com" cannot become two different accounts.
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(120))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), onupdate=func.now())
