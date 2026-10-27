"""DiscoveryRun DB row lifecycle: create -> claim (running) -> progress commits
-> finalize (completed/failed/cancelled). Mirrors engine.run_dealer's short,
frequent-commit pattern (dealer_scraper/engine.py) so GET .../discover/runs/{id}
polling works identically whether the run executes on a local thread or a
remote Celery worker — the DB row is the only thing both backends share.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..cancel import clear_discovery_cancel, is_discovery_cancelled
from ..config import settings
from ..db import SessionLocal
from ..models import DiscoveryRun
from .context import DiscoveryCancelled
from .coordinator import DiscoveryCoordinator, DiscoveryResult
from .heuristics import HeuristicsConfig

log = logging.getLogger("scraper.discovery.persistence")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def create_run(session: Session, base_url: str, dealer_id: int | None = None) -> DiscoveryRun:
    run = DiscoveryRun(base_url=base_url, dealer_id=dealer_id, status="queued", phases_total=4)
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def find_in_flight(session: Session, base_url: str) -> DiscoveryRun | None:
    """An existing queued/running run for this exact base_url, if any — used to
    dedupe a repeated 'Detect settings' click into the same run instead of
    starting a second browser session for the same site.

    Treats runs as stale if they've been running too long OR if they're queued
    but never started (zombie-run bug: when a host lock times out, run_discovery
    is never called but the row remains queued forever, blocking future attempts).
    """
    stmt = (
        select(DiscoveryRun)
        .where(DiscoveryRun.base_url == base_url, DiscoveryRun.status.in_(("queued", "running")))
        .order_by(DiscoveryRun.id.desc())
        .limit(1)
    )
    run = session.execute(stmt).scalar_one_or_none()
    if run is None:
        return None

    # Check if the run is stale (either started and ran too long, or queued but never started)
    now = _now()
    timeout_sec = settings.discovery_budget_seconds * 3

    if run.started_at is not None:
        # Run started: check if it ran too long
        started = run.started_at
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        age = (now - started).total_seconds()
    else:
        # Run never started: check if it's been queued too long (zombie-run bug)
        created = run.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        age = (now - created).total_seconds()

    if age > timeout_sec:
        log.warning("Ignoring stale discovery run #%s for %s (%.0fs old, status=%s)",
                    run.id, base_url, age, run.status)
        return None

    return run


def run_discovery(run_id: int) -> dict:
    """Entry point for both dispatch backends (local thread pool / Celery task).

    Claims the row, runs the coordinator with progress/cancel wired to the row
    and the discovery cancel channel (dealer_scraper.cancel), and finalizes the
    row no matter how the run ends. Returns a small summary dict — the full
    DiscoveryReport lives on the row's `report` column, never in the task
    return value (which Celery serializes through its result backend).
    """
    clear_discovery_cancel(run_id)

    with SessionLocal() as session:
        run = session.get(DiscoveryRun, run_id)
        if run is None:
            return {"status": "failed", "run_id": run_id, "error": "Discovery run not found"}
        run.status = "running"
        run.started_at = _now()
        session.commit()
        base_url = run.base_url

    def _progress(phase: str, done: int, total: int) -> None:
        try:
            with SessionLocal() as s:
                r = s.get(DiscoveryRun, run_id)
                if r is not None:
                    r.phase = phase
                    r.phases_done = done
                    r.phases_total = total
                    s.commit()
        except Exception:
            log.warning("Could not persist discovery progress for run %s", run_id, exc_info=True)

    def _check_cancel() -> None:
        if is_discovery_cancelled(run_id):
            raise DiscoveryCancelled(f"Discovery run {run_id} was cancelled")

    heuristics = HeuristicsConfig.from_overrides(settings.discovery_heuristics_overrides)
    coordinator = DiscoveryCoordinator(
        base_url, heuristics, budget_seconds=settings.discovery_budget_seconds,
        progress=_progress, check_cancel=_check_cancel,
    )

    try:
        result = coordinator.run()
    except Exception as exc:  # coordinator.run() itself never raises except for
        # a bug in the coordinator's own bookkeeping — DiscoveryCancelled and
        # every discovery-internal failure are already caught inside run().
        log.exception("Discovery run %s crashed outside the coordinator's own handling", run_id)
        _finalize(run_id, status="failed", result=None, error=str(exc)[:1000])
        return {"status": "failed", "run_id": run_id, "error": str(exc)[:1000]}

    status = "cancelled" if is_discovery_cancelled(run_id) else "completed"
    _finalize(run_id, status=status, result=result, error=None)
    log.info("Discovery run %s finished dealer_base=%s -> %s (%s)", run_id, base_url, status, result.verdict)
    return {"status": status, "run_id": run_id, "verdict": result.verdict}


#: Statuses a run can never move out of — anything else means nobody finished it.
TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})


def fail_unfinished_run(run_id: int, error: str) -> bool:
    """Mark a run failed if nothing else has finalized it. Returns whether it did.

    The dispatch backends acquire a per-host lock *before* calling
    run_discovery(), which is the only thing that moves a row off `queued`. So
    anything that goes wrong in between — most commonly a host-lock timeout —
    used to leave the row at `queued` with no error, forever. The UI polls that
    row, so the operator sees "detecting…" indefinitely with nothing to act on.
    Observed live on grcars.ca after a killed run left its lock orphaned.

    Both backends call this on every failure path, so a run always reaches a
    terminal state with a written reason no matter where it died.
    """
    with SessionLocal() as session:
        run = session.get(DiscoveryRun, run_id)
        if run is None or run.status in TERMINAL_STATUSES:
            return False
        run.status = "failed"
        run.finished_at = _now()
        run.error = error[:1000]
        session.commit()
    clear_discovery_cancel(run_id)
    log.warning("Discovery run %s finalized as failed: %s", run_id, error)
    return True


def _finalize(run_id: int, *, status: str, result: DiscoveryResult | None, error: str | None) -> None:
    with SessionLocal() as session:
        run = session.get(DiscoveryRun, run_id)
        if run is None:
            return
        run.status = status
        run.finished_at = _now()
        if result is not None:
            run.confidence = result.confidence
            run.verdict = result.verdict
            run.requires_custom_work = result.requires_custom_work
            run.adapter_mode = result.adapter_mode
            run.scrape_config = result.scrape_config
            run.vehicles_verified = result.vehicles_found
            run.result = result.as_dict()
            run.report = result.report.model_dump(mode="json") if result.report is not None else None
            if run.phases_total:
                run.phases_done = run.phases_total
            run.phase = "done"
        if error:
            run.error = error
        session.commit()
    clear_discovery_cancel(run_id)
