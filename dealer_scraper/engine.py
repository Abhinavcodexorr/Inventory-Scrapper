"""Core scrape engine: dedup (upsert), change detection, price history,
removal reconciliation (safety-gated), sold detection, per-vehicle audit trail,
and the daily per-dealer snapshot.

Dealer-agnostic: driven by an adapter that yields NormalizedVehicle objects.
One call to run_dealer() == one dealer's daily run.
"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone, timedelta
from decimal import Decimal

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from .cancel import ScrapeCancelled, clear_cancel, is_cancelled
from .config import settings
from .models import (
    Car, CarPriceHistory, Dealer, ScrapeRun, VehicleAuditLog, DailyInventorySnapshot,
)
from .adapters.registry import build_adapter
from .quality import is_described, is_valid_vin
from .timing import StageTimer

log = logging.getLogger("scraper.engine")

# How often to publish mid-scrape progress (seconds). Time-based rather than
# every-N-vehicles so both a 6-car lot and a 4000-car one report smoothly.
_PROGRESS_COMMIT_INTERVAL = 2.0

_VEHICLE_FIELDS = (
    "vin", "make", "model", "year", "trim", "mileage", "body_type", "condition",
    "exterior_color", "interior_color", "transmission", "fuel_type", "drivetrain",
    "price", "original_price", "currency", "url",
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _audit(session: Session, car: Car, dealer_id: int, event_type: str,
           old_status: str | None = None, new_status: str | None = None, note: str | None = None,
           old_value: Decimal | int | None = None, new_value: Decimal | int | None = None) -> None:
    """Record one change against a vehicle.

    `note` stays human-readable for the UI; `old_value`/`new_value` carry the
    same movement as numbers so the changes feed can sort and filter by how
    much a price moved — the question the operator actually asks.
    """
    session.add(VehicleAuditLog(
        car_id=car.id, dealer_id=dealer_id, event_type=event_type,
        old_status=old_status, new_status=new_status, note=note,
        old_value=Decimal(old_value) if old_value is not None else None,
        new_value=Decimal(new_value) if new_value is not None else None,
        occurred_at=_now(),
    ))


def _apply_fields(car: Car, v) -> None:
    for f in _VEHICLE_FIELDS:
        setattr(car, f, getattr(v, f))


# --------------------------------------------------------------- identity
def _vin_key(vin: str | None) -> str | None:
    """VINs are compared case/whitespace-insensitively; blanks never match.

    Only a well-formed 17-character VIN is an identity. A mis-mapped field
    supplies junk such as "TRUE", and treating that as a VIN makes every vehicle
    in the feed match the same row — 124 scraped vehicles collapsed into one."""
    if not vin or not is_valid_vin(vin):
        return None
    return str(vin).strip().upper()


class _CarIndex:
    """Lookup of a dealer's existing cars by every identity key we support.

    Dedup precedence (SOW: never create duplicate vehicles):
        1. VIN                  — globally stable, survives a re-listing
        2. listing URL          — stable when the site has no VIN on the grid
        3. external_id / stock# — provider inventory id, the weakest key

    First writer wins per key, so a duplicated VIN in existing data can never make
    two rows fight over the same incoming listing.
    """

    def __init__(self, cars: list[Car]):
        self.by_vin: dict[str, Car] = {}
        self.by_url: dict[str, Car] = {}
        self.by_ext: dict[str, Car] = {}
        for car in cars:
            self.add(car)

    def add(self, car: Car) -> None:
        vin = _vin_key(car.vin)
        if vin:
            self.by_vin.setdefault(vin, car)
        if car.url:
            self.by_url.setdefault(car.url, car)
        if car.external_id:
            self.by_ext.setdefault(car.external_id, car)

    def match(self, v) -> Car | None:
        vin = _vin_key(v.vin)
        if vin and (car := self.by_vin.get(vin)) is not None:
            return car
        if v.url and (car := self.by_url.get(v.url)) is not None:
            return car
        if v.external_id and (car := self.by_ext.get(v.external_id)) is not None:
            return car
        return None

    def holder_of_external_id(self, external_id: str) -> Car | None:
        return self.by_ext.get(external_id)


_MAX_FEED_STATUSES = 12


def _feed_counter(raw: str, seen: set[str]) -> str:
    """Counter name for a feed status word, e.g. "Archive" -> "feed_archive".

    Bounded: a feed that returns free text in its status field must not create a
    counter per distinct value, so past a dozen distinct words the rest pool into
    "feed_other"."""
    key = "feed_" + re.sub(r"[^a-z0-9]+", "_", raw.lower()).strip("_")
    if key not in seen and len(seen) >= _MAX_FEED_STATUSES:
        return "feed_other"
    seen.add(key)
    return key


def _check_runaway(run: ScrapeRun, held_active: int) -> None:
    """Abort a run that has scraped implausibly more than the dealer can hold."""
    cap = max(
        settings.runaway_floor,
        settings.runaway_multiple * max(run.reported_total or 0, held_active),
    )
    if run.scraped_count > cap:
        raise RuntimeError(
            f"Runaway scrape stopped at {run.scraped_count} vehicles (limit {int(cap)}; "
            f"site reports {run.reported_total}, dealer holds {held_active}). The same "
            f"pages are being read repeatedly — check the config's pagination."
        )


def run_dealer(session: Session, dealer: Dealer, adapter=None) -> ScrapeRun:
    """Scrape one dealer end-to-end and persist results. Never raises for a
    scrape/parse failure — records it on the ScrapeRun and returns so the daily
    runner can continue with other dealers."""
    run = ScrapeRun(dealer_id=dealer.id, status="running", started_at=_now())
    session.add(run)
    session.flush()
    # Commit the 'running' row immediately so other connections (the API's status
    # endpoint, a second sync request) can see this scrape is already in flight.
    session.commit()

    timer = StageTimer()
    adapter = adapter or build_adapter(dealer.adapter_mode, dealer.scrape_config, timer=timer)

    all_cars = list(
        session.execute(select(Car).where(Car.dealer_id == dealer.id)).scalars()
    )
    index = _CarIndex(all_cars)
    active_at_start = {c.id for c in all_cars if c.status == "active"}
    seen_car_ids: set[int] = set()
    # Vehicles that carried at least one describing field. A listing page the
    # extractor cannot read still yields rows — it collects the detail links fine
    # and parses nothing out of the cards — so counting rows alone reports a
    # perfectly healthy run over completely empty inventory. See the incomplete
    # gate below.
    described_count = 0
    # Rows that resolved to a vehicle already reconciled earlier in THIS run.
    duplicate_count = 0
    feed_statuses_seen: set[str] = set()

    complete = True
    try:
        # A stop can arrive while the job is still queued; honour it before
        # spending a request on the site.
        if is_cancelled(dealer.id):
            raise ScrapeCancelled()
        run.reported_total = adapter.fetch_total_count()
        # Publish the denominator straight away so the UI can show a real
        # progress bar from the first poll instead of an indeterminate one.
        session.commit()
        last_progress = time.monotonic()
        for v in adapter.iter_vehicles():
            if not (v.external_id or _vin_key(v.vin) or v.url):
                continue  # nothing we can key on — cannot dedup, so skip
            run.scraped_count += 1
            _check_runaway(run, len(active_at_start))
            if v.source_status_raw:
                timer.incr(_feed_counter(v.source_status_raw, feed_statuses_seen))
            if is_described(v):
                described_count += 1
            if time.monotonic() - last_progress >= _PROGRESS_COMMIT_INTERVAL:
                if run.reported_total is None:
                    # Browser-mode adapters only learn the total while iterating
                    # (it's parsed off the rendered list page), so the pre-loop
                    # call above returns None for them; keep polling until it's
                    # available so the progress bar and removal gate get a real
                    # denominator instead of running blind for the whole scrape.
                    run.reported_total = adapter.fetch_total_count()
                # Publish progress and keep the transaction short — a full-site
                # scrape can run for an hour.
                with timer.stage("db"):
                    session.commit()
                last_progress = time.monotonic()
                # Stop requests are honoured here, right after a commit, so a
                # cancelled run keeps every vehicle it already reconciled.
                if is_cancelled(dealer.id):
                    raise ScrapeCancelled()
            # A vehicle the feed marks unpublished is not on the dealer's site,
            # so it is not inventory. Skipping it here (rather than storing a
            # fourth status the UI would have to learn) also means a listing
            # that gets unpublished is picked up by the normal removal pass,
            # because it never joins `seen_car_ids`.
            if v.source_status == "hidden":
                # Counted, not stored: the UI shows how many rows of the feed were
                # ignored on purpose, so "1,090 scraped" next to a 104-vehicle
                # website explains itself (Eco Cars: 952 Archive + 34 Unpublished).
                timer.incr("hidden")
                continue

            with timer.stage("db"):
                h = v.content_hash()
                car = index.match(v)

                if car is None:  # ---- new listing ----
                    # A feed that states its own lifecycle is believed over the
                    # default. Importing Cardora's 482 Sold rows as "active"
                    # would have inflated live inventory threefold; they are
                    # real history worth keeping, just not as available stock.
                    initial_status = v.source_status or "active"
                    car = Car(
                        dealer_id=dealer.id, external_id=v.external_id, content_hash=h,
                        status=initial_status, first_seen_at=_now(), last_seen_at=_now(),
                        sold_at=_now() if initial_status == "sold" else None,
                        # Seeded at creation so the column is never blank in the UI;
                        # it only moves again on a real content change.
                        updated_at=_now(),
                    )
                    _apply_fields(car, v)
                    car.raw_payload = v.raw_payload
                    session.add(car)
                    session.flush()  # obtain car.id for FKs
                    if v.price is not None:
                        session.add(CarPriceHistory(car_id=car.id, price=v.price, currency=v.currency))
                    _audit(session, car, dealer.id, "created", None, initial_status)
                    index.add(car)
                    seen_car_ids.add(car.id)
                    run.new_count += 1
                    continue

                # ---- existing listing ----
                if car.id in seen_car_ids:
                    # Same vehicle surfaced twice in one scrape (e.g. paging overlap).
                    # Already reconciled above — do not double-count.
                    duplicate_count += 1
                    continue
                seen_car_ids.add(car.id)

                # A feed-stated lifecycle change is the one case where a sale is
                # OBSERVED rather than inferred, so it is applied immediately
                # instead of waiting out SOLD_AFTER_DAYS. `sold_at` is stamped
                # once, on the transition, so the date means "when the dealer
                # first published it as sold", not "when we last looked".
                if v.source_status and v.source_status != car.status:
                    previous = car.status
                    car.status = v.source_status
                    if v.source_status == "sold":
                        car.sold_at = car.sold_at or _now()
                        run.sold_count += 1
                    elif v.source_status == "active":
                        car.sold_at = None
                        car.removed_at = None
                        run.reappeared_count += 1
                    _audit(session, car, dealer.id, "status_changed",
                           old_status=previous, new_status=v.source_status,
                           note=f"feed reported {previous} -> {v.source_status}")

                # Field-level change events (SOW §5 audit trail).
                if v.price is not None and v.price != car.price:
                    session.add(CarPriceHistory(car_id=car.id, price=v.price, currency=v.currency))
                    _audit(session, car, dealer.id, "price_changed",
                           note=f"{car.price} -> {v.price}",
                           old_value=car.price, new_value=v.price)
                    run.price_changed_count += 1

                if v.mileage is not None and v.mileage != car.mileage:
                    _audit(session, car, dealer.id, "mileage_changed",
                           note=f"{car.mileage} -> {v.mileage}",
                           old_value=car.mileage, new_value=v.mileage)

                if v.condition and v.condition != car.condition:
                    _audit(session, car, dealer.id, "status_changed",
                           note=f"{car.condition} -> {v.condition}")

                # The provider may re-stock the same VIN under a new stock number.
                # Adopt it only when no other row already owns it (uq_car_dealer_external).
                if v.external_id and v.external_id != car.external_id:
                    holder = index.holder_of_external_id(v.external_id)
                    if holder is None:
                        _audit(session, car, dealer.id, "updated",
                               note=f"external_id {car.external_id} -> {v.external_id}")
                        car.external_id = v.external_id
                        index.by_ext.setdefault(v.external_id, car)

                if car.content_hash != h:
                    _apply_fields(car, v)
                    car.raw_payload = v.raw_payload
                    car.content_hash = h
                    # "Last updated" — only here, never on a plain re-sighting.
                    # last_seen_at below moves every day regardless; this is what
                    # tells the client whether the LISTING changed.
                    car.updated_at = _now()
                    _audit(session, car, dealer.id, "updated")
                    run.updated_count += 1

                # Inferred reappearance only. When the feed states a status it has
                # already been applied above, and a vehicle the feed calls Sold
                # must stay sold: without this guard every Sold row was stored
                # as sold on one run and flipped back to active on the next,
                # forever (Cardora: 510 flips per run, inventory inflated by 520).
                if v.source_status is None and car.status in ("removed", "sold"):  # reappeared
                    old = car.status
                    car.status = "active"
                    car.removed_at = None
                    car.sold_at = None
                    _audit(session, car, dealer.id, "reactivated", old, "active")
                    run.reappeared_count += 1

                car.last_seen_at = _now()

        if run.reported_total is None:
            run.reported_total = adapter.fetch_total_count()

    except ScrapeCancelled:   # operator pressed Stop -> keep what we scraped
        # Deliberately BEFORE the generic handler: a cancellation is an outcome,
        # not a failure, and must not be reported as a broken scraper.
        clear_cancel(dealer.id)
        run.status = "cancelled"
        run.finished_at = _now()
        # Removals are skipped for the same reason a failed run skips them: we
        # only saw part of the inventory, so absence proves nothing.
        _finish_dealer(dealer, run, sync_status="cancelled", refresh_count=True,
                       session=session)
        _write_snapshot(session, dealer, run, sync_status="cancelled")
        _log_report(timer, dealer, run)
        session.commit()
        log.info("Dealer %s scrape cancelled after %s vehicles", dealer.id, run.scraped_count)
        return run

    except Exception as exc:  # scrape/parse failure -> do NOT touch removals
        complete = False
        run.status = "failed"
        run.error = f"{type(exc).__name__}: {exc}"[:1000]
        run.finished_at = _now()
        _finish_dealer(dealer, run, sync_status="failed", refresh_count=False)
        _write_snapshot(session, dealer, run, sync_status="failed")
        _log_report(timer, dealer, run)
        session.commit()
        clear_cancel(dealer.id)
        log.exception("Dealer %s scrape failed", dealer.id)
        return run

    # ---- A run that found nothing is a failure, never a success ----
    # Previously this was only caught when the dealer ALREADY had stock, so a
    # first-ever sync that scraped nothing reported "completed / success" and the
    # dealer sat at 0 vehicles looking healthy. A listing page that yields zero
    # vehicles is essentially always a broken config or a site we cannot read —
    # not an empty lot — so it must say so.
    if run.scraped_count == 0:
        run.status = "incomplete"
        run.error = run.error or (
            "Scraped 0 vehicles. The scraper reached the listing page but extracted "
            "nothing, so the inventory was left untouched (no vehicles were marked "
            "removed). Usually the listing renders client-side and needs more time, "
            "or the configured id_regex does not match this site's vehicle URLs."
        )
        run.finished_at = _now()
        _finish_dealer(dealer, run, sync_status="incomplete", refresh_count=True,
                       session=session)
        _write_snapshot(session, dealer, run, sync_status="incomplete")
        session.commit()
        clear_cancel(dealer.id)
        log.error("Dealer %s scraped 0 vehicles — reported incomplete", dealer.id)
        return run

    # ---- A run that extracted nothing usable is not a success ----
    # Rows with a link and an id but no year/make/model/price are what a wrong
    # card_selector produces: the anchors are found, so the count looks healthy,
    # and every field is NULL. Observed live on cardora.ca — 528 vehicles,
    # "success", and not one populated field. Counting rows cannot catch that;
    # counting rows that actually describe a vehicle can.
    described_fraction = described_count / run.scraped_count if run.scraped_count else 1.0
    if described_fraction < settings.min_described_fraction:
        run.status = "incomplete"
        run.error = run.error or (
            f"Scraped {run.scraped_count} vehicles but only {described_count} had any "
            f"details (year, make, model or price) — the listing was found but its "
            f"cards could not be read, so the inventory would be a list of empty "
            f"records. Usually 'card_selector' is missing or wrong for this site."
        )
        run.finished_at = _now()
        _finish_dealer(dealer, run, sync_status="incomplete", refresh_count=True,
                       session=session)
        _write_snapshot(session, dealer, run, sync_status="incomplete")
        _log_report(timer, dealer, run)
        session.commit()
        clear_cancel(dealer.id)
        log.error("Dealer %s scraped %s vehicles but only %s described — reported incomplete",
                  dealer.id, run.scraped_count, described_count)
        return run

    # ---- A run that mostly re-read the same vehicles is not a success ----
    # A feed whose page parameter is ignored returns page 1 every time: the
    # row count climbs (120 scraped), the distinct vehicles do not (12 stored),
    # and the run reports success with a quarter of the inventory.
    if (
        run.scraped_count >= settings.min_rows_for_duplicate_check
        and duplicate_count > run.scraped_count * settings.max_duplicate_fraction
    ):
        run.status = "incomplete"
        distinct = run.scraped_count - duplicate_count
        if dealer.adapter_mode == "browser":
            # A browser listing is read once per page, so collapsed rows mean the
            # ids collided, not that pages repeated: an id_regex capturing a fixed
            # word ("/cars/used/<slug>" -> "used") gives every vehicle one id.
            cause = (
                "different vehicles were given the same id. Usually 'id_regex' "
                "captures a fixed part of the detail URL (e.g. 'used' in "
                "/cars/used/<vehicle>) instead of the part unique to each vehicle"
                if distinct <= 1 else
                "the same pages were read repeatedly, or 'id_regex' gives several "
                "vehicles the same id")
        else:
            cause = (
                "the same pages were read repeatedly. Usually the endpoint has no "
                "{page} placeholder, or the site ignores its page parameter")
        run.error = run.error or (
            f"Scraped {run.scraped_count} rows but only {distinct} were distinct "
            f"vehicles — {cause}. No vehicles were marked removed."
        )
        run.finished_at = _now()
        _finish_dealer(dealer, run, sync_status="incomplete", refresh_count=True,
                       session=session)
        _write_snapshot(session, dealer, run, sync_status="incomplete")
        _log_report(timer, dealer, run)
        session.commit()
        clear_cancel(dealer.id)
        log.error("Dealer %s: %s of %s rows were repeats — reported incomplete",
                  dealer.id, duplicate_count, run.scraped_count)
        return run

    # ---- Removal reconciliation (safety-gated) ----
    # A scrape that returned nothing while we hold active inventory is virtually
    # always a broken selector / blocked request, not an emptied lot. Treat it as
    # incomplete so a silent breakage can never wipe a dealer's inventory.
    empty_but_had_stock = run.scraped_count == 0 and bool(active_at_start)

    # The site's own "N results" count is the best expectation when we have it,
    # but plenty of listings never publish one. Falling straight through to
    # "reconciled" on reported_total IS None was a hole big enough to wipe a
    # dealer: a config that silently degrades to page 1 only (see the clutch.ca
    # candidate-selection bug) scrapes ~1% of the lot, passes unchallenged, and
    # marks the other 99% removed — then sold, SOLD_AFTER_DAYS later. So when
    # the site gives us no total, fall back to what we already hold: a run may
    # not shrink a dealer below reconcile_min_fraction of its own active
    # inventory without being called incomplete.
    expected_total = run.reported_total
    expectation_source = "site"
    if expected_total is None and active_at_start:
        expected_total = len(active_at_start)
        expectation_source = "prior_inventory"

    reconciled = (
        not empty_but_had_stock
        and (
            expected_total is None
            or run.scraped_count >= expected_total * settings.reconcile_min_fraction
        )
    )
    if complete and reconciled:
        by_id = {c.id: c for c in all_cars}
        for car_id in (active_at_start - seen_car_ids):
            car = by_id[car_id]
            car.status = "removed"
            car.removed_at = _now()
            _audit(session, car, dealer.id, "removed", "active", "removed")
            run.removed_count += 1
        _mark_sold(session, dealer, run)
        run.status = "completed"
        sync_status = "success"
    else:
        run.status = "incomplete"
        sync_status = "incomplete"
        if not run.error:
            if expectation_source == "prior_inventory":
                run.error = (
                    f"Scraped {run.scraped_count} vehicles but this dealer already held "
                    f"{expected_total} active — too large a drop to trust. The listing page "
                    f"publishes no total, so the previous inventory was used as the "
                    f"expectation. No vehicles were marked removed. Usually the scrape "
                    f"stopped after the first page (check the pagination config) or the "
                    f"listing was only partly rendered."
                )
            else:
                run.error = (
                    f"Scraped {run.scraped_count} of the {expected_total} vehicles the site "
                    f"reports — below the {settings.reconcile_min_fraction:.0%} threshold, so "
                    f"no vehicles were marked removed."
                )
        log.warning(
            "Dealer %s INCOMPLETE: scraped %s of expected %s (source=%s) — removals/sold skipped",
            dealer.id, run.scraped_count, expected_total, expectation_source,
        )

    # ---- Dealer denormalized fields + daily snapshot ----
    run.finished_at = _now()
    _finish_dealer(dealer, run, sync_status=sync_status, refresh_count=True,
                   session=session)
    _write_snapshot(session, dealer, run, sync_status=sync_status)
    _log_report(timer, dealer, run)
    session.commit()
    # A stop that lost the race with the final commit must not carry into the
    # next run.
    clear_cancel(dealer.id)
    return run


def _finish_dealer(dealer: Dealer, run: ScrapeRun, sync_status: str,
                   refresh_count: bool, session: Session | None = None) -> None:
    """Denormalized 'last sync' block on the dealer row — powers the admin list and
    the Dealer Overview card without joining scrape_runs."""
    dealer.last_synced_at = run.finished_at or _now()
    dealer.last_sync_status = sync_status
    dealer.last_sync_duration_seconds = _duration_seconds(run)
    if refresh_count and session is not None:
        dealer.inventory_count = _active_count(session, dealer.id)


def _duration_seconds(run: ScrapeRun) -> float | None:
    if not run.started_at or not run.finished_at:
        return None
    return round((run.finished_at - run.started_at).total_seconds(), 3)


def _log_report(timer: StageTimer, dealer: Dealer, run: ScrapeRun) -> None:
    """Persist the stage-timing snapshot on the run and log the formatted
    report — called on every terminal path (completed/incomplete/cancelled/
    failed) so a broken or slow scrape is diagnosable from its own row."""
    run.stats = timer.snapshot()
    total = _duration_seconds(run)
    text = timer.report_text(
        header={"Dealer": f"{dealer.name} (#{dealer.id})", "Run": f"#{run.id}",
                "Status": run.status},
        total_seconds=total if total is not None else timer.elapsed(),
        vehicles=run.scraped_count,
    )
    log.info("\n%s", text)


def _mark_sold(session: Session, dealer: Dealer, run: ScrapeRun) -> None:
    """SOW §4 'Mark as Sold': a car removed for longer than SOLD_AFTER_DAYS and
    still absent is treated as sold. (Business rule — configurable / overridable
    if the client later supplies confirmed dealer sale data.)"""
    if settings.sold_after_days is None:
        return
    cutoff = _now() - timedelta(days=settings.sold_after_days)
    stale_removed = session.execute(
        select(Car).where(
            Car.dealer_id == dealer.id, Car.status == "removed", Car.removed_at < cutoff,
        )
    ).scalars().all()
    for car in stale_removed:
        car.status = "sold"
        car.sold_at = _now()
        _audit(session, car, dealer.id, "sold", "removed", "sold",
               note=f"auto: removed >{settings.sold_after_days}d")
        run.sold_count += 1


def _active_count(session: Session, dealer_id: int) -> int:
    # The session is autoflush=False, so this run's status changes (sold,
    # removed) are still pending here. Without the flush the count describes the
    # database as it stood BEFORE the run — the dashboard total lagged one run
    # behind and never matched the Inventory page.
    session.flush()
    return session.execute(
        select(func.count()).select_from(Car).where(
            Car.dealer_id == dealer_id, Car.status == "active")
    ).scalar_one()


def _write_snapshot(session: Session, dealer: Dealer, run: ScrapeRun, sync_status: str) -> None:
    """Upsert one DailyInventorySnapshot per dealer per day (SOW §6/§7)."""
    today = _now().date()
    snap = session.execute(
        select(DailyInventorySnapshot).where(
            DailyInventorySnapshot.dealer_id == dealer.id,
            DailyInventorySnapshot.snapshot_date == today,
        )
    ).scalar_one_or_none()
    if snap is None:
        snap = DailyInventorySnapshot(dealer_id=dealer.id, snapshot_date=today)
        session.add(snap)
    snap.total_active = _active_count(session, dealer.id)
    snap.added = run.new_count
    snap.removed = run.removed_count
    snap.sold = run.sold_count
    snap.price_changed = run.price_changed_count
    snap.sync_status = sync_status
    snap.run_id = run.id
