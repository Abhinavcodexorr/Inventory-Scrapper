"""SOW §6 — Inventory Summary Dashboard endpoints.

All figures come from `daily_inventory_snapshots` (written once per dealer per
run) so the dashboard never has to scan the whole `cars` table.
"""
from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from dealer_scraper.models import Car, DailyInventorySnapshot, Dealer, ScrapeRun
from ..deps import get_db
from ..schemas import DashboardSummary, DealerStats, SnapshotOut

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


def _snap_map(db: Session, day: date) -> dict[int, DailyInventorySnapshot]:
    rows = db.execute(
        select(DailyInventorySnapshot).where(DailyInventorySnapshot.snapshot_date == day)
    ).scalars()
    return {s.dealer_id: s for s in rows}


def _latest_feed_counts(db: Session) -> dict[int, tuple[dict[str, int], int, str]]:
    """Per dealer: the feed's status breakdown from its most recent finished run.

    A run's `stats.counters` holds one `feed_<status>` counter per distinct word
    the feed used. Incomplete runs are included — a partial read is still the
    freshest evidence of what the feed contains — and flagged by run status.
    """
    latest = (
        select(func.max(ScrapeRun.id))
        .where(ScrapeRun.status.in_(("completed", "incomplete")))
        .group_by(ScrapeRun.dealer_id)
    )
    out: dict[int, tuple[dict[str, int], int, str]] = {}
    for dealer_id, stats, scraped, status in db.execute(
        select(ScrapeRun.dealer_id, ScrapeRun.stats, ScrapeRun.scraped_count, ScrapeRun.status)
        .where(ScrapeRun.id.in_(latest))
    ):
        counters = (stats or {}).get("counters") or {}
        feed = {k[len("feed_"):]: int(v) for k, v in counters.items() if k.startswith("feed_")}
        if feed:
            out[dealer_id] = (feed, scraped, status)
    return out


@router.get("/summary", response_model=DashboardSummary)
def summary(db: Session = Depends(get_db)):
    """Overall inventory summary across all dealerships."""
    today, yesterday = date.today(), date.today() - timedelta(days=1)
    today_snaps = _snap_map(db, today)
    # fall back to yesterday's run if today's hasn't executed yet
    snaps = today_snaps or _snap_map(db, yesterday)

    def status_count(s: str) -> int:
        return db.execute(
            select(func.count()).select_from(Car).where(Car.status == s)
        ).scalar_one()

    return DashboardSummary(
        dealers_total=db.execute(select(func.count()).select_from(Dealer)).scalar_one(),
        dealers_active=db.execute(
            select(func.count()).select_from(Dealer).where(Dealer.active.is_(True))
        ).scalar_one(),
        vehicles_active=status_count("active"),
        vehicles_removed=status_count("removed"),
        vehicles_sold=status_count("sold"),
        added_today=sum(s.added for s in snaps.values()),
        removed_today=sum(s.removed for s in snaps.values()),
        sold_today=sum(s.sold for s in snaps.values()),
        last_sync_at=db.execute(select(func.max(Dealer.last_synced_at))).scalar_one(),
        dealers_failed_last_run=sum(
            1 for s in snaps.values() if s.sync_status in ("failed", "incomplete")
        ),
    )


@router.get("/dealers", response_model=list[DealerStats])
def per_dealer(db: Session = Depends(get_db)):
    """Per-dealership statistics — the main dashboard table (SOW §6)."""
    today, yesterday = date.today(), date.today() - timedelta(days=1)
    t_snaps, y_snaps = _snap_map(db, today), _snap_map(db, yesterday)

    feed = _latest_feed_counts(db)
    out: list[DealerStats] = []
    for dealer in db.execute(select(Dealer).order_by(Dealer.name)).scalars():
        t, y = t_snaps.get(dealer.id), y_snaps.get(dealer.id)
        f = feed.get(dealer.id)
        out.append(DealerStats(
            feed_statuses=f[0] if f else None,
            feed_total=f[1] if f else None,
            feed_run_status=f[2] if f else None,
            dealer_id=dealer.id,
            dealer_name=dealer.name,
            total_today=(t.total_active if t else dealer.inventory_count),
            total_yesterday=(y.total_active if y else 0),
            added_today=(t.added if t else 0),
            removed_today=(t.removed if t else 0),
            sold_today=(t.sold if t else 0),
            last_sync_at=dealer.last_synced_at,
            sync_status=(t.sync_status if t else None),
        ))
    return out


@router.get("/dealers/{dealer_id}/trend", response_model=list[SnapshotOut])
def dealer_trend(
    dealer_id: int,
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
):
    """Daily snapshot series for charting inventory over time."""
    since = date.today() - timedelta(days=days)
    stmt = (select(DailyInventorySnapshot)
            .where(DailyInventorySnapshot.dealer_id == dealer_id,
                   DailyInventorySnapshot.snapshot_date >= since)
            .order_by(DailyInventorySnapshot.snapshot_date))
    return list(db.execute(stmt).scalars())
