"""The dealer inventory Status filter: list filtering and the per-status counts.

Run against a throwaway database:

    DATABASE_URL="sqlite:///./vstatus_test.db" API_KEY=test-key python -m pytest tests/test_vehicle_status_filter.py
"""
from __future__ import annotations

import os

from fastapi.testclient import TestClient

from dealer_scraper.db import SessionLocal, engine
from dealer_scraper.models import Base, Car, Dealer
from . import require_disposable_db


def _setup() -> tuple[TestClient, int, int]:
    """Dealer A: 3 active, 2 sold, 1 removed. Dealer B: 1 sold (must never leak into A)."""
    require_disposable_db()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    from api.main import app

    with SessionLocal() as s:
        a = Dealer(name="A", base_url="https://a.example", adapter_mode="json_api", scrape_config={}, active=True)
        b = Dealer(name="B", base_url="https://b.example", adapter_mode="json_api", scrape_config={}, active=True)
        s.add_all([a, b])
        s.flush()
        n = 0
        for dealer, status, count in ((a, "active", 3), (a, "sold", 2), (a, "removed", 1), (b, "sold", 1)):
            for _ in range(count):
                n += 1
                s.add(Car(dealer_id=dealer.id, external_id=f"E{n}", vin=f"1HGCM82633A{n:06d}",
                          make="Honda", model="Civic", year=2020, status=status))
        s.commit()
        ids = (a.id, b.id)
    client = TestClient(app, headers={"X-API-Key": os.environ.get("API_KEY", "test-key")})
    return client, *ids


def test_each_status_returns_only_that_status_for_that_dealer() -> None:
    client, a, _ = _setup()
    for status, expected in (("active", 3), ("sold", 2), ("removed", 1)):
        body = client.get("/api/vehicles", params={"dealer_id": a, "status": status}).json()
        assert body["meta"]["total"] == expected, status
        assert {c["status"] for c in body["items"]} == {status}


def test_no_status_means_all_of_the_dealers_cars() -> None:
    client, a, _ = _setup()
    assert client.get("/api/vehicles", params={"dealer_id": a}).json()["meta"]["total"] == 6


def test_unknown_status_is_rejected_not_silently_empty() -> None:
    client, a, _ = _setup()
    assert client.get("/api/vehicles", params={"dealer_id": a, "status": "Sold "}).status_code == 422
    assert client.get("/api/vehicles", params={"dealer_id": a, "status": "all"}).status_code == 422


def test_counts_match_what_each_filter_returns() -> None:
    client, a, b = _setup()
    counts = client.get("/api/vehicles/status-counts", params={"dealer_id": a}).json()
    assert counts == {"active": 3, "sold": 2, "removed": 1, "total": 6}
    for status in ("active", "sold", "removed"):
        listed = client.get("/api/vehicles", params={"dealer_id": a, "status": status}).json()["meta"]["total"]
        assert listed == counts[status]
    assert client.get("/api/vehicles/status-counts", params={"dealer_id": b}).json() == {
        "active": 0, "sold": 1, "removed": 0, "total": 1}


def test_counts_without_a_dealer_cover_every_dealer_and_are_zero_when_empty() -> None:
    client, _, _ = _setup()
    assert client.get("/api/vehicles/status-counts").json()["total"] == 7
    assert client.get("/api/vehicles/status-counts", params={"dealer_id": 99999}).json() == {
        "active": 0, "sold": 0, "removed": 0, "total": 0}


def test_status_counts_is_not_swallowed_by_the_car_id_route() -> None:
    client, _, _ = _setup()
    assert client.get("/api/vehicles/status-counts").status_code == 200


def test_list_can_be_sorted_by_create_date() -> None:
    from datetime import datetime, timedelta, timezone

    client, a, _ = _setup()
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with SessionLocal() as s:
        for i, car in enumerate(s.query(Car).filter(Car.dealer_id == a).order_by(Car.id)):
            car.first_seen_at = base + timedelta(days=i)
        s.commit()
    newest = client.get("/api/vehicles", params={"dealer_id": a, "sort": "first_seen_at", "order": "desc"}).json()["items"]
    dates = [c["first_seen_at"] for c in newest]
    assert dates == sorted(dates, reverse=True) and len(set(dates)) == 6
    oldest = client.get("/api/vehicles", params={"dealer_id": a, "sort": "first_seen_at", "order": "asc"}).json()["items"]
    assert [c["id"] for c in oldest] == [c["id"] for c in reversed(newest)]


def main() -> None:
    test_each_status_returns_only_that_status_for_that_dealer()
    test_no_status_means_all_of_the_dealers_cars()
    test_unknown_status_is_rejected_not_silently_empty()
    test_counts_match_what_each_filter_returns()
    test_counts_without_a_dealer_cover_every_dealer_and_are_zero_when_empty()
    test_status_counts_is_not_swallowed_by_the_car_id_route()
    test_list_can_be_sorted_by_create_date()
    print("VEHICLE STATUS FILTER TESTS PASSED — each status lists, counts and rejects correctly")


if __name__ == "__main__":
    main()
