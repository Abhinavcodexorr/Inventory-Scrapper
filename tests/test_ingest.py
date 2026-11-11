"""A scrape done on another machine is pushed in and handled like a local one.

For sites the server cannot reach (country-restricted dealers). Run against a
throwaway database:

    DATABASE_URL="sqlite:///./ingest_test.db" python -m pytest tests/test_ingest.py
"""
from __future__ import annotations

import os

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from dealer_scraper.db import SessionLocal, engine
from dealer_scraper.models import Base, Car, Dealer
from . import require_disposable_db


def _client() -> TestClient:
    require_disposable_db()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    from api.main import app

    return TestClient(app, headers={"X-API-Key": os.environ.get("API_KEY", "test-key")})


def _dealer() -> int:
    with SessionLocal() as s:
        d = Dealer(name="Pushed", base_url="https://pushed.example", adapter_mode="json_api",
                   scrape_config={"endpoint": "https://x.test/{page}", "field_map": {}}, active=True)
        s.add(d)
        s.commit()
        return d.id


def _car(n: int, price: str = "10000.00") -> dict:
    return {"external_id": f"S{n}", "vin": f"1HGCM82633A{n:06d}", "make": "Honda", "model": "Civic",
            "year": 2020, "price": price, "mileage": 1000 + n, "url": f"https://pushed.example/v/{n}"}


def _count(dealer_id: int, status: str | None = None) -> int:
    with SessionLocal() as s:
        q = select(func.count()).select_from(Car).where(Car.dealer_id == dealer_id)
        if status:
            q = q.where(Car.status == status)
        return s.scalar(q)


def test_pushed_cars_are_saved_like_a_normal_scrape() -> None:
    client = _client()
    did = _dealer()
    r = client.post(f"/api/dealers/{did}/ingest",
                    json={"vehicles": [_car(i) for i in range(5)], "reported_total": 5})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "completed" and body["scraped"] == 5 and body["new"] == 5
    assert _count(did, "active") == 5


def test_a_second_push_updates_prices_and_does_not_duplicate() -> None:
    client = _client()
    did = _dealer()
    client.post(f"/api/dealers/{did}/ingest", json={"vehicles": [_car(i) for i in range(5)], "reported_total": 5})
    r = client.post(f"/api/dealers/{did}/ingest",
                    json={"vehicles": [_car(i, "9000.00") for i in range(5)], "reported_total": 5})
    assert r.json()["new"] == 0
    assert _count(did) == 5


def test_a_car_missing_from_a_complete_push_is_removed() -> None:
    client = _client()
    did = _dealer()
    client.post(f"/api/dealers/{did}/ingest", json={"vehicles": [_car(i) for i in range(5)], "reported_total": 5})
    client.post(f"/api/dealers/{did}/ingest", json={"vehicles": [_car(i) for i in range(4)], "reported_total": 4})
    assert _count(did, "removed") == 1


def test_a_partial_push_does_not_remove_the_rest() -> None:
    """Site says 5 cars, the push only has 2: the removal safety gate must hold."""
    client = _client()
    did = _dealer()
    client.post(f"/api/dealers/{did}/ingest", json={"vehicles": [_car(i) for i in range(5)], "reported_total": 5})
    client.post(f"/api/dealers/{did}/ingest", json={"vehicles": [_car(i) for i in range(2)], "reported_total": 5})
    assert _count(did, "removed") == 0


def test_an_empty_push_is_refused_and_removes_nothing() -> None:
    client = _client()
    did = _dealer()
    client.post(f"/api/dealers/{did}/ingest", json={"vehicles": [_car(i) for i in range(3)], "reported_total": 3})
    r = client.post(f"/api/dealers/{did}/ingest", json={"vehicles": [], "reported_total": 0})
    assert r.status_code == 422
    assert _count(did, "active") == 3


def test_unknown_dealer_is_404_and_no_key_is_refused() -> None:
    client = _client()
    assert client.post("/api/dealers/99999/ingest", json={"vehicles": [_car(1)]}).status_code == 404
    anon = TestClient(client.app)
    assert anon.post("/api/dealers/1/ingest", json={"vehicles": [_car(1)]}).status_code in (401, 503)
