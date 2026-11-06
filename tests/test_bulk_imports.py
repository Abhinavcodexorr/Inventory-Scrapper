"""Bulk dealer import: a list goes in, the background runner checks each website,
and the report says what was added and what failed and why.

Run:  python -m tests.test_bulk_imports
"""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from api import bulk_runner
from api.routers.bulk_imports import normalize_website
from dealer_scraper.config import settings
from dealer_scraper.db import SessionLocal, engine
from dealer_scraper.models import Base, BulkImportRow, Dealer, DiscoveryRun
from . import require_disposable_db

CONFIG = {"list_url": "https://{host}/inventory", "card_anchor": "a[href*='/vehicles/']",
          "id_regex": "/vehicles/([^/]+)"}


def _config_for(url: str) -> dict:
    host = url.split("://", 1)[1].split("/", 1)[0]
    return {**CONFIG, "list_url": f"https://{host}/inventory", "base_url": f"https://{host}"}


def _fresh_client() -> TestClient:
    require_disposable_db()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    from api.main import app

    # Not a context manager: that would start the real background thread.
    return TestClient(app, headers={"X-API-Key": os.environ.get("API_KEY", "test-key")})


class Sites:
    """Stands in for the workers. Each dispatched check finishes on the next tick."""

    def __init__(self, outcomes: dict[str, dict] | None = None):
        self.outcomes = outcomes or {}
        self.dispatched: list[str] = []

    def dispatch(self, run_id: int, url: str) -> dict:
        self.dispatched.append(url)
        outcome = self.outcomes.get(url, {"vehicles": 12})
        with SessionLocal() as s:
            run = s.get(DiscoveryRun, run_id)
            if outcome.get("hang"):
                return {"status": "queued", "job_id": "x", "mode": "celery"}
            run.status = outcome.get("status", "completed")
            run.vehicles_verified = outcome.get("vehicles", 0)
            run.error = outcome.get("error")
            run.result = {"summary": outcome.get("summary", "summary")}
            if run.vehicles_verified:
                run.adapter_mode = "browser"
                run.scrape_config = outcome.get("config") or _config_for(url)
            run.report = {"detected_cms": outcome["cms"]} if outcome.get("cms") else None
            s.commit()
        return {"status": "queued", "job_id": "x", "mode": "celery"}


def _run_all(sites: Sites, max_ticks: int = 40) -> None:
    with patch("api.bulk_runner.dispatch_discovery", sites.dispatch), \
         patch("api.bulk_runner.choose_backend", lambda: "celery"), \
         patch("api.bulk_runner.validate_public_url", lambda url: url), \
         patch("api.bulk_runner.validate_scrape_config", lambda *a, **k: None):
        for _ in range(max_ticks):
            bulk_runner.tick()


def _rows(*sites: str) -> list[dict]:
    return [{"website": s} for s in sites]


def test_the_report_counts_added_failed_and_duplicates() -> None:
    client = _fresh_client()
    with SessionLocal() as s:
        s.add(Dealer(name="Old Motors", base_url="https://www.old-motors.ca/", adapter_mode="browser",
                     scrape_config={}))
        s.commit()
    job = client.post("/api/bulk-imports", json={
        "provider": "HillzDealer", "filename": "list.csv",
        "rows": _rows("good-cars.ca", "https://nocars.ca/", "not a website", "old-motors.ca", "GOOD-cars.ca"),
    })
    assert job.status_code == 202, job.text
    job_id = job.json()["id"]
    assert job.json()["waiting"] == 2

    _run_all(Sites({"https://nocars.ca": {"vehicles": 0, "summary": "No vehicles were found."}}))

    report = client.get(f"/api/bulk-imports/{job_id}").json()
    assert report["status"] == "completed" and report["finished_at"]
    assert (report["total"], report["added"], report["failed"], report["duplicate"]) == (5, 1, 2, 2)

    rows = client.get(f"/api/bulk-imports/{job_id}/rows").json()
    assert rows[1]["reason"] == "No vehicles were found."
    assert "spaces" in rows[2]["reason"]
    assert "Old Motors" in rows[3]["reason"]
    assert "row 1" in rows[4]["reason"]
    failed = client.get(f"/api/bulk-imports/{job_id}/rows", params={"status": "failed"})
    assert failed.headers["X-Total-Count"] == "2"


def test_the_selected_provider_is_saved_and_a_row_can_override_it() -> None:
    client = _fresh_client()
    client.post("/api/bulk-imports", json={"provider": "HillzDealer", "rows": [
        {"website": "one.ca", "name": "Number One", "city": "Toronto"},
        {"website": "two-cars.ca", "provider": "Convertus"},
    ]})
    _run_all(Sites({"https://one.ca": {"vehicles": 3, "cms": ["wordpress"]}}))
    with SessionLocal() as s:
        one = s.query(Dealer).filter_by(base_url="https://one.ca").one()
        two = s.query(Dealer).filter_by(base_url="https://two-cars.ca").one()
    assert (one.name, one.city, one.website_provider, one.detected_platform) == (
        "Number One", "Toronto", "HillzDealer", "wordpress")
    assert (two.name, two.website_provider) == ("Two Cars", "Convertus")
    assert one.scrape_config and one.active


def test_a_blank_provider_falls_back_to_the_detected_platform() -> None:
    client = _fresh_client()
    client.post("/api/bulk-imports", json={"rows": _rows("one.ca")})
    _run_all(Sites({"https://one.ca": {"vehicles": 3, "cms": ["convertus"]}}))
    with SessionLocal() as s:
        assert s.query(Dealer).one().website_provider == "convertus"


def test_no_more_than_the_limit_is_checked_at_once() -> None:
    client = _fresh_client()
    client.post("/api/bulk-imports", json={"rows": _rows("a1.ca", "a2.ca", "a3.ca", "a4.ca")})
    sites = Sites({f"https://a{i}.ca": {"hang": True} for i in range(1, 5)})
    _run_all(sites, max_ticks=3)
    assert sites.dispatched == ["https://a1.ca", "https://a2.ca"]
    assert settings.bulk_import_concurrency == 2


def test_a_killed_worker_fails_its_row_instead_of_hanging_the_import() -> None:
    client = _fresh_client()
    job_id = client.post("/api/bulk-imports", json={"rows": _rows("stuck.ca")}).json()["id"]
    _run_all(Sites({"https://stuck.ca": {"hang": True}}), max_ticks=2)
    with patch.object(settings, "bulk_import_queued_timeout_seconds", -1):
        _run_all(Sites(), max_ticks=3)
    report = client.get(f"/api/bulk-imports/{job_id}").json()
    assert (report["status"], report["failed"]) == ("completed", 1)
    rows = client.get(f"/api/bulk-imports/{job_id}/rows").json()
    assert "never started" in rows[0]["reason"]


def test_no_worker_pauses_the_list_and_says_so() -> None:
    client = _fresh_client()
    job_id = client.post("/api/bulk-imports", json={"rows": _rows("one.ca")}).json()["id"]
    with patch("api.bulk_runner.choose_backend", lambda: "no_worker"):
        bulk_runner.tick()
    report = client.get(f"/api/bulk-imports/{job_id}").json()
    assert (report["status"], report["waiting"]) == ("running", 1)
    assert "no background worker" in report["notice"]

    _run_all(Sites())
    report = client.get(f"/api/bulk-imports/{job_id}").json()
    assert (report["status"], report["added"], report["notice"]) == ("completed", 1, None)


def test_stop_skips_what_is_waiting_and_retry_checks_it_again() -> None:
    client = _fresh_client()
    job_id = client.post("/api/bulk-imports", json={"rows": _rows("a1.ca", "a2.ca", "a3.ca")}).json()["id"]
    assert client.post(f"/api/bulk-imports/{job_id}/stop").json()["skipped"] == 3
    _run_all(Sites(), max_ticks=1)
    assert client.get(f"/api/bulk-imports/{job_id}").json()["status"] == "stopped"

    assert client.post(f"/api/bulk-imports/{job_id}/retry").json()["waiting"] == 3
    _run_all(Sites())
    report = client.get(f"/api/bulk-imports/{job_id}").json()
    assert (report["status"], report["added"], report["skipped"]) == ("completed", 3, 0)


def test_retry_leaves_invalid_addresses_alone_and_is_refused_while_running() -> None:
    client = _fresh_client()
    job_id = client.post("/api/bulk-imports", json={"rows": _rows("one.ca", "bad address")}).json()["id"]
    assert client.post(f"/api/bulk-imports/{job_id}/retry").status_code == 409
    _run_all(Sites())
    assert client.post(f"/api/bulk-imports/{job_id}/retry").json()["waiting"] == 0


def test_preview_saves_nothing() -> None:
    client = _fresh_client()
    body = client.post("/api/bulk-imports/preview", json={"rows": _rows("one.ca", "bad address", "one.ca")}).json()
    assert (body["ready"], body["invalid"], body["duplicate"]) == (1, 1, 1)
    with SessionLocal() as s:
        assert s.query(BulkImportRow).count() == 0


def test_a_list_needs_a_login_and_has_a_size_limit() -> None:
    client = _fresh_client()
    from api.main import app

    assert TestClient(app).post("/api/bulk-imports", json={"rows": _rows("one.ca")}).status_code == 401
    assert client.post("/api/bulk-imports", json={"rows": []}).status_code == 422
    assert client.post("/api/bulk-imports", json={"rows": _rows("a.ca") * 5001}).status_code == 422


def test_addresses_are_stored_the_way_dealers_are() -> None:
    for raw, expected in (
        ("cardora.ca", "https://cardora.ca"),
        (" HTTPS://WWW.Cardora.CA/ ", "https://www.cardora.ca"),
        ("http://cardora.ca/store-1/?utm=x#top", "http://cardora.ca/store-1"),
    ):
        assert normalize_website(raw) == expected


def test_unusable_addresses_are_refused() -> None:
    for raw in ("", "not a website", "localhost", "ftp://cardora.ca", "https://user:pw@cardora.ca",
                "https://cardora.ca:8080", "http://169.254.169.254/latest", "https://[::1]/"):
        with pytest.raises(ValueError):
            normalize_website(raw)


def test_a_site_verified_long_ago_still_counts_as_verified() -> None:
    """Bulk lists produce many checks, and the verified-site lookup only used to see
    the newest 50 across every site, so an earlier success was reported as missing."""
    client = _fresh_client()
    from api.routers.dealers import _require_proven_site_or_409

    with SessionLocal() as s:
        s.add(DiscoveryRun(base_url="https://proven.ca", status="completed", vehicles_verified=5))
        s.add_all(DiscoveryRun(base_url=f"https://other{i}.ca", status="completed", vehicles_verified=1)
                  for i in range(60))
        s.commit()
        _require_proven_site_or_409(s, "https://proven.ca")


def main() -> None:
    test_the_report_counts_added_failed_and_duplicates()
    test_the_selected_provider_is_saved_and_a_row_can_override_it()
    test_a_blank_provider_falls_back_to_the_detected_platform()
    test_no_more_than_the_limit_is_checked_at_once()
    test_a_killed_worker_fails_its_row_instead_of_hanging_the_import()
    test_no_worker_pauses_the_list_and_says_so()
    test_stop_skips_what_is_waiting_and_retry_checks_it_again()
    test_retry_leaves_invalid_addresses_alone_and_is_refused_while_running()
    test_preview_saves_nothing()
    test_a_list_needs_a_login_and_has_a_size_limit()
    test_addresses_are_stored_the_way_dealers_are()
    test_unusable_addresses_are_refused()
    test_a_site_verified_long_ago_still_counts_as_verified()
    print("bulk import checks passed")


if __name__ == "__main__":
    main()
