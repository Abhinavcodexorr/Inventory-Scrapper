"""Accept a finished scrape from another machine.

For dealer sites the server cannot reach (country-restricted sites such as
mostwantedcars.ca). The caller scrapes where the site is reachable and POSTs the
rows here; they go through the same engine as a server-side scrape, so matching,
price history and the "only remove cars when the scrape was complete" gate all
still apply. Protected by the same auth as every other /api/dealers route.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from dealer_scraper.adapters.pushed import PushedAdapter
from dealer_scraper.engine import run_dealer
from dealer_scraper.models import Dealer, ScrapeRun

from ..deps import get_db

router = APIRouter(prefix="/api/dealers", tags=["ingest"])

MAX_ROWS = 20_000
# A run still marked "running" after this long is a leftover, not a live scrape.
_STALE_AFTER = timedelta(minutes=15)


class PushedVehicle(BaseModel):
    external_id: str | None = None
    vin: str | None = None
    make: str | None = None
    model: str | None = None
    year: int | None = None
    trim: str | None = None
    mileage: int | None = None
    body_type: str | None = None
    condition: str | None = None
    exterior_color: str | None = None
    interior_color: str | None = None
    transmission: str | None = None
    fuel_type: str | None = None
    drivetrain: str | None = None
    price: float | str | None = None
    original_price: float | str | None = None
    currency: str = "CAD"
    url: str | None = None
    source_status: str | None = None
    source_status_raw: str | None = None


class IngestIn(BaseModel):
    vehicles: list[PushedVehicle] = Field(max_length=MAX_ROWS)
    #: How many cars the site said it has. Drives the removal safety gate: cars
    #: are only marked removed when this push covers (nearly) all of them.
    reported_total: int | None = None


class IngestOut(BaseModel):
    run_id: int
    status: str
    scraped: int
    new: int
    updated: int
    removed: int
    error: str | None = None


@router.post("/{dealer_id}/ingest", response_model=IngestOut)
def ingest_scrape(dealer_id: int, payload: IngestIn, db: Session = Depends(get_db)) -> IngestOut:
    dealer = db.get(Dealer, dealer_id)
    if dealer is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Dealer not found.")
    if not payload.vehicles:
        # An empty push is never "the dealer has no cars": it is a scrape that
        # found nothing, and treating it as inventory would remove every car.
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "No vehicles in the push.")

    cutoff = datetime.now(timezone.utc) - _STALE_AFTER
    running = db.execute(
        select(ScrapeRun.id, ScrapeRun.started_at)
        .where(ScrapeRun.dealer_id == dealer_id, ScrapeRun.status == "running")
    ).all()
    for _id, started in running:
        if started is not None and started.replace(tzinfo=started.tzinfo or timezone.utc) > cutoff:
            raise HTTPException(status.HTTP_409_CONFLICT, "A scrape for this dealer is already running.")

    rows = [v.model_dump() for v in payload.vehicles]
    run = run_dealer(db, dealer, adapter=PushedAdapter(rows, payload.reported_total))
    return IngestOut(
        run_id=run.id, status=run.status, scraped=run.scraped_count,
        new=run.new_count, updated=run.updated_count, removed=run.removed_count,
        error=run.error,
    )
