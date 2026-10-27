"""SOW §3 — the daily synchronisation schedule.

Covers the two things that make "runs automatically every day" true:
  * Celery Beat has an entry firing sync_all_dealers at the configured time
  * sync_all_dealers fans out to every ACTIVE dealer (and only those), staggered

Run:  DATABASE_URL="sqlite:////tmp/sched.db" python -m pytest tests/test_daily_schedule.py -q
"""
from __future__ import annotations

from unittest.mock import patch

from dealer_scraper.config import settings
from dealer_scraper.db import SessionLocal, engine
from dealer_scraper.models import Base, Dealer
from worker.celery_app import _daily_sync_schedule
from worker.tasks import sync_all_dealers

from . import require_disposable_db


def _fresh_dealers() -> tuple[int, int, int]:
    require_disposable_db()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with SessionLocal() as s:
        a = Dealer(name="Active A", base_url="https://a.example",
                   adapter_mode="stub", scrape_config={}, active=True)
        b = Dealer(name="Active B", base_url="https://b.example",
                   adapter_mode="stub", scrape_config={}, active=True)
        c = Dealer(name="Inactive", base_url="https://c.example",
                   adapter_mode="stub", scrape_config={}, active=False)
        s.add_all([a, b, c]); s.commit()
        return a.id, b.id, c.id


def test_beat_has_a_daily_entry_at_the_configured_time():
    schedule = _daily_sync_schedule()
    assert "daily-inventory-sync" in schedule
    entry = schedule["daily-inventory-sync"]
    assert entry["task"] == "worker.tasks.sync_all_dealers"
    cron = entry["schedule"]
    assert settings.daily_sync_hour in cron.hour
    assert settings.daily_sync_minute in cron.minute
    # A worker that was down must not fire a backlog of catch-up runs.
    assert entry["options"]["expires"] > 0


def test_disabling_the_schedule_removes_the_entry():
    original = settings.daily_sync_enabled
    try:
        settings.daily_sync_enabled = False
        assert _daily_sync_schedule() == {}
    finally:
        settings.daily_sync_enabled = original


def test_fan_out_enqueues_only_active_dealers():
    active_a, active_b, inactive = _fresh_dealers()
    with patch("worker.tasks.scrape_dealer.apply_async") as enqueue:
        result = sync_all_dealers.run()

    assert result["dispatched"] == 2, result
    dispatched = [call.kwargs["args"][0] for call in enqueue.call_args_list]
    assert sorted(dispatched) == sorted([active_a, active_b])
    assert inactive not in dispatched, "inactive dealers must never be scraped"


def test_fan_out_staggers_so_a_fleet_does_not_stampede():
    _fresh_dealers()
    original = settings.daily_sync_stagger_seconds
    try:
        settings.daily_sync_stagger_seconds = 30
        with patch("worker.tasks.scrape_dealer.apply_async") as enqueue:
            sync_all_dealers.run()
        countdowns = sorted(c.kwargs["countdown"] for c in enqueue.call_args_list)
        # first goes immediately, each subsequent one is spaced out
        assert countdowns == [0, 30], countdowns
    finally:
        settings.daily_sync_stagger_seconds = original


def test_explicit_dealer_ids_narrow_the_fan_out():
    active_a, _active_b, _inactive = _fresh_dealers()
    with patch("worker.tasks.scrape_dealer.apply_async") as enqueue:
        result = sync_all_dealers.run(dealer_ids=[active_a])
    assert result["dispatched"] == 1
    assert enqueue.call_args_list[0].kwargs["args"] == [active_a]


def test_no_active_dealers_is_not_an_error():
    require_disposable_db()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with patch("worker.tasks.scrape_dealer.apply_async") as enqueue:
        result = sync_all_dealers.run()
    assert result["dispatched"] == 0
    enqueue.assert_not_called()


def main() -> None:
    test_beat_has_a_daily_entry_at_the_configured_time()
    test_disabling_the_schedule_removes_the_entry()
    test_fan_out_enqueues_only_active_dealers()
    test_fan_out_staggers_so_a_fleet_does_not_stampede()
    test_explicit_dealer_ids_narrow_the_fan_out()
    test_no_active_dealers_is_not_an_error()
    print("DAILY SCHEDULE TESTS PASSED — beat entry + active-only staggered fan-out")


if __name__ == "__main__":
    main()
