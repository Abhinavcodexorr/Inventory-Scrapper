"""Runs left 'running' by a killed worker are closed; live runs are never touched.

    DATABASE_URL="sqlite:///./reap_test.db" API_KEY=test-key python -m tests.test_reap_runs
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from dealer_scraper.db import SessionLocal, engine
from dealer_scraper.engine import ABANDONED_RUN_ERROR, reap_abandoned_runs
from dealer_scraper.models import Base, Dealer, ScrapeRun
from . import require_disposable_db


def _setup():
    require_disposable_db()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


def _dealer(s, name):
    d = Dealer(name=name, base_url=f"https://{name}.example", adapter_mode="json_api",
               scrape_config={}, active=True)
    s.add(d)
    s.flush()
    return d


def _run(s, d, status, age):
    r = ScrapeRun(dealer_id=d.id, status=status, started_at=datetime.now(timezone.utc) - age)
    s.add(r)
    s.flush()
    return r


def test_superseded_run_is_closed():
    """The Rj1 case: old run stuck 'running', a later run completed."""
    _setup()
    with SessionLocal() as s:
        d = _dealer(s, "rj1")
        old = _run(s, d, "running", timedelta(minutes=30))
        _run(s, d, "completed", timedelta(minutes=10))
        assert reap_abandoned_runs(s) == 1
        s.refresh(old)
        assert old.status == "failed" and old.finished_at is not None
        assert old.error == ABANDONED_RUN_ERROR


def test_live_run_is_kept():
    _setup()
    with SessionLocal() as s:
        d = _dealer(s, "live")
        live = _run(s, d, "running", timedelta(minutes=20))
        assert reap_abandoned_runs(s) == 0
        s.refresh(live)
        assert live.status == "running"


def test_stale_run_is_closed():
    _setup()
    with SessionLocal() as s:
        d = _dealer(s, "stale")
        r = _run(s, d, "running", timedelta(hours=5))
        assert reap_abandoned_runs(s) == 1
        s.refresh(r)
        assert r.status == "failed"


def test_one_dealer_only():
    _setup()
    with SessionLocal() as s:
        a, b = _dealer(s, "a"), _dealer(s, "b")
        _run(s, a, "running", timedelta(hours=5))
        rb = _run(s, b, "running", timedelta(hours=5))
        assert reap_abandoned_runs(s, a.id) == 1
        s.refresh(rb)
        assert rb.status == "running"


def main():
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("REAP TESTS PASSED")


if __name__ == "__main__":
    main()
