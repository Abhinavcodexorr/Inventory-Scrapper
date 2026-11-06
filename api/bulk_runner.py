"""Works through bulk dealer imports in the background.

A thread in the API process ticks every few seconds. Each tick saves the results
of finished checks, starts the next websites while there is room, and closes any
import with nothing left to do. All state is in the database, so a restart only
pauses an import.

It runs in the API rather than as a Celery task so a list keeps moving on both
sync backends, and so releasing a few websites at a time (bulk_import_concurrency)
leaves room in the worker queue for the daily sync. The checks themselves go
through dispatch_discovery, the same path as the Detect settings button.

Rows are claimed with SELECT ... FOR UPDATE SKIP LOCKED, so several API processes
can tick at once without handling a row twice.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from dealer_scraper.adapters.registry import validate_scrape_config
from dealer_scraper.config import settings
from dealer_scraper.config_lint import find_identity_conflict
from dealer_scraper.db import SessionLocal
from dealer_scraper.discovery.net.safe_http import UnsafeUrlError, validate_public_url
from dealer_scraper.discovery.persistence import (
    TERMINAL_STATUSES, create_run, fail_unfinished_run, find_in_flight,
)
from dealer_scraper.models import BulkImport, BulkImportRow, Dealer, DiscoveryRun
from worker.dispatch import choose_backend, dispatch_discovery

from .routers.dealers import _find_same_site

log = logging.getLogger("api.bulk_runner")

TICK_SECONDS = 5.0
NO_WORKER_NOTICE = "Paused: no background worker is running. This list resumes when one starts."

_stop = threading.Event()
_thread: threading.Thread | None = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _age_seconds(moment: datetime | None) -> float:
    if moment is None:
        return 0.0
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return (_now() - moment).total_seconds()


def _claim(db: Session, row_id: int, status: str) -> BulkImportRow | None:
    """Lock a row if it is still in `status`. None when another process has it."""
    return db.execute(
        select(BulkImportRow)
        .where(BulkImportRow.id == row_id, BulkImportRow.status == status)
        .with_for_update(skip_locked=True)
    ).scalar_one_or_none()


def _close(row: BulkImportRow, status: str, reason: str | None) -> None:
    row.status = status
    row.reason = reason[:1000] if reason else None
    row.finished_at = _now()


# ----------------------------------------------------------- finished checks
def _failure_reason(run: DiscoveryRun) -> str:
    if run.status == "cancelled":
        return "The website check was cancelled."
    summary = (run.result or {}).get("summary")
    return run.error or summary or "The scraper could not find any vehicles on this website."


def _add_dealer(db: Session, row: BulkImportRow, run: DiscoveryRun) -> None:
    """Save the dealer if the check proved the site readable, with the same rules
    as adding one by hand: vehicles verified, a valid config, no shared inventory."""
    existing = _find_same_site(db, row.website)
    if existing is not None:
        row.dealer_id = existing.id
        _close(row, "duplicate", f"Already in the tool as {existing.name}.")
        return

    row.vehicles_found = run.vehicles_verified or 0
    if run.status != "completed" or row.vehicles_found < 1 or not run.scrape_config:
        _close(row, "failed", _failure_reason(run))
        return

    mode = run.adapter_mode or "browser"
    try:
        validate_scrape_config(mode, run.scrape_config, row.website)
    except ValueError as exc:
        _close(row, "failed", f"The detected settings were not usable: {exc}")
        return
    active = db.execute(
        select(Dealer.id, Dealer.name, Dealer.scrape_config).where(Dealer.active.is_(True))
    )
    conflict = find_identity_conflict(run.scrape_config, ((i, n, c or {}) for i, n, c in active))
    if conflict:
        _close(row, "failed", conflict)
        return

    detected = ((run.report or {}).get("detected_cms") or [None])[0]
    dealer = Dealer(
        name=row.name, base_url=row.website, address=row.address, city=row.city,
        province=row.province, postal_code=row.postal_code, phone=row.phone,
        # The provider the operator chose wins; the fingerprint only fills a blank.
        website_provider=row.provider or detected,
        detected_platform=detected,
        adapter_mode=mode, scrape_config=run.scrape_config, active=True,
    )
    db.add(dealer)
    db.flush()
    row.dealer_id = dealer.id
    _close(row, "added", None)


def _settle_row(db: Session, row_id: int) -> None:
    row = _claim(db, row_id, "checking")
    if row is None:
        return
    run = db.get(DiscoveryRun, row.discovery_run_id) if row.discovery_run_id else None
    if run is None:
        # Died between claiming the row and creating its run.
        row.status, row.started_at = "queued", None
    elif run.status in TERMINAL_STATUSES:
        _add_dealer(db, row, run)
    else:
        # A killed worker leaves its run unfinished forever, which would hold a
        # slot and keep the import open. Failing it here lets the next tick report it.
        run_id, queued = run.id, run.started_at is None
        age = _age_seconds(run.created_at if queued else run.started_at)
        limit = (settings.bulk_import_queued_timeout_seconds if queued
                 else settings.discovery_budget_seconds * 3)
        db.rollback()
        if age > limit:
            fail_unfinished_run(run_id, (
                "The website check never started: the workers stayed busy." if queued
                else "The website check stopped responding and was abandoned."))
        return
    db.commit()


def _settle_finished(db: Session) -> None:
    ids = db.execute(select(BulkImportRow.id).where(BulkImportRow.status == "checking")).scalars().all()
    for row_id in ids:
        try:
            _settle_row(db, row_id)
        except Exception as exc:
            # One bad row must not hold up the rest of the list or stay in flight forever.
            db.rollback()
            log.exception("Could not save bulk import row %s", row_id)
            row = _claim(db, row_id, "checking")
            if row is not None:
                _close(row, "failed", f"Could not save this dealer: {exc}")
                db.commit()


# ------------------------------------------------------------- starting more
def _start_row(db: Session, row_id: int) -> str:
    """Start one website's check. Returns 'started', 'closed' (settled without a
    check, so it used no slot) or 'paused' (nothing can run right now)."""
    row = _claim(db, row_id, "queued")
    if row is None:
        return "closed"
    existing = _find_same_site(db, row.website)
    if existing is not None:
        row.dealer_id = existing.id
        _close(row, "duplicate", f"Already in the tool as {existing.name}.")
        db.commit()
        return "closed"
    website = row.website
    try:
        validate_public_url(website)
    except UnsafeUrlError as exc:
        _close(row, "invalid", str(exc))
        db.commit()
        return "closed"

    row.status, row.started_at = "checking", _now()
    db.commit()

    run = find_in_flight(db, website)       # a Detect settings click may already be on it
    outcome = None
    if run is None:
        run = create_run(db, website)
        outcome = dispatch_discovery(run.id, website)
        if outcome["status"] == "no_worker":
            fail_unfinished_run(run.id, NO_WORKER_NOTICE)
            db.expire_all()
            row = db.get(BulkImportRow, row_id)
            row.status, row.started_at = "queued", None
            db.commit()
            return "paused"
    row = db.get(BulkImportRow, row_id)
    row.discovery_run_id = run.id
    row.dispatch_mode = outcome["mode"] if outcome else None
    db.commit()
    return "started"


def _start_next(db: Session) -> None:
    room = settings.bulk_import_concurrency - (db.scalar(
        select(func.count()).select_from(BulkImportRow).where(BulkImportRow.status == "checking")) or 0)
    if room <= 0:
        return
    paused = choose_backend() == "no_worker"
    _set_notice(db, NO_WORKER_NOTICE if paused else None)
    if paused:
        return

    # Oldest list first, in the order the operator wrote it. Only a running import
    # qualifies: stopping one already skipped its waiting rows.
    candidates = db.execute(
        select(BulkImportRow.id)
        .join(BulkImport, BulkImport.id == BulkImportRow.import_id)
        .where(BulkImportRow.status == "queued", BulkImport.status == "running")
        .order_by(BulkImportRow.import_id, BulkImportRow.position)
        # Rows closed without a check take no slot, so look past the room there is.
        .limit(room + 50)
    ).scalars().all()
    for row_id in candidates:
        if room <= 0:
            return
        try:
            result = _start_row(db, row_id)
        except Exception:
            # The row stays where it is and is picked up again on a later tick.
            db.rollback()
            log.exception("Could not start bulk import row %s", row_id)
            return
        if result == "paused":
            _set_notice(db, NO_WORKER_NOTICE)
            return
        if result == "started":
            room -= 1


def _set_notice(db: Session, notice: str | None) -> None:
    for job in db.execute(select(BulkImport).where(BulkImport.status == "running")).scalars():
        job.notice = notice
    db.commit()


def _finish_imports(db: Session) -> None:
    open_rows = select(BulkImportRow.id).where(
        BulkImportRow.import_id == BulkImport.id,
        BulkImportRow.status.in_(("queued", "checking")),
    ).exists()
    jobs = db.execute(
        select(BulkImport).where(BulkImport.status.in_(("running", "stopping")), ~open_rows)
    ).scalars().all()
    for job in jobs:
        job.status = "stopped" if job.status == "stopping" else "completed"
        job.notice = None
        job.finished_at = _now()
        log.info("Bulk import %s finished: %s", job.id, job.status)
    db.commit()


def tick() -> None:
    """One pass over everything in flight."""
    with SessionLocal() as db:
        _settle_finished(db)
        _start_next(db)
        _settle_finished(db)      # a run reused from Detect settings may already be done
        _finish_imports(db)


# ------------------------------------------------------------ the background loop
def recover_local_rows() -> None:
    """Requeue checks that were running inside a previous API process. In-process
    jobs die with it, and otherwise each would sit out the full timeout first.
    Checks on Celery survive a restart, so those are left alone."""
    with SessionLocal() as db:
        rows = db.execute(select(BulkImportRow).where(
            BulkImportRow.status == "checking", BulkImportRow.dispatch_mode == "local",
        )).scalars().all()
        run_ids = [r.discovery_run_id for r in rows if r.discovery_run_id]
        for row in rows:
            row.status, row.started_at, row.discovery_run_id, row.dispatch_mode = "queued", None, None, None
        db.commit()
    for run_id in run_ids:
        fail_unfinished_run(run_id, "The server restarted during this website check.")


def _loop() -> None:
    try:
        recover_local_rows()
    except Exception:
        log.warning("Could not recover in-process bulk import rows", exc_info=True)
    while not _stop.wait(TICK_SECONDS):
        try:
            tick()
        except Exception:
            # A database blip must not end the loop: nothing would restart it,
            # and imports would stall silently until the next deploy.
            log.warning("Bulk import tick failed; retrying", exc_info=True)


def start() -> None:
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="bulk-import-runner", daemon=True)
    _thread.start()


def stop() -> None:
    _stop.set()
