"""Tests for the DiscoveryRun DB row lifecycle (persistence.py): create, claim,
progress commits, cancellation, and finalize on success/failure — plus the
dealer-purge FK-safety behavior (discovery_runs.dealer_id is nulled, not
cascade-deleted).

The coordinator itself is monkeypatched (DiscoveryCoordinator._run_browser_phase)
so this suite never touches Playwright.

Run:  DATABASE_URL="sqlite:///./discovery_persistence_test.db" python -m tests.test_discovery_persistence
"""
from __future__ import annotations

from dealer_scraper.cancel import request_discovery_cancel
from dealer_scraper.db import SessionLocal, engine
from dealer_scraper.discovery import coordinator as coordinator_mod, persistence
from dealer_scraper.discovery.context import DiscoveryCancelled
from dealer_scraper.models import Base, Dealer, DiscoveryRun
from . import require_disposable_db


def _patch_success(vehicles_found: int = 42) -> None:
    def _run(self, ctx, result):
        self.progress("explore", 1, 4)
        result.ok = True
        result.confidence = "high"
        result.verdict = "ready"
        result.requires_custom_work = False
        result.summary = "fake"
        result.scrape_config = {"endpoint": "https://x", "field_map": {"vin": "vin"}}
        result.adapter_mode = "json_api"
        result.vehicles_found = vehicles_found
        ctx.report.add_note("persistence test note")

    coordinator_mod.DiscoveryCoordinator._run_browser_phase = _run


def _patch_failure() -> None:
    def _run(self, ctx, result):
        raise RuntimeError("simulated coordinator crash")

    coordinator_mod.DiscoveryCoordinator._run_browser_phase = _run


def _patch_cancel_aware() -> None:
    def _run(self, ctx, result):
        self.progress("explore", 1, 4)
        for _ in range(50):
            ctx.check_cancel()
            import time
            time.sleep(0.05)
        result.ok = True

    coordinator_mod.DiscoveryCoordinator._run_browser_phase = _run


def test_create_run_and_find_in_flight():
    with SessionLocal() as s:
        run = persistence.create_run(s, "https://example.com")
        assert run.status == "queued"
        assert run.phases_total > 0

        found = persistence.find_in_flight(s, "https://example.com")
        assert found is not None and found.id == run.id

        assert persistence.find_in_flight(s, "https://not-this-one.example") is None


def test_run_discovery_success_populates_row():
    _patch_success(vehicles_found=88)
    with SessionLocal() as s:
        run = persistence.create_run(s, "https://success.example")
        run_id = run.id

    outcome = persistence.run_discovery(run_id)
    assert outcome["status"] == "completed"
    assert outcome["verdict"] == "ready"

    with SessionLocal() as s:
        row = s.get(DiscoveryRun, run_id)
        assert row.status == "completed"
        assert row.confidence == "high"
        assert row.verdict == "ready"
        assert row.vehicles_verified == 88
        assert row.result["vehicles_found"] == 88
        assert row.report is not None
        assert "persistence test note" in row.report["notes"]
        assert row.phases_done == row.phases_total
        assert row.started_at is not None and row.finished_at is not None


def test_run_discovery_internal_crash_completes_with_failed_confidence():
    # DiscoveryCoordinator.run() never raises (see test_discovery_coordinator.py)
    # — a crash inside _run_browser_phase is caught internally and reported as
    # a normal, completed DiscoveryResult with confidence='failed', not a
    # persistence-level failure. persistence.run_discovery()'s own "failed"
    # status is reserved for something going wrong OUTSIDE coordinator.run()
    # itself (see the next test).
    _patch_failure()
    with SessionLocal() as s:
        run = persistence.create_run(s, "https://crash.example")
        run_id = run.id

    outcome = persistence.run_discovery(run_id)
    assert outcome["status"] == "completed"
    assert outcome["verdict"] == "unsupported"

    with SessionLocal() as s:
        row = s.get(DiscoveryRun, run_id)
        assert row.status == "completed"
        assert row.confidence == "failed"
        assert row.result is not None
        assert row.result["confidence"] == "failed"
        assert any("simulated coordinator crash" in n for n in row.report["notes"])


def test_run_discovery_reports_failed_status_if_coordinator_run_itself_raises():
    # A bug in the coordinator's OWN bookkeeping (as opposed to inside
    # _run_browser_phase, which run() already guards) is the one thing
    # persistence.run_discovery()'s outer try/except exists to catch.
    original_run = coordinator_mod.DiscoveryCoordinator.run

    def _broken_run(self):
        raise RuntimeError("simulated coordinator.run() bug")

    coordinator_mod.DiscoveryCoordinator.run = _broken_run
    try:
        with SessionLocal() as s:
            run = persistence.create_run(s, "https://outer-crash.example")
            run_id = run.id

        outcome = persistence.run_discovery(run_id)
        assert outcome["status"] == "failed"
        assert "simulated coordinator.run() bug" in outcome["error"]

        with SessionLocal() as s:
            row = s.get(DiscoveryRun, run_id)
            assert row.status == "failed"
            assert row.error is not None
    finally:
        coordinator_mod.DiscoveryCoordinator.run = original_run


def test_run_discovery_missing_row_is_reported_not_raised():
    outcome = persistence.run_discovery(999999)
    assert outcome["status"] == "failed"
    assert "not found" in outcome["error"]


def test_run_discovery_cancellation_marks_row_cancelled():
    # request_discovery_cancel() must target an ALREADY-RUNNING run — run_discovery()
    # clears any stale flag as its first step (a new run always starts
    # uncancelled), so the cancel request has to land while it's in flight.
    import threading
    import time

    _patch_cancel_aware()
    with SessionLocal() as s:
        run = persistence.create_run(s, "https://cancelme.example")
        run_id = run.id

    outcome_holder: dict = {}

    def _run_it():
        outcome_holder["outcome"] = persistence.run_discovery(run_id)

    thread = threading.Thread(target=_run_it)
    thread.start()
    time.sleep(0.3)  # let it get past clear_discovery_cancel() and into the loop
    request_discovery_cancel(run_id)
    thread.join(timeout=10)

    assert not thread.is_alive(), "run_discovery did not stop after cancellation"
    assert outcome_holder["outcome"]["status"] == "cancelled"

    with SessionLocal() as s:
        row = s.get(DiscoveryRun, run_id)
        assert row.status == "cancelled"


def test_dealer_purge_nulls_discovery_run_dealer_id_not_delete():
    with SessionLocal() as s:
        dealer = Dealer(name="PurgeMe", base_url="https://purgeme.example",
                         adapter_mode="stub", scrape_config={})
        s.add(dealer)
        s.commit()
        dealer_id = dealer.id
        run = persistence.create_run(s, "https://purgeme.example", dealer_id=dealer_id)
        run_id = run.id

        # Mirrors api/routers/dealers.py::delete_dealer's null-out step.
        from sqlalchemy import update

        s.execute(update(DiscoveryRun).where(DiscoveryRun.dealer_id == dealer_id).values(dealer_id=None))
        s.delete(dealer)
        s.commit()

    with SessionLocal() as s:
        row = s.get(DiscoveryRun, run_id)
        assert row is not None, "discovery history must survive dealer deletion"
        assert row.dealer_id is None
        assert s.get(Dealer, dealer_id) is None


def main() -> None:
    require_disposable_db()   # drop_all() below — never let this hit a real DB
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)

    test_create_run_and_find_in_flight()
    test_run_discovery_success_populates_row()
    test_run_discovery_internal_crash_completes_with_failed_confidence()
    test_run_discovery_missing_row_is_reported_not_raised()
    test_run_discovery_cancellation_marks_row_cancelled()
    test_dealer_purge_nulls_discovery_run_dealer_id_not_delete()
    test_run_discovery_reports_failed_status_if_coordinator_run_itself_raises()
    print("DISCOVERY PERSISTENCE TEST PASSED")


if __name__ == "__main__":
    main()
