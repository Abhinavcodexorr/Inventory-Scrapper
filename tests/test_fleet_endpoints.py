"""Endpoints that let the UI work at 20,000 dealers without holding them all:
paging and search, duplicate-website lookup, and fleet-wide runs and trends.

Run:  python -m tests.test_fleet_endpoints
"""
from __future__ import annotations

import os
from datetime import date
from unittest.mock import patch

from fastapi.testclient import TestClient

from dealer_scraper.db import SessionLocal, engine
from dealer_scraper.models import Base, DailyInventorySnapshot, Dealer, ScrapeRun
from . import require_disposable_db


def _client() -> TestClient:
    from api.main import app

    return TestClient(app, headers={"X-API-Key": os.environ.get("API_KEY", "test-key")})


def _seed() -> dict[str, int]:
    require_disposable_db()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with SessionLocal() as s:
        dealers = [
            Dealer(name="Alpha Motors", base_url="https://www.alpha.example/", city="Toronto",
                   website_provider="hillz", inventory_count=50, adapter_mode="json_api",
                   scrape_config={"endpoint": "https://alpha.example/feed"}),
            Dealer(name="Beta Cars", base_url="https://beta.example", city="Calgary",
                   inventory_count=200, adapter_mode="json_api", scrape_config={"endpoint": "x"}),
            Dealer(name="Group Store", base_url="https://group.example/store-1", inventory_count=0,
                   adapter_mode="json_api", scrape_config={}),
            Dealer(name="Retired", base_url="https://retired.example", active=False, inventory_count=5,
                   adapter_mode="json_api", scrape_config={"endpoint": "x"}),
        ]
        s.add_all(dealers)
        s.flush()
        ids = {d.name.split()[0].lower(): d.id for d in dealers}
        for status in ("completed", "failed", "completed"):
            s.add(ScrapeRun(dealer_id=ids["alpha"], status=status))
        s.add(ScrapeRun(dealer_id=ids["beta"], status="failed", error="boom"))
        today = date.today()
        for dealer_id, total in ((ids["alpha"], 50), (ids["beta"], 200)):
            s.add(DailyInventorySnapshot(dealer_id=dealer_id, snapshot_date=today, total_active=total,
                                         added=1, removed=0, sold=0, price_changed=2))
        s.commit()
    return ids


def test_list_pages_searches_and_sorts_on_the_server() -> None:
    _seed()
    c = _client()
    page = c.get("/api/dealers", params={"limit": 2, "sort": "inventory_count", "order": "desc"})
    assert [d["name"] for d in page.json()] == ["Beta Cars", "Alpha Motors"]
    assert page.headers["X-Total-Count"] == "4"
    for q, name in (("alpha.example", "Alpha Motors"), ("calgary", "Beta Cars"), ("HILLZ", "Alpha Motors")):
        assert [d["name"] for d in c.get("/api/dealers", params={"q": q}).json()] == [name]
    # A LIKE wildcard in the search is text, not a pattern.
    assert c.get("/api/dealers", params={"q": "%"}).json() == []


def test_list_returns_requested_ids_only() -> None:
    ids = _seed()
    got = _client().get("/api/dealers", params={"ids": f"{ids['alpha']},{ids['beta']}"}).json()
    assert {d["id"] for d in got} == {ids["alpha"], ids["beta"]}
    assert _client().get("/api/dealers", params={"ids": "1,nope"}).status_code == 422


def test_lookup_ignores_scheme_www_and_trailing_slash() -> None:
    ids = _seed()
    c = _client()
    for url in ("https://alpha.example", "http://WWW.ALPHA.EXAMPLE/", "alpha.example/"):
        assert c.get("/api/dealers/lookup", params={"url": url}).json()["id"] == ids["alpha"], url
    # The dealer being edited is not its own duplicate.
    assert c.get("/api/dealers/lookup", params={"url": "alpha.example", "exclude_id": ids["alpha"]}).json() is None
    # Another path on a shared host is another dealer, and the host alone is not a match.
    assert c.get("/api/dealers/lookup", params={"url": "group.example/store-2"}).json() is None
    assert c.get("/api/dealers/lookup", params={"url": "group.example"}).json() is None
    assert c.get("/api/dealers/lookup", params={"url": "notalpha.example"}).json() is None


def test_create_refuses_the_same_site_written_differently() -> None:
    _seed()
    res = _client().post("/api/dealers", json={"name": "Again", "base_url": "http://alpha.example",
                                               "adapter_mode": "json_api", "scrape_config": {}})
    assert res.status_code == 409, res.text


def test_template_is_a_proven_dealer_of_that_provider() -> None:
    ids = _seed()
    c = _client()
    assert c.get("/api/dealers/template", params={"provider": "hillz"}).json()["id"] == ids["alpha"]
    assert c.get("/api/dealers/template", params={"provider": "unknown"}).json() is None


def test_runs_across_all_dealers_page_and_filter() -> None:
    ids = _seed()
    c = _client()
    page = c.get("/api/dealers/runs", params={"limit": 2})
    assert page.headers["X-Total-Count"] == "4"
    first = page.json()[0]
    assert (first["dealer_name"], first["error"], first["dealer_active"]) == ("Beta Cars", "boom", True)
    assert len(page.json()) == 2
    assert len(c.get("/api/dealers/runs", params={"status": "failed"}).json()) == 2
    assert len(c.get("/api/dealers/runs", params={"dealer_id": ids["alpha"]}).json()) == 3
    assert len(c.get("/api/dealers/runs", params={"q": "beta"}).json()) == 1


def test_run_stats_and_latest_statuses() -> None:
    ids = _seed()
    c = _client()
    stats = c.get("/api/dealers/runs/stats").json()
    assert (stats["sampled"], stats["completed"], stats["failed"], stats["success_rate"]) == (4, 2, 2, 50)
    got = c.get("/api/dealers/runs/latest-statuses",
                params={"ids": str(ids["alpha"]), "per_dealer": 2}).json()
    assert got == {str(ids["alpha"]): ["completed", "failed"]}


def test_sync_all_answers_for_active_dealers_with_settings() -> None:
    ids = _seed()
    with patch("api.routers.dealers.dispatch_sync") as dispatch:
        body = _client().post("/api/dealers/sync-all").json()
    assert body == {"queued": 2, "already_running": 0, "skipped_no_settings": 1}
    assert {call.args[0] for call in dispatch.call_args_list} == {ids["alpha"], ids["beta"]}


def test_dashboard_aggregates() -> None:
    ids = _seed()
    c = _client()
    day = c.get("/api/dashboard/trend").json()
    assert [(d["total"], d["price_changed"], d["dealers"]) for d in day] == [(250, 4, 2)]
    assert c.get("/api/dashboard/trend", params={"dealer_id": ids["alpha"]}).json()[0]["total"] == 50
    share = c.get("/api/dashboard/inventory-share", params={"top": 1}).json()
    assert ([r["name"] for r in share["top"]], share["total"], share["dealer_count"]) == (["Beta Cars"], 255, 4)
    assert (share["rest_count"], share["rest_total"]) == (2, 55)
    top = c.get("/api/dashboard/dealers", params={"limit": 1}).json()
    assert [s["dealer_name"] for s in top] == ["Beta Cars"]
    assert len(c.get("/api/dashboard/dealers").json()) == 4
    attention = c.get("/api/dashboard/dealers", params={"attention": True}).json()
    assert len(attention) == 4  # none has synced yet


def main() -> None:
    test_list_pages_searches_and_sorts_on_the_server()
    test_list_returns_requested_ids_only()
    test_lookup_ignores_scheme_www_and_trailing_slash()
    test_create_refuses_the_same_site_written_differently()
    test_template_is_a_proven_dealer_of_that_provider()
    test_runs_across_all_dealers_page_and_filter()
    test_run_stats_and_latest_statuses()
    test_sync_all_answers_for_active_dealers_with_settings()
    test_dashboard_aggregates()
    print("OK")


if __name__ == "__main__":
    main()
