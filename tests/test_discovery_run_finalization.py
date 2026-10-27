"""A discovery run must always reach a terminal state with a written reason.

Regression for the grcars.ca "stuck on Detecting…" failure. A killed run left
its per-host Redis lock orphaned (2h TTL). Every later run for that host then
timed out on the lock — and because the lock is taken *before* run_discovery(),
which is the only thing that moves a row off `queued`, the row was never
finalized. The UI polled it forever with no error to show, while runs for other
hosts completed normally.

Both dispatch backends must finalize the row on every failure path.

Run:  DATABASE_URL="sqlite:///./.finalization_test.db" python -m tests.test_discovery_run_finalization
"""
from __future__ import annotations

from dealer_scraper.db import SessionLocal, engine
from dealer_scraper.discovery.persistence import (
    TERMINAL_STATUSES,
    fail_unfinished_run,
)
from dealer_scraper.models import Base, DiscoveryRun

from . import require_disposable_db


def _fresh_run(status: str = "queued") -> int:
    require_disposable_db()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with SessionLocal() as s:
        run = DiscoveryRun(base_url="https://grcars.ca", status=status)
        s.add(run)
        s.commit()
        return run.id


def test_queued_run_is_finalized_with_a_reason() -> None:
    """The exact orphaned-lock case: queued, never started, must not stay queued."""
    run_id = _fresh_run("queued")
    changed = fail_unfinished_run(run_id, "host busy: lock held by another run")

    assert changed is True
    with SessionLocal() as s:
        run = s.get(DiscoveryRun, run_id)
        assert run.status == "failed"
        assert run.finished_at is not None
        assert run.error and "host busy" in run.error


def test_running_run_is_also_finalized() -> None:
    """A run that started and then died mid-flight is just as stuck."""
    run_id = _fresh_run("running")
    assert fail_unfinished_run(run_id, "worker died") is True
    with SessionLocal() as s:
        assert s.get(DiscoveryRun, run_id).status == "failed"


def test_completed_run_is_never_overwritten() -> None:
    """Finalizing must be idempotent and must not clobber a real result — the
    local backend calls this after run_discovery() has already finalized."""
    run_id = _fresh_run("completed")
    with SessionLocal() as s:
        run = s.get(DiscoveryRun, run_id)
        run.verdict = "ready"
        run.error = None
        s.commit()

    assert fail_unfinished_run(run_id, "should be ignored") is False
    with SessionLocal() as s:
        run = s.get(DiscoveryRun, run_id)
        assert run.status == "completed"
        assert run.verdict == "ready"
        assert run.error is None


def test_missing_run_is_handled() -> None:
    _fresh_run()
    assert fail_unfinished_run(999999, "no such run") is False


def test_terminal_statuses_cover_every_finished_state() -> None:
    """If a new terminal status is added without updating this set, finished runs
    would silently get overwritten by a late failure."""
    assert TERMINAL_STATUSES == frozenset({"completed", "failed", "cancelled"})


def test_error_message_is_truncated_to_the_column_width() -> None:
    run_id = _fresh_run("queued")
    fail_unfinished_run(run_id, "x" * 5000)
    with SessionLocal() as s:
        assert len(s.get(DiscoveryRun, run_id).error) <= 1000


def test_discovery_lock_ttl_is_bounded_by_the_discovery_budget() -> None:
    """An orphaned discovery lock must not brick a host for the scrape TTL (2h).

    This is the setting that turned one killed run into every subsequent run for
    that dealer failing, invisibly, until the TTL expired.
    """
    from dealer_scraper.config import settings

    assert settings.discovery_host_lock_ttl < settings.scrape_host_lock_ttl
    assert settings.discovery_host_lock_ttl >= settings.discovery_budget_seconds, (
        "the lock must outlive a legitimately-running discovery, or two runs "
        "for one host could overlap"
    )


def main() -> None:
    test_queued_run_is_finalized_with_a_reason()
    test_running_run_is_also_finalized()
    test_completed_run_is_never_overwritten()
    test_missing_run_is_handled()
    test_terminal_statuses_cover_every_finished_state()
    test_error_message_is_truncated_to_the_column_width()
    test_discovery_lock_ttl_is_bounded_by_the_discovery_budget()
    print("PASSED")


if __name__ == "__main__":
    main()
