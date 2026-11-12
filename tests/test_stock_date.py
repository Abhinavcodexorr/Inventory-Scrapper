"""Days in Stock: reading the dealer's stock date, storing it, and serving it.

Run against a throwaway database:

    DATABASE_URL="sqlite:///./stock_test.db" API_KEY=test-key python -m pytest tests/test_stock_date.py
"""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone

from fastapi.testclient import TestClient

from dealer_scraper.db import SessionLocal, engine
from dealer_scraper.models import Base, Car, Dealer
from dealer_scraper.normalize import days_in_stock, normalize, parse_date, stock_date_from
from . import require_disposable_db

TODAY = date(2026, 11, 12)


# ------------------------------------------------------------ reading the date
def test_zop_gives_a_direct_stock_date() -> None:
    raw = {"_raw": {"stock_date": "2026-11-03", "days_in_stock": "8"}}
    assert stock_date_from(raw, TODAY) == date(2026, 11, 3)


def test_convertus_day_count_becomes_a_date() -> None:
    assert stock_date_from({"_raw": {"days_on_lot": 49}}, TODAY) == date(2026, 9, 24)


def test_hillz_uses_created_at_because_instock_date_is_empty() -> None:
    item = {"frk_dealership_id": 7, "instock_date": None, "age": 18, "createdAt": "2026-10-24T18:40:59.000Z"}
    assert stock_date_from({"_raw": item}, TODAY) == date(2026, 10, 24)


def test_car_pages_uses_the_listing_creation_time() -> None:
    item = {"organization": {"id": 1}, "create_datetime": "2026-11-01 10:39:45"}
    assert stock_date_from({"_raw": item}, TODAY) == date(2026, 11, 1)


def test_a_created_at_alone_is_not_a_stock_date() -> None:
    """Only recognised providers: an unknown feed's createdAt could mean anything."""
    assert stock_date_from({"_raw": {"createdAt": "2026-10-24T18:40:59Z"}}, TODAY) is None


def test_a_mapped_field_wins_over_the_raw_item() -> None:
    raw = {"stock_date": "2026-11-05", "_raw": {"stock_date": "2026-01-01"}}
    assert stock_date_from(raw, TODAY) == date(2026, 11, 5)


def test_a_site_that_says_nothing_gives_none() -> None:
    assert stock_date_from({"_raw": {"href": "/car/1", "text": "2019 Toyota"}}, TODAY) is None
    assert stock_date_from({}, TODAY) is None


def test_impossible_values_are_rejected_not_stored() -> None:
    for bad in ({"stock_date": "1970-01-01"}, {"stock_date": "2031-01-01"}, {"stock_date": "soon"},
                {"days_in_stock": -3}, {"days_in_stock": "N/A"}, {"days_on_lot": 99999}):
        assert stock_date_from({"_raw": bad}, TODAY) is None, bad


def test_normalize_carries_it_and_it_is_not_a_listing_change() -> None:
    a = normalize({"external_id": "1", "price": "100", "_raw": {"stock_date": "2026-11-03"}})
    b = normalize({"external_id": "1", "price": "100", "_raw": {"stock_date": "2026-10-01"}})
    assert a.stock_date == date(2026, 11, 3)
    assert a.content_hash() == b.content_hash()   # must not bump "Last updated"


def test_parse_date_accepts_the_shapes_feeds_use() -> None:
    assert parse_date("2026-11-03") == date(2026, 11, 3)
    assert parse_date("2026-11-03 09:20:11") == date(2026, 11, 3)
    assert parse_date("2026-10-24T18:40:59.000Z") == date(2026, 10, 24)
    assert parse_date(datetime(2026, 1, 2, 3, 4)) == date(2026, 1, 2)
    assert parse_date("") is None and parse_date(None) is None and parse_date("x") is None


# ------------------------------------------------------------ counting the days
def test_days_count_to_today_while_for_sale() -> None:
    assert days_in_stock(date(2026, 11, 2), "active", today=TODAY) == 10
    assert days_in_stock(None, "active", today=TODAY) is None
    assert days_in_stock(date(2026, 11, 12), "active", today=TODAY) == 0


def test_days_stop_at_the_sold_or_removed_date() -> None:
    sold = datetime(2026, 11, 7, 15, 0, tzinfo=timezone.utc)
    assert days_in_stock(date(2026, 11, 2), "sold", sold_at=sold, today=TODAY) == 5
    assert days_in_stock(date(2026, 11, 2), "removed", removed_at=sold, today=TODAY) == 5
    # A sold car with no recorded sold date falls back to today rather than hiding it.
    assert days_in_stock(date(2026, 11, 2), "sold", today=TODAY) == 10


# ------------------------------------------------------------ stored and served
def _client() -> tuple[TestClient, int]:
    require_disposable_db()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    from api.main import app

    with SessionLocal() as s:
        d = Dealer(name="D", base_url="https://d.example", adapter_mode="json_api", scrape_config={}, active=True)
        s.add(d)
        s.commit()
        did = d.id
    return TestClient(app, headers={"X-API-Key": os.environ.get("API_KEY", "test-key")}), did


def _car(n: int, stock: str | None) -> dict:
    return {"external_id": f"S{n}", "vin": f"1HGCM82633A{n:06d}", "make": "Honda", "model": "Civic",
            "year": 2020, "price": "10000.00", "url": f"https://d.example/v/{n}", "stock_date": stock}


def _push(client: TestClient, did: int, cars: list[dict]) -> None:
    r = client.post(f"/api/dealers/{did}/ingest", json={"vehicles": cars, "reported_total": len(cars)})
    assert r.status_code == 200, r.text


def test_the_stock_date_is_saved_and_served_with_days_in_stock() -> None:
    client, did = _client()
    ten_days_ago = (date.today() - timedelta(days=10)).isoformat()
    _push(client, did, [_car(1, ten_days_ago), _car(2, None)])
    items = {c["external_id"]: c for c in client.get("/api/vehicles", params={"dealer_id": did}).json()["items"]}
    assert items["S1"]["stock_date"] == ten_days_ago
    assert 9 <= items["S1"]["days_in_stock"] <= 11          # allow for the Toronto/UTC date edge
    assert items["S2"]["stock_date"] is None and items["S2"]["days_in_stock"] is None


def test_a_later_sync_fills_in_a_missing_date_and_ignores_one_day_wobble() -> None:
    client, did = _client()
    _push(client, did, [_car(1, None)])
    _push(client, did, [_car(1, "2026-11-03")])
    with SessionLocal() as s:
        assert s.query(Car).one().stock_date == date(2026, 11, 3)
    _push(client, did, [_car(1, "2026-11-04")])                   # derived-date jitter: ignored
    with SessionLocal() as s:
        assert s.query(Car).one().stock_date == date(2026, 11, 3)
    _push(client, did, [_car(1, "2026-10-20")])                   # a real correction: taken
    with SessionLocal() as s:
        assert s.query(Car).one().stock_date == date(2026, 10, 20)


def test_a_listing_that_stops_reporting_the_date_keeps_it() -> None:
    client, did = _client()
    _push(client, did, [_car(1, "2026-11-03")])
    _push(client, did, [_car(1, None)])
    with SessionLocal() as s:
        assert s.query(Car).one().stock_date == date(2026, 11, 3)


def test_a_date_change_alone_does_not_touch_last_updated() -> None:
    client, did = _client()
    _push(client, did, [_car(1, "2026-11-03")])
    with SessionLocal() as s:
        before = s.query(Car).one().updated_at
    _push(client, did, [_car(1, "2026-10-01")])
    with SessionLocal() as s:
        assert s.query(Car).one().updated_at == before


def test_sort_by_days_in_stock_puts_unknown_last_both_ways() -> None:
    client, did = _client()
    today = date.today()
    _push(client, did, [_car(1, (today - timedelta(days=5)).isoformat()),
                        _car(2, (today - timedelta(days=50)).isoformat()),
                        _car(3, None),
                        _car(4, (today - timedelta(days=20)).isoformat())])
    for order, expected in (("desc", ["S2", "S4", "S1", "S3"]), ("asc", ["S1", "S4", "S2", "S3"])):
        body = client.get("/api/vehicles", params={"dealer_id": did, "sort": "days_in_stock", "order": order}).json()
        assert [c["external_id"] for c in body["items"]] == expected, order
        assert body["meta"]["total"] == 4


def main() -> None:
    test_zop_gives_a_direct_stock_date()
    test_convertus_day_count_becomes_a_date()
    test_hillz_uses_created_at_because_instock_date_is_empty()
    test_car_pages_uses_the_listing_creation_time()
    test_a_created_at_alone_is_not_a_stock_date()
    test_a_mapped_field_wins_over_the_raw_item()
    test_a_site_that_says_nothing_gives_none()
    test_impossible_values_are_rejected_not_stored()
    test_normalize_carries_it_and_it_is_not_a_listing_change()
    test_parse_date_accepts_the_shapes_feeds_use()
    test_days_count_to_today_while_for_sale()
    test_days_stop_at_the_sold_or_removed_date()
    test_the_stock_date_is_saved_and_served_with_days_in_stock()
    test_a_later_sync_fills_in_a_missing_date_and_ignores_one_day_wobble()
    test_a_listing_that_stops_reporting_the_date_keeps_it()
    test_a_date_change_alone_does_not_touch_last_updated()
    test_sort_by_days_in_stock_puts_unknown_last_both_ways()
    print("STOCK DATE TESTS PASSED — read, stored, counted and sorted")


if __name__ == "__main__":
    main()
