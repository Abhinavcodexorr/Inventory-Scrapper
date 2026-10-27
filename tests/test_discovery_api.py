"""FastAPI TestClient tests for the discovery endpoints: the ?sync=true legacy
contract, the default background-job flow (202 + polling), in-flight dedupe,
run history, cancellation, and the dealer-purge FK-safety behavior.

The coordinator is monkeypatched (DiscoveryCoordinator._run_browser_phase) so
this suite never touches Playwright, and SYNC_BACKEND=local / a malformed
REDIS_URL keep it fast and independent of any broker being reachable.

Run:  DATABASE_URL="sqlite:///./discovery_api_test.db" SYNC_BACKEND=local \
      REDIS_URL="not-a-valid-url" python -m tests.test_discovery_api
"""
from __future__ import annotations

import os
import time

from fastapi.testclient import TestClient

from dealer_scraper.db import SessionLocal, engine
from dealer_scraper.discovery import coordinator as coordinator_mod
from dealer_scraper.models import Base, DiscoveryRun
from . import require_disposable_db


def _client() -> TestClient:
    """Every route requires X-API-Key as of C1 — conftest.py sets API_KEY to
    this value (test-key) before dealer_scraper.config is ever imported."""
    from api.main import app

    return TestClient(app, headers={"X-API-Key": os.environ.get("API_KEY", "test-key")})


def _patch_fake_discovery(vehicles_found: int = 88) -> None:
    def _run(self, ctx, result):
        self.progress("explore", 1, 4)
        self.progress("candidates", 2, 4)
        result.ok = True
        result.confidence = "high"
        result.verdict = "ready"
        result.requires_custom_work = False
        result.summary = "fake summary"
        result.scrape_config = {"endpoint": "https://example.com/api/vehicles",
                                 "field_map": {"vin": "vin"}}
        result.adapter_mode = "json_api"
        result.vehicles_found = vehicles_found
        result.listing_url = "https://example.com/inventory"
        ctx.report.add_note("fake note from api test")

    coordinator_mod.DiscoveryCoordinator._run_browser_phase = _run


# Applied at import time, not just inside main(): unlike test_discovery_persistence.py
# (where each test self-patches its own coordinator behavior), every test in this
# file shares one fake "always succeeds" coordinator. Patching only inside main()
# meant this worked under `python -m tests.test_discovery_api` (which always
# imports-then-calls-main in that order) but silently used the REAL coordinator
# under pytest, which imports the module and calls test functions directly.
_patch_fake_discovery()


def _poll_until_done(client: TestClient, run_id: int, timeout_s: float = 15.0) -> dict:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        resp = client.get(f"/api/dealers/discover/runs/{run_id}")
        data = resp.json()
        if data["status"] in ("completed", "failed", "cancelled"):
            return data
        time.sleep(0.1)
    raise AssertionError(f"discovery run {run_id} did not finish within {timeout_s}s")


def test_sync_true_returns_legacy_discovery_out_shape():
    client = _client()
    resp = client.post("/api/dealers/discover?sync=true", json={"base_url": "https://sync-example.com"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["verdict"] == "ready"
    assert body["vehicles_found"] == 88
    assert body["run_id"] is None
    assert body["report_available"] is False


def test_background_dispatch_returns_202_and_polls_to_completion():
    client = _client()
    resp = client.post("/api/dealers/discover", json={"base_url": "https://bg-example.com"})
    assert resp.status_code == 202
    job = resp.json()
    assert job["mode"] == "local"
    run_id = job["run_id"]

    data = _poll_until_done(client, run_id)
    assert data["status"] == "completed"
    assert data["result"]["vehicles_found"] == 88
    assert data["result"]["run_id"] == run_id
    assert data["result"]["report_available"] is True
    assert data["report"] is None  # include_report defaults to false

    detailed = client.get(f"/api/dealers/discover/runs/{run_id}?include_report=true").json()
    assert "fake note from api test" in detailed["report"]["notes"]


def test_in_flight_discovery_is_deduped_not_duplicated():
    client = _client()
    first = client.post("/api/dealers/discover", json={"base_url": "https://dedupe-api-example.com"})
    second = client.post("/api/dealers/discover", json={"base_url": "https://dedupe-api-example.com"})
    assert first.json()["run_id"] == second.json()["run_id"]
    _poll_until_done(client, first.json()["run_id"])


def test_discovery_run_history_filters_by_base_url():
    client = _client()
    resp = client.post("/api/dealers/discover", json={"base_url": "https://history-example.com"})
    run_id = resp.json()["run_id"]
    _poll_until_done(client, run_id)

    history = client.get("/api/dealers/discover/runs", params={"base_url": "https://history-example.com"})
    assert history.status_code == 200
    rows = history.json()
    assert len(rows) == 1
    assert rows[0]["id"] == run_id
    assert rows[0]["verdict"] == "ready"


def test_cancel_not_running_and_unknown_run():
    client = _client()
    resp = client.post("/api/dealers/discover", json={"base_url": "https://cancel-finished-example.com"})
    run_id = resp.json()["run_id"]
    _poll_until_done(client, run_id)

    already_finished = client.post(f"/api/dealers/discover/runs/{run_id}/cancel")
    assert already_finished.status_code == 200
    assert already_finished.json()["status"] == "not_running"

    unknown = client.post("/api/dealers/discover/runs/999999/cancel")
    assert unknown.status_code == 404

    unknown_status = client.get("/api/dealers/discover/runs/999999")
    assert unknown_status.status_code == 404


def test_dealer_delete_keeps_discovery_history_nulls_dealer_id():
    client = _client()
    created = client.post("/api/dealers", json={
        "name": "Purge API Test", "base_url": "https://purge-api-example.com",
        "adapter_mode": "stub", "scrape_config": {},
    })
    dealer_id = created.json()["id"]

    disc = client.post("/api/dealers/discover",
                        json={"base_url": "https://purge-api-example.com", "dealer_id": dealer_id})
    run_id = disc.json()["run_id"]
    _poll_until_done(client, run_id)

    deleted = client.delete(f"/api/dealers/{dealer_id}")
    assert deleted.status_code == 204

    with SessionLocal() as s:
        row = s.get(DiscoveryRun, run_id)
        assert row is not None
        assert row.dealer_id is None


def main() -> None:
    require_disposable_db()   # drop_all() below — never let this hit a real DB
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    # _patch_fake_discovery() already ran at import time, above.

    test_sync_true_returns_legacy_discovery_out_shape()
    test_background_dispatch_returns_202_and_polls_to_completion()
    test_in_flight_discovery_is_deduped_not_duplicated()
    test_discovery_run_history_filters_by_base_url()
    test_cancel_not_running_and_unknown_run()
    test_dealer_delete_keeps_discovery_history_nulls_dealer_id()
    print("DISCOVERY API TEST PASSED")


if __name__ == "__main__":
    main()
