"""Fleet-wide inventory change feed (SOW §4, §5).

The dashboard can already say "12 vehicles removed today". This is what lets
an operator answer the next question — *which* twelve, and what did their
prices do — without knowing a car id in advance.

Everything here reads `vehicle_audit_logs`, which the engine writes on every
change. Nothing is recomputed: if the feed and the dashboard ever disagree,
one of them is reading the wrong window, not deriving a different truth.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from dealer_scraper.models import Car, Dealer, VehicleAuditLog

from ..deps import get_db
from ..schemas import ChangeListOut, ChangeOut, ChangeSummaryOut

router = APIRouter(prefix="/api/changes", tags=["changes"])

# What the operator means by "what changed" — field-level noise like
# `updated` (content hash moved) is recorded but not surfaced by default,
# because a description tweak is not a change anyone is watching for.
MEANINGFUL_EVENTS = ("created", "removed", "sold", "price_changed",
                     "mileage_changed", "reactivated")


def _window(hours: int | None, since: datetime | None, until: datetime | None):
    """Resolve the time window. `since`/`until` win when given; otherwise the
    last `hours`, defaulting to 24 — "what happened in last night's sync?" is
    the question this page exists to answer."""
    now = datetime.now(timezone.utc)
    if since is None:
        since = now - timedelta(hours=hours if hours is not None else 24)
    return since, (until or now)


def _base_query(since, until, dealer_id, event_type, min_abs_delta):
    q = (
        select(VehicleAuditLog, Car, Dealer.name)
        .join(Car, Car.id == VehicleAuditLog.car_id)
        .join(Dealer, Dealer.id == VehicleAuditLog.dealer_id)
        .where(VehicleAuditLog.occurred_at >= since,
               VehicleAuditLog.occurred_at <= until)
    )
    if event_type:
        q = q.where(VehicleAuditLog.event_type == event_type)
    else:
        q = q.where(VehicleAuditLog.event_type.in_(MEANINGFUL_EVENTS))
    if dealer_id:
        q = q.where(VehicleAuditLog.dealer_id == dealer_id)
    if min_abs_delta is not None:
        # "show me anything that moved more than $2,000" — only possible
        # because the movement is stored as numbers, not inside the note text.
        q = q.where(
            VehicleAuditLog.old_value.is_not(None),
            VehicleAuditLog.new_value.is_not(None),
            func.abs(VehicleAuditLog.new_value - VehicleAuditLog.old_value) >= min_abs_delta,
        )
    return q


@router.get("", response_model=ChangeListOut)
def list_changes(
    db: Session = Depends(get_db),
    hours: int | None = Query(None, ge=1, le=24 * 90,
                              description="Look back this many hours (default 24)"),
    since: datetime | None = Query(None, description="Explicit window start (overrides hours)"),
    until: datetime | None = Query(None, description="Explicit window end (default now)"),
    dealer_id: int | None = None,
    event_type: str | None = Query(
        None, description="created | removed | sold | price_changed | mileage_changed | reactivated"),
    min_abs_delta: float | None = Query(
        None, ge=0, description="Only changes where the value moved at least this much"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
) -> ChangeListOut:
    """Every change in the window, newest first, with the vehicle attached."""
    since, until = _window(hours, since, until)
    q = _base_query(since, until, dealer_id, event_type, min_abs_delta)

    total = db.execute(
        select(func.count()).select_from(q.subquery())).scalar_one()

    rows = db.execute(
        q.order_by(VehicleAuditLog.occurred_at.desc(), VehicleAuditLog.id.desc())
         .offset((page - 1) * page_size).limit(page_size)
    ).all()

    items = []
    for log, car, dealer_name in rows:
        delta = None
        if log.old_value is not None and log.new_value is not None:
            delta = log.new_value - log.old_value
        items.append(ChangeOut(
            id=log.id, occurred_at=log.occurred_at, event_type=log.event_type,
            dealer_id=log.dealer_id, dealer_name=dealer_name,
            car_id=car.id, year=car.year, make=car.make, model=car.model,
            trim=car.trim, external_id=car.external_id, vin=car.vin, url=car.url,
            note=log.note, old_value=log.old_value, new_value=log.new_value,
            delta=delta,
        ))

    return ChangeListOut(items=items, total=total, page=page, page_size=page_size)


@router.get("/summary", response_model=ChangeSummaryOut)
def changes_summary(
    db: Session = Depends(get_db),
    hours: int | None = Query(None, ge=1, le=24 * 90),
    since: datetime | None = None,
    until: datetime | None = None,
    dealer_id: int | None = None,
) -> ChangeSummaryOut:
    """Counts per event type for the same window the list uses.

    Drives the header on the changes screen, and gives the dashboard numbers
    somewhere to link to that is guaranteed to agree with them.
    """
    since, until = _window(hours, since, until)

    q = (select(VehicleAuditLog.event_type, func.count())
         .where(VehicleAuditLog.occurred_at >= since,
                VehicleAuditLog.occurred_at <= until,
                VehicleAuditLog.event_type.in_(MEANINGFUL_EVENTS))
         .group_by(VehicleAuditLog.event_type))
    if dealer_id:
        q = q.where(VehicleAuditLog.dealer_id == dealer_id)
    counts = dict(db.execute(q).all())

    # Split price moves by direction: a lot getting cheaper and a lot getting
    # more expensive are different signals, and "price_changed: 40" hides both.
    drops = increases = 0
    dq = (select(VehicleAuditLog.old_value, VehicleAuditLog.new_value)
          .where(VehicleAuditLog.occurred_at >= since,
                 VehicleAuditLog.occurred_at <= until,
                 VehicleAuditLog.event_type == "price_changed",
                 VehicleAuditLog.old_value.is_not(None),
                 VehicleAuditLog.new_value.is_not(None)))
    if dealer_id:
        dq = dq.where(VehicleAuditLog.dealer_id == dealer_id)
    for old, new in db.execute(dq).all():
        if new < old:
            drops += 1
        elif new > old:
            increases += 1

    return ChangeSummaryOut(
        added=counts.get("created", 0),
        removed=counts.get("removed", 0),
        sold=counts.get("sold", 0),
        price_changed=counts.get("price_changed", 0),
        price_drops=drops,
        price_increases=increases,
        reactivated=counts.get("reactivated", 0),
    )
