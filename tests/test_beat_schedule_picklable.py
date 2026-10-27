"""Beat's schedule must survive being pickled, or the daily sync never runs.

Celery's PersistentScheduler shelves every schedule entry to disk on each sync.
An entry holding a local object — the obvious one being a `nowfun` lambda that
closes over a ZoneInfo — raises

    AttributeError: Can't pickle local object
        '_daily_sync_schedule.<locals>.<lambda>'

on beat's first sync. beat exits 1, systemd restarts it, and it exits again:
the service sits in `activating (auto-restart)` forever and the nightly sync
silently never fires. Nothing else breaks, so the failure is invisible unless
someone checks `systemctl is-active` — it went unnoticed until a deployed
server was asked to prove the schedule worked.

The timezone therefore comes from the app's `timezone` setting, which Celery
applies to crontab entries itself (and which tracks DST).

Run:  python -m tests.test_beat_schedule_picklable
"""
from __future__ import annotations

import pickle

from worker.celery_app import _daily_sync_schedule, celery_app, schedule_timezone


def test_schedule_is_picklable() -> None:
    """The exact operation beat performs on every sync."""
    schedule = _daily_sync_schedule()
    assert schedule, "daily sync should be configured by default"
    pickle.loads(pickle.dumps(schedule))  # must not raise


def test_each_entry_is_picklable_individually() -> None:
    """Beat pickles entries one at a time, so a single bad entry is enough."""
    for name, entry in _daily_sync_schedule().items():
        try:
            pickle.loads(pickle.dumps(entry))
        except Exception as exc:  # pragma: no cover - failure path
            raise AssertionError(f"schedule entry {name!r} is not picklable: {exc}") from exc


def test_no_nowfun_on_the_crontab() -> None:
    """`nowfun` is the specific footgun: any callable there is a local object
    unless it is a module-level function, and a lambda always is."""
    for name, entry in _daily_sync_schedule().items():
        nowfun = getattr(entry["schedule"], "nowfun", None)
        assert nowfun is None, (
            f"{name} sets crontab nowfun={nowfun!r}; use the app timezone instead "
            "so the entry stays picklable")


def test_app_timezone_drives_the_local_hour() -> None:
    """With nowfun gone, the app's timezone is what makes DAILY_SYNC_HOUR mean
    the client's local hour rather than UTC."""
    assert celery_app.conf.timezone == schedule_timezone()


def test_unusable_timezone_falls_back_to_utc() -> None:
    """A server without tzdata must still boot and still run the sync — at the
    wrong local hour, which is recoverable, rather than not at all."""
    import unittest.mock as mock

    from dealer_scraper.config import settings

    with mock.patch.object(settings, "daily_sync_timezone", "Not/AZone"):
        assert schedule_timezone() == "UTC"
    with mock.patch.object(settings, "daily_sync_timezone", "America/Toronto"):
        assert schedule_timezone() in ("America/Toronto", "UTC")


def test_disabled_schedule_is_empty() -> None:
    import unittest.mock as mock

    from dealer_scraper.config import settings

    with mock.patch.object(settings, "daily_sync_enabled", False):
        assert _daily_sync_schedule() == {}


def test_missed_window_expires_rather_than_piling_up() -> None:
    for entry in _daily_sync_schedule().values():
        assert entry["options"]["expires"] == 6 * 60 * 60


def main() -> None:
    test_schedule_is_picklable()
    test_each_entry_is_picklable_individually()
    test_no_nowfun_on_the_crontab()
    test_app_timezone_drives_the_local_hour()
    test_unusable_timezone_falls_back_to_utc()
    test_disabled_schedule_is_empty()
    test_missed_window_expires_rather_than_piling_up()
    print("BEAT SCHEDULE TESTS PASSED — schedule pickles, so beat stays up and the daily sync fires")


if __name__ == "__main__":
    main()
