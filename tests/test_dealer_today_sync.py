"""The dealership list's "Today's sync" filter and its counts.

Buckets (never overlapping): running (in progress now), pending (active, not
synced yet today), done, failed (failed or incomplete). Run against a throwaway DB:

    DATABASE_URL="sqlite:///./today_test.db" API_KEY=test-key python -m pytest tests/test_dealer_today_sync.py
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from dealer_scraper.db import SessionLocal, engine
from dealer_scraper.models import Base, Dealer, ScrapeRun
from dealer_scraper.normalize import start_of_local_day
from . import require_disposable_db


def _setup() -> tuple[TestClient, dict[str, int]]:
    require_disposable_db()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    from api.main import app

    now = datetime.now(timezone.utc)
    yesterday = start_of_local_day() - timedelta(hours=1)
    today = now - timedelta(seconds=1)

    def dealer(s, name, synced, status, active=True):
        d = Dealer(name=name, base_url=f"https://{name}.example", adapter_mode="json_api",
                   scrape_config={}, active=active, last_synced_at=synced, last_sync_status=status)
        s.add(d)
        s.flush()
        return d

    def running(s, d, age):
        s.add(ScrapeRun(dealer_id=d.id, status="running", started_at=now - age))

    with SessionLocal() as s:
        ids = {
            "running": dealer(s, "running", yesterday, "success"),
            "rerun_today": dealer(s, "rerun", today, "success"),      # done earlier, being re-synced now
            "stale": dealer(s, "stale", yesterday, "success"),        # abandoned 'running' row
            "done": dealer(s, "done", today, "success"),
            "done2": dealer(s, "done2", today, "completed"),
            "failed": dealer(s, "failed", today, "failed"),
            "incomplete": dealer(s, "incomplete", today, "incomplete"),
            "never": dealer(s, "never", None, None),
            "waiting": dealer(s, "waiting", yesterday, "success"),
            "off": dealer(s, "off", None, None, active=False),        # inactive: not waiting for anything
        }
        running(s, ids["running"], timedelta(minutes=5))
        running(s, ids["rerun_today"], timedelta(minutes=1))
        running(s, ids["stale"], timedelta(hours=5))
        s.commit()
        ids = {k: v.id for k, v in ids.items()}
    return TestClient(app, headers={"X-API-Key": os.environ.get("API_KEY", "test-key")}), ids


def _names(client: TestClient, bucket: str) -> set[str]:
    r = client.get("/api/dealers", params={"today_sync": bucket})
    assert r.status_code == 200, r.text
    return {d["name"] for d in r.json()}


def test_in_progress_means_a_live_run_and_beats_todays_result() -> None:
    client, _ = _setup()
    assert _names(client, "running") == {"running", "rerun"}


def test_an_abandoned_running_row_does_not_count_as_in_progress() -> None:
    client, _ = _setup()
    assert "stale" not in _names(client, "running")
    assert "stale" in _names(client, "pending")          # it has not really synced today


def test_pending_is_active_dealers_not_yet_synced_today() -> None:
    client, _ = _setup()
    assert _names(client, "pending") == {"stale", "never", "waiting"}


def test_done_and_failed_are_todays_finished_results() -> None:
    client, _ = _setup()
    assert _names(client, "done") == {"done", "done2"}
    assert _names(client, "failed") == {"failed", "incomplete"}


def test_buckets_never_overlap() -> None:
    client, _ = _setup()
    groups = [_names(client, b) for b in ("running", "pending", "done", "failed")]
    assert sum(len(g) for g in groups) == len(set().union(*groups))


def test_counts_equal_what_each_filter_returns() -> None:
    client, _ = _setup()
    counts = client.get("/api/dealers/sync-today").json()
    assert counts == {"running": 2, "pending": 3, "done": 2, "failed": 2}
    for bucket, n in counts.items():
        listed = client.get("/api/dealers", params={"today_sync": bucket})
        assert int(listed.headers["x-total-count"]) == n == len(listed.json())


def test_unknown_bucket_is_rejected_and_route_is_not_taken_by_dealer_id() -> None:
    client, _ = _setup()
    assert client.get("/api/dealers", params={"today_sync": "sometime"}).status_code == 422
    assert client.get("/api/dealers/sync-today").status_code == 200


def test_it_combines_with_the_other_filters() -> None:
    client, _ = _setup()
    r = client.get("/api/dealers", params={"today_sync": "done", "q": "done2"})
    assert [d["name"] for d in r.json()] == ["done2"]


def main() -> None:
    test_in_progress_means_a_live_run_and_beats_todays_result()
    test_an_abandoned_running_row_does_not_count_as_in_progress()
    test_pending_is_active_dealers_not_yet_synced_today()
    test_done_and_failed_are_todays_finished_results()
    test_buckets_never_overlap()
    test_counts_equal_what_each_filter_returns()
    test_unknown_bucket_is_rejected_and_route_is_not_taken_by_dealer_id()
    test_it_combines_with_the_other_filters()
    print("TODAY SYNC FILTER TESTS PASSED — in progress / waiting / done / failed are exact and never overlap")


if __name__ == "__main__":
    main()
