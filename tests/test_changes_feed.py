"""An operator must be able to see WHICH vehicles changed, not just how many.

The dashboard reported "12 removed today" and offered no way to find out which
twelve: the only audit endpoint was per-vehicle, so you had to already know the
car id. Every change was recorded and none of it was reachable.

These tests pin the feed that closes that gap, and the property that makes it
useful: price movement stored as numbers, so "everything that dropped more than
$2,000" is a query rather than a manual scan of note text.

Run:  DATABASE_URL="sqlite:///./.changes_test.db" python -m tests.test_changes_feed
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal

os.environ.setdefault("DATABASE_URL", "sqlite:///./.changes_test.db")
os.environ.setdefault("API_KEY", "test-key")
os.environ.setdefault("SYNC_BACKEND", "local")

from fastapi.testclient import TestClient  # noqa: E402

from api.main import app  # noqa: E402
from dealer_scraper.db import SessionLocal, engine  # noqa: E402
from dealer_scraper.models import Base, Car, Dealer, VehicleAuditLog  # noqa: E402

from . import require_disposable_db  # noqa: E402

KEY = {"X-API-Key": os.environ.get("API_KEY", "test-key")}


def _seed() -> dict:
    """Two dealers, four cars, a spread of changes across time."""
    require_disposable_db()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    now = datetime.now(timezone.utc)

    with SessionLocal() as db:
        d1 = Dealer(name="Clutch", base_url="https://clutch.test", adapter_mode="stub",
                    scrape_config={})
        d2 = Dealer(name="Tabangi", base_url="https://tabangi.test", adapter_mode="stub",
                    scrape_config={})
        db.add_all([d1, d2])
        db.commit()

        cars = []
        for i, (dealer, make, model) in enumerate([
            (d1, "Honda", "Civic"), (d1, "Toyota", "Corolla"),
            (d1, "Ford", "F-150"), (d2, "Mazda", "CX-5"),
        ]):
            c = Car(dealer_id=dealer.id, external_id=f"S{i}", vin=f"VIN{i}",
                    make=make, model=model, year=2020 + i, trim="EX",
                    price=Decimal("20000"), status="active",
                    url=f"https://x.test/{i}")
            db.add(c)
            cars.append(c)
        db.commit()

        def log(car, ev, hours_ago, old=None, new=None, note=None):
            db.add(VehicleAuditLog(
                car_id=car.id, dealer_id=car.dealer_id, event_type=ev,
                note=note, old_value=old, new_value=new,
                occurred_at=now - timedelta(hours=hours_ago)))

        # Inside the default 24h window
        log(cars[0], "price_changed", 2, Decimal("44990"), Decimal("42990"),
            "44990 -> 42990")                                   # -2000 drop
        log(cars[1], "price_changed", 3, Decimal("30000"), Decimal("30500"),
            "30000 -> 30500")                                   # +500 increase
        log(cars[2], "removed", 4)
        log(cars[3], "created", 5)                              # other dealer
        log(cars[0], "updated", 6, note="content hash")         # noise, excluded
        # Outside it
        log(cars[1], "sold", 50)

        db.commit()
        return {"d1": d1.id, "d2": d2.id, "cars": [c.id for c in cars]}


# --- the gap this closes ------------------------------------------------------
def test_removed_vehicles_are_identifiable_not_just_counted() -> None:
    """The whole point: from "1 removed" to "which one"."""
    _seed()
    with TestClient(app) as c:
        r = c.get("/api/changes", params={"event_type": "removed"}, headers=KEY)
        assert r.status_code == 200, r.text
        items = r.json()["items"]
        assert len(items) == 1
        row = items[0]
        assert row["make"] == "Ford" and row["model"] == "F-150"
        assert row["external_id"] == "S2"          # stock number
        assert row["vin"] == "VIN2"
        assert row["url"]                           # link back to the listing
        assert row["dealer_name"] == "Clutch"


def test_price_change_shows_before_after_and_delta() -> None:
    _seed()
    with TestClient(app) as c:
        items = c.get("/api/changes", params={"event_type": "price_changed"},
                      headers=KEY).json()["items"]
    by_make = {i["make"]: i for i in items}
    honda = by_make["Honda"]
    assert Decimal(honda["old_value"]) == Decimal("44990")
    assert Decimal(honda["new_value"]) == Decimal("42990")
    assert Decimal(honda["delta"]) == Decimal("-2000"), "a drop must be negative"
    assert Decimal(by_make["Toyota"]["delta"]) == Decimal("500")


# --- filters ------------------------------------------------------------------
def test_default_window_is_last_24_hours() -> None:
    """A sale 50 hours ago is history, not "what happened last night"."""
    _seed()
    with TestClient(app) as c:
        events = {i["event_type"] for i in c.get("/api/changes", headers=KEY).json()["items"]}
    assert "sold" not in events
    assert {"price_changed", "removed", "created"} <= events


def test_wider_window_includes_older_events() -> None:
    _seed()
    with TestClient(app) as c:
        events = {i["event_type"] for i in
                  c.get("/api/changes", params={"hours": 72}, headers=KEY).json()["items"]}
    assert "sold" in events


def test_noise_events_are_excluded_by_default() -> None:
    """`updated` fires when a content hash moves — a description tweak is not
    something anyone is watching for, and would bury the real changes."""
    _seed()
    with TestClient(app) as c:
        events = [i["event_type"] for i in c.get("/api/changes", headers=KEY).json()["items"]]
    assert "updated" not in events


def test_filter_by_dealer() -> None:
    ids = _seed()
    with TestClient(app) as c:
        items = c.get("/api/changes", params={"dealer_id": ids["d2"]},
                      headers=KEY).json()["items"]
    assert items and all(i["dealer_id"] == ids["d2"] for i in items)


def test_min_abs_delta_finds_big_movers() -> None:
    """The competitive-pricing question, only answerable because the movement
    is numeric: a text note cannot be compared to 2000."""
    _seed()
    with TestClient(app) as c:
        items = c.get("/api/changes", params={"min_abs_delta": 1000},
                      headers=KEY).json()["items"]
    assert len(items) == 1
    assert items[0]["make"] == "Honda"          # -2000 kept, +500 filtered out


def test_newest_first() -> None:
    _seed()
    with TestClient(app) as c:
        times = [i["occurred_at"] for i in c.get("/api/changes", headers=KEY).json()["items"]]
    assert times == sorted(times, reverse=True)


def test_pagination_reports_the_full_total() -> None:
    """`total` must count the whole window, not the page — otherwise the UI
    cannot tell the operator how much they are not looking at."""
    _seed()
    with TestClient(app) as c:
        body = c.get("/api/changes", params={"page_size": 2}, headers=KEY).json()
    assert len(body["items"]) == 2
    assert body["total"] >= 4
    assert body["page"] == 1 and body["page_size"] == 2


# --- summary ------------------------------------------------------------------
def test_summary_counts_match_the_list() -> None:
    _seed()
    with TestClient(app) as c:
        s = c.get("/api/changes/summary", headers=KEY).json()
    assert s["removed"] == 1
    assert s["added"] == 1
    assert s["price_changed"] == 2
    assert s["sold"] == 0            # outside the 24h window


def test_summary_splits_drops_from_increases() -> None:
    """"price_changed: 2" hides that one lot got cheaper and another dearer —
    opposite signals for someone tracking a competitor."""
    _seed()
    with TestClient(app) as c:
        s = c.get("/api/changes/summary", headers=KEY).json()
    assert s["price_drops"] == 1
    assert s["price_increases"] == 1


def test_changes_require_auth() -> None:
    _seed()
    with TestClient(app) as c:
        assert c.get("/api/changes").status_code == 401
        assert c.get("/api/changes/summary").status_code == 401


def main() -> None:
    test_removed_vehicles_are_identifiable_not_just_counted()
    test_price_change_shows_before_after_and_delta()
    test_default_window_is_last_24_hours()
    test_wider_window_includes_older_events()
    test_noise_events_are_excluded_by_default()
    test_filter_by_dealer()
    test_min_abs_delta_finds_big_movers()
    test_newest_first()
    test_pagination_reports_the_full_total()
    test_summary_counts_match_the_list()
    test_summary_splits_drops_from_increases()
    test_changes_require_auth()
    print("CHANGES FEED TESTS PASSED — every counted change is traceable to a vehicle")


if __name__ == "__main__":
    main()
