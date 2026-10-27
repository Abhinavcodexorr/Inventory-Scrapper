"""Tests for the manual-sync integration: identity resolution, change events,
the empty-scrape safety gate, the dealer 'last sync' block, and the dispatcher.

Complements smoke_test.py (which covers the day-over-day lifecycle).

Run:  DATABASE_URL="sqlite:///./sync_test.db" python -m tests.test_sync_integration
"""
from __future__ import annotations

from sqlalchemy import select, func

from dealer_scraper.db import SessionLocal, engine
from dealer_scraper.models import Base, Car, Dealer, VehicleAuditLog
from dealer_scraper.adapters.stub import StubAdapter
from dealer_scraper.adapters.registry import build_adapter
from dealer_scraper.engine import run_dealer
from . import require_disposable_db


def car(stock, **extra):
    base = {"external_id": stock, "make": "Honda", "model": "Civic", "year": 2020,
            "trim": "EX", "mileage": 10000, "condition": "Used", "price": "20000",
            "url": f"https://x/{stock}"}
    base.update(extra)
    return base


def _dealer(name: str) -> int:
    with SessionLocal() as s:
        d = Dealer(name=name, base_url=f"https://{name}.example",
                   adapter_mode="stub", scrape_config={})
        s.add(d); s.commit()
        return d.id


def _run(dealer_id, items):
    with SessionLocal() as s:
        d = s.get(Dealer, dealer_id)
        return run_dealer(s, d, adapter=StubAdapter(items))


def _cars(dealer_id) -> list[Car]:
    with SessionLocal() as s:
        return list(s.execute(
            select(Car).where(Car.dealer_id == dealer_id).order_by(Car.id)
        ).scalars())


def _events(dealer_id, event_type) -> list[VehicleAuditLog]:
    with SessionLocal() as s:
        return list(s.execute(
            select(VehicleAuditLog).where(
                VehicleAuditLog.dealer_id == dealer_id,
                VehicleAuditLog.event_type == event_type)
        ).scalars())


def test_registry_knows_stub():
    a = build_adapter("stub", {"items": [car("A1")]})
    assert a.fetch_total_count() == 1
    assert [v.external_id for v in a.iter_vehicles()] == ["A1"]
    print("  registry builds stub from scrape_config ✔")


def test_vin_wins_over_external_id():
    """Provider re-stocks the same physical car under a new stock number."""
    did = _dealer("vin")
    _run(did, [car("STOCK-1", vin="1HGCM82633A004352")])
    # Same VIN, brand-new external_id and URL.
    r = _run(did, [car("STOCK-2", vin="1HGCM82633A004352", url="https://x/STOCK-2")])

    cars = _cars(did)
    assert len(cars) == 1, f"VIN dedup failed — {len(cars)} rows"
    assert cars[0].external_id == "STOCK-2", cars[0].external_id
    assert cars[0].status == "active", cars[0].status
    assert r.new_count == 0 and r.removed_count == 0, (r.new_count, r.removed_count)
    print("  VIN match beats external_id, no duplicate, stock # adopted ✔")


def test_url_matches_when_no_vin():
    did = _dealer("url")
    _run(did, [car("OLD-1", url="https://x/listing/99")])
    r = _run(did, [car("NEW-1", url="https://x/listing/99")])

    cars = _cars(did)
    assert len(cars) == 1, f"URL dedup failed — {len(cars)} rows"
    assert cars[0].external_id == "NEW-1", cars[0].external_id
    assert r.new_count == 0, r.new_count
    print("  listing URL matches when VIN is absent ✔")


def test_duplicate_listing_in_one_scrape():
    """Paging overlap must not create two rows or inflate counters."""
    did = _dealer("dupe")
    r = _run(did, [car("D1", vin="VINDUP"), car("D1", vin="VINDUP")])
    assert len(_cars(did)) == 1, "same listing twice created duplicates"
    assert r.new_count == 1, r.new_count
    print("  repeated listing inside one scrape is deduped ✔")


def test_field_change_events():
    did = _dealer("changes")
    _run(did, [car("C1", mileage=10000, price="20000", condition="Used")])
    _run(did, [car("C1", mileage=12500, price="18000", condition="New")])

    assert len(_events(did, "price_changed")) == 1, "missing price_changed"
    assert len(_events(did, "mileage_changed")) == 1, "missing mileage_changed"
    assert len(_events(did, "status_changed")) == 1, "missing status_changed"
    note = _events(did, "mileage_changed")[0].note
    assert note == "10000 -> 12500", note
    print("  price / mileage / status changes each recorded ✔")


def test_empty_scrape_does_not_wipe_inventory():
    did = _dealer("empty")
    _run(did, [car("E1"), car("E2")])
    r = _run(did, [])          # broken selector / blocked request

    assert r.status == "incomplete", r.status
    assert r.removed_count == 0, r.removed_count
    assert all(c.status == "active" for c in _cars(did)), "inventory was wiped"
    print("  empty scrape is 'incomplete', never removes stock ✔")


def test_dealer_last_sync_block():
    did = _dealer("lastsync")
    run = _run(did, [car("L1"), car("L2")])
    with SessionLocal() as s:
        d = s.get(Dealer, did)
        assert d.last_sync_status == "success", d.last_sync_status
        assert d.last_synced_at is not None
        assert d.last_sync_duration_seconds is not None
        assert d.last_sync_duration_seconds >= 0
        assert d.inventory_count == 2, d.inventory_count
    assert run.finished_at is not None
    print("  dealer.last_sync_status / duration / inventory_count updated ✔")


def test_failed_scrape_records_status():
    did = _dealer("boom")

    class Boom:
        def fetch_total_count(self): return 5
        def iter_vehicles(self): raise RuntimeError("site blocked us")

    with SessionLocal() as s:
        d = s.get(Dealer, did)
        r = run_dealer(s, d, adapter=Boom())
    assert r.status == "failed", r.status
    assert "site blocked us" in (r.error or ""), r.error
    with SessionLocal() as s:
        assert s.get(Dealer, did).last_sync_status == "failed"
    print("  failed scrape recorded on run + dealer ✔")


def test_dispatcher_runs_without_celery():
    """Requirement: with no broker, the scrape still runs in the background."""
    import time
    from worker import dispatch

    did = _dealer("dispatch")
    with SessionLocal() as s:
        d = s.get(Dealer, did)
        d.adapter_mode = "stub"
        d.scrape_config = {"items": [car("Z1"), car("Z2"), car("Z3")]}
        s.commit()
        base_url = d.base_url

    # Force the no-broker path regardless of whether Redis happens to be up.
    original = dispatch.broker_available
    dispatch.broker_available = lambda *a, **k: False
    try:
        out = dispatch.dispatch_sync(did, base_url)
        assert out["mode"] == "local", out
        assert out["status"] == "queued", out
        assert out["job_id"].startswith("local-"), out

        for _ in range(100):                     # wait for the worker thread
            job = dispatch.local_job(out["job_id"])
            if job and job.finished_at:
                break
            time.sleep(0.1)
        else:
            raise AssertionError("local job never finished")

        assert job.status == "SUCCESS", (job.status, job.result)
        assert job.result["status"] == "completed", job.result
        assert job.result["scraped_count"] == 3, job.result
    finally:
        dispatch.broker_available = original

    with SessionLocal() as s:
        n = s.execute(select(func.count()).select_from(Car)
                      .where(Car.dealer_id == did)).scalar_one()
    assert n == 3, n
    print("  dispatcher scrapes in-process when Celery is unavailable ✔")


def test_dispatcher_prefers_celery_when_broker_is_up():
    """With Redis reachable, the job must go to the queue — not the local pool."""
    from worker import dispatch
    import worker.tasks as wt

    did = _dealer("celery")
    sent: list[int] = []

    class FakeAsyncResult:
        id = "celery-task-abc123"

    def fake_delay(dealer_id):
        sent.append(dealer_id)
        return FakeAsyncResult()

    orig_probe, orig_delay = dispatch.broker_available, wt.scrape_dealer.delay
    orig_workers, orig_mode = dispatch.workers_available, dispatch.settings.sync_backend
    dispatch.broker_available = lambda *a, **k: True
    dispatch.workers_available = lambda *a, **k: True   # a worker IS consuming
    dispatch.settings.sync_backend = "auto"             # independent of .env
    wt.scrape_dealer.delay = fake_delay
    try:
        out = dispatch.dispatch_sync(did, "https://celery.example")
    finally:
        dispatch.broker_available = orig_probe
        dispatch.workers_available = orig_workers
        dispatch.settings.sync_backend = orig_mode
        wt.scrape_dealer.delay = orig_delay

    assert out == {"status": "queued", "job_id": "celery-task-abc123", "mode": "celery"}, out
    assert sent == [did], sent
    assert dispatch.local_job_for_dealer(did) is None, "should not have run locally"
    print("  broker up -> enqueued on Celery ✔")


def test_dispatcher_falls_back_when_publish_fails():
    """Broker dies between the probe and the publish -> still scrapes, never 5xx."""
    import time
    from kombu.exceptions import OperationalError
    from worker import dispatch
    import worker.tasks as wt

    did = _dealer("celeryfail")
    with SessionLocal() as s:
        d = s.get(Dealer, did)
        d.scrape_config = {"items": [car("F1"), car("F2")]}
        s.commit()

    def boom(dealer_id):
        raise OperationalError("broker went away")

    orig_probe, orig_delay = dispatch.broker_available, wt.scrape_dealer.delay
    orig_workers, orig_mode = dispatch.workers_available, dispatch.settings.sync_backend
    dispatch.broker_available = lambda *a, **k: True
    dispatch.workers_available = lambda *a, **k: True
    dispatch.settings.sync_backend = "auto"
    wt.scrape_dealer.delay = boom
    try:
        out = dispatch.dispatch_sync(did, "https://celeryfail.example")
        assert out["mode"] == "local", out
        assert out["status"] == "queued", out
        for _ in range(100):
            job = dispatch.local_job(out["job_id"])
            if job and job.finished_at:
                break
            time.sleep(0.1)
        else:
            raise AssertionError("fallback job never finished")
    finally:
        dispatch.broker_available = orig_probe
        dispatch.workers_available = orig_workers
        dispatch.settings.sync_backend = orig_mode
        wt.scrape_dealer.delay = orig_delay

    assert job.result["status"] == "completed", job.result
    with SessionLocal() as s:
        n = s.execute(select(func.count()).select_from(Car)
                      .where(Car.dealer_id == did)).scalar_one()
    assert n == 2, n
    print("  publish failure -> transparent local fallback, no error to caller ✔")


def test_backend_selection():
    """SYNC_BACKEND decides who runs a sync — and 'auto' must never pick Celery
    when nothing is consuming, which is the one silently-broken combination."""
    from worker import dispatch

    orig = (dispatch.broker_available, dispatch.workers_available,
            dispatch.settings.sync_backend)
    try:
        for mode, broker, workers, expected in [
            # SYNC_BACKEND=local never touches Celery, even with both up.
            ("local",  True,  True,  "local"),
            ("local",  False, False, "local"),
            # SYNC_BACKEND=celery is explicit: no worker is an error, not a fallback.
            ("celery", True,  True,  "celery"),
            ("celery", True,  False, "no_worker"),
            # auto: a reachable broker is NOT sufficient — a worker must answer.
            ("auto",   True,  True,  "celery"),
            ("auto",   True,  False, "local"),
            ("auto",   False, False, "local"),
            # An unrecognised value must degrade to auto, never to "no backend".
            ("nonsense", True, False, "local"),
        ]:
            dispatch.settings.sync_backend = mode
            dispatch.broker_available = lambda *a, **k: broker
            dispatch.workers_available = lambda *a, **k: workers
            got = dispatch.choose_backend()
            assert got == expected, f"{mode} broker={broker} workers={workers} -> {got}"
    finally:
        (dispatch.broker_available, dispatch.workers_available,
         dispatch.settings.sync_backend) = orig
    print("  SYNC_BACKEND local/celery/auto each route correctly ✔")


def main() -> None:
    require_disposable_db()   # drop_all() below — never let this hit a real DB
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    for fn in (
        test_registry_knows_stub,
        test_vin_wins_over_external_id,
        test_url_matches_when_no_vin,
        test_duplicate_listing_in_one_scrape,
        test_field_change_events,
        test_empty_scrape_does_not_wipe_inventory,
        test_dealer_last_sync_block,
        test_failed_scrape_records_status,
        test_backend_selection,
        test_dispatcher_runs_without_celery,
        test_dispatcher_prefers_celery_when_broker_is_up,
        test_dispatcher_falls_back_when_publish_fails,
    ):
        fn()
    print("SYNC INTEGRATION TESTS PASSED ✔")


if __name__ == "__main__":
    main()
