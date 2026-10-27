"""A run must not report success for data that cannot be right.

Each case is a run that used to read "completed":

* CCC Motors re-read one 199-vehicle payload until the 90-minute task limit —
  1,072,212 rows, the worker held the whole time.
* B Town scraped 120 rows and stored 12: the same page, ten times over.
* Coliseum stored 124 vehicles as make "True", year 1, VIN "TRUE" — every one
  matched the same VIN key, so 124 collapsed into a handful of rows.

Run:  DATABASE_URL="sqlite:///./sync_test.db" python -m tests.test_run_invariants
"""
from __future__ import annotations

from sqlalchemy import select

from dealer_scraper.adapters.stub import StubAdapter
from dealer_scraper.config import settings
from dealer_scraper.db import SessionLocal, engine
from dealer_scraper.engine import _vin_key, run_dealer
from dealer_scraper.models import Base, Car, Dealer
from dealer_scraper.quality import is_described
from dealer_scraper.normalize import normalize
from . import require_disposable_db


def _car(i: int, **extra) -> dict:
    base = {"external_id": f"S{i}", "make": "Honda", "model": "Civic", "year": 2020,
            "price": "20000", "url": f"https://x/{i}"}
    base.update(extra)
    return base


def _dealer(name: str) -> int:
    with SessionLocal() as s:
        d = Dealer(name=name, base_url=f"https://{name}.example", adapter_mode="stub", scrape_config={})
        s.add(d)
        s.commit()
        return d.id


class _HonestTotal(StubAdapter):
    """Reports the site's real total, as a live feed does, instead of the
    length of whatever list the loop will produce."""

    def __init__(self, items: list[dict], total: int) -> None:
        super().__init__(items)
        self._total = total

    def fetch_total_count(self) -> int:
        return self._total


def _run(dealer_id: int, items: list[dict], site_total: int | None = None):
    with SessionLocal() as s:
        adapter = StubAdapter(items) if site_total is None else _HonestTotal(items, site_total)
        return run_dealer(s, s.get(Dealer, dealer_id), adapter=adapter)


def _active(dealer_id: int) -> int:
    with SessionLocal() as s:
        return len(list(s.execute(
            select(Car).where(Car.dealer_id == dealer_id, Car.status == "active")).scalars()))


def test_a_run_that_rereads_the_same_pages_is_stopped_and_incomplete() -> None:
    """120 rows made of the same 12 vehicles is not a successful scrape."""
    did = _dealer("repeats")
    _run(did, [_car(i) for i in range(12)])
    page = [_car(i) for i in range(12)]
    run = _run(did, page * 10)
    assert run.status == "incomplete", run.status
    assert "same pages were read repeatedly" in run.error, run.error
    assert _active(did) == 12


def test_a_repeat_run_does_not_remove_vehicles_it_never_re_read() -> None:
    """The first run held 30; a run that only ever saw the first 12 must not
    mark the other 18 removed."""
    did = _dealer("noremove")
    _run(did, [_car(i) for i in range(30)])
    run = _run(did, [_car(i) for i in range(12)] * 10)
    assert run.status == "incomplete"
    assert _active(did) == 30, "removals must be skipped on an incomplete run"


def test_a_runaway_scrape_is_aborted_at_the_cap_not_at_the_task_limit() -> None:
    old = (settings.runaway_floor, settings.runaway_multiple)
    settings.runaway_floor, settings.runaway_multiple = 100, 3.0
    try:
        did = _dealer("runaway")
        _run(did, [_car(i) for i in range(10)])
        run = _run(did, [_car(i % 10) for i in range(5000)], site_total=10)
        assert run.status == "failed", run.status
        assert "Runaway scrape stopped" in run.error, run.error
        assert run.scraped_count < 200, f"breaker let {run.scraped_count} rows through"
    finally:
        settings.runaway_floor, settings.runaway_multiple = old


def test_a_large_first_sync_below_the_floor_is_not_tripped() -> None:
    did = _dealer("bigfirst")
    run = _run(did, [_car(i) for i in range(300)])
    assert run.status == "completed", (run.status, run.error)
    assert _active(did) == 300


def test_junk_values_do_not_count_as_a_described_vehicle() -> None:
    v = normalize({"external_id": "W26-319", "make": "True", "model": "True", "year": 1, "vin": "TRUE"})
    assert not is_described(v)
    assert is_described(normalize({"external_id": "A", "make": "Honda", "year": 2020}))


def test_a_vin_that_is_not_a_vin_never_identifies_a_vehicle() -> None:
    assert _vin_key("TRUE") is None
    assert _vin_key("N/A") is None
    assert _vin_key(" 1hgcm82633a004352 ") == "1HGCM82633A004352"


def test_a_mismapped_feed_is_reported_incomplete_not_stored_as_inventory() -> None:
    """Coliseum: 124 rows of make/model "True", year 1."""
    did = _dealer("mismapped")
    junk = [{"external_id": f"W26-{i}", "make": "True", "model": "True", "year": 1, "vin": "TRUE"}
            for i in range(124)]
    run = _run(did, junk)
    assert run.status == "incomplete", run.status
    assert "had any details" in run.error, run.error


def main() -> None:
    require_disposable_db()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    test_a_run_that_rereads_the_same_pages_is_stopped_and_incomplete()
    test_a_repeat_run_does_not_remove_vehicles_it_never_re_read()
    test_a_runaway_scrape_is_aborted_at_the_cap_not_at_the_task_limit()
    test_a_large_first_sync_below_the_floor_is_not_tripped()
    test_junk_values_do_not_count_as_a_described_vehicle()
    test_a_vin_that_is_not_a_vin_never_identifies_a_vehicle()
    test_a_mismapped_feed_is_reported_incomplete_not_stored_as_inventory()
    print("RUN INVARIANT TESTS PASSED — a run that read nothing real no longer says completed")


if __name__ == "__main__":
    main()
