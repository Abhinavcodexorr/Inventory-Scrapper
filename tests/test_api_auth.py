"""Tests for the API-key authentication dependency (C1): every route requires
X-API-Key, /health stays open for the load balancer, and an unconfigured
API_KEY fails closed (503) rather than silently serving the world.

No pytest fixtures are used (matching every other file in this package) so
this stays runnable via `python -m tests.test_api_auth` too. The root
conftest.py sets API_KEY=test-key before dealer_scraper.config is ever
imported, which is what "the right key" means below.

Run:  pytest -q tests/test_api_auth.py
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from dealer_scraper.config import settings

RIGHT_KEY = "test-key"


def test_health_is_open_without_any_key():
    from api.main import app

    resp = TestClient(app).get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_dealers_list_requires_key():
    from api.main import app

    resp = TestClient(app).get("/api/dealers")
    assert resp.status_code == 401


def test_dealers_list_rejects_wrong_key():
    from api.main import app

    resp = TestClient(app, headers={"X-API-Key": "not-the-real-key"}).get("/api/dealers")
    assert resp.status_code == 401


def test_dealers_list_succeeds_with_right_key():
    from api.main import app

    resp = TestClient(app, headers={"X-API-Key": RIGHT_KEY}).get("/api/dealers")
    assert resp.status_code == 200


def test_discover_requires_key():
    from api.main import app

    resp = TestClient(app).post("/api/dealers/discover", json={"base_url": "https://example.com"})
    assert resp.status_code == 401


def test_dealer_purge_requires_key():
    from api.main import app

    resp = TestClient(app).delete("/api/dealers/999999?purge=true")
    assert resp.status_code == 401


def test_health_ready_requires_key():
    from api.main import app

    resp = TestClient(app).get("/health/ready")
    assert resp.status_code == 401


def test_unconfigured_api_key_fails_closed():
    from api.main import app

    original = settings.api_key
    settings.api_key = None
    try:
        resp = TestClient(app, headers={"X-API-Key": "anything"}).get("/api/dealers")
        assert resp.status_code == 503
    finally:
        settings.api_key = original


def test_auth_can_be_disabled_for_local_dev():
    from api.main import app

    original = settings.api_auth_required
    settings.api_auth_required = False
    try:
        resp = TestClient(app).get("/api/dealers")
        assert resp.status_code == 200
    finally:
        settings.api_auth_required = original


def main() -> None:
    test_health_is_open_without_any_key()
    test_dealers_list_requires_key()
    test_dealers_list_rejects_wrong_key()
    test_dealers_list_succeeds_with_right_key()
    test_discover_requires_key()
    test_dealer_purge_requires_key()
    test_health_ready_requires_key()
    test_unconfigured_api_key_fails_closed()
    test_auth_can_be_disabled_for_local_dev()
    print("API AUTH TEST PASSED")


if __name__ == "__main__":
    main()
