"""Fleet-wide site compatibility report (GET /api/dealers/compatibility).

Answers the two commercial questions across every site we have evaluated:
  * which dealer websites the engine can scrape as-is
  * which need chargeable new-website-structure development

Run:  DATABASE_URL="sqlite:///./.compat_test.db" python -m tests.test_compatibility_report
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

from dealer_scraper.db import SessionLocal, engine
from dealer_scraper.models import Base, Dealer, DiscoveryRun, SitePattern

from . import require_disposable_db


def _client():
    """Authenticated TestClient — mirrors the `client` fixture in conftest.py so
    this module also runs standalone via `python -m tests.test_...`."""
    from fastapi.testclient import TestClient

    from api.main import app

    return TestClient(app, headers={"X-API-Key": os.environ.get("API_KEY", "test-key")})


def _seed() -> None:
    require_disposable_db()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    now = datetime.now(timezone.utc)
    with SessionLocal() as s:
        dealer = Dealer(name="Clutch", base_url="https://www.clutch.ca",
                        adapter_mode="browser", scrape_config={})
        s.add(dealer)
        s.flush()
        s.add_all([
            # onboarded + supported
            DiscoveryRun(base_url="https://www.clutch.ca", dealer_id=dealer.id,
                         status="completed", verdict="ready", confidence="high",
                         requires_custom_work=False, adapter_mode="browser",
                         vehicles_verified=3161, finished_at=now),
            # evaluated only, needs paid work
            DiscoveryRun(base_url="https://tricky-dealer.ca", status="completed",
                         verdict="unsupported", confidence="failed",
                         requires_custom_work=True, vehicles_verified=0, finished_at=now),
            # two hosts on one CMS -> reuse signal
            DiscoveryRun(base_url="https://dealerA.ca", status="completed", verdict="ready",
                         confidence="high", requires_custom_work=False,
                         adapter_mode="json_api", vehicles_verified=210, finished_at=now),
            DiscoveryRun(base_url="https://dealerB.ca", status="completed", verdict="ready",
                         confidence="high", requires_custom_work=False,
                         adapter_mode="json_api", vehicles_verified=180, finished_at=now),
            # an in-flight run must not appear at all
            DiscoveryRun(base_url="https://pending.ca", status="running"),
        ])
        s.add_all([
            SitePattern(host="dealera.ca", cms_platform="DealerOn",
                        pattern_kind="listing_url", pattern={}),
            SitePattern(host="dealerb.ca", cms_platform="DealerOn",
                        pattern_kind="listing_url", pattern={}),
        ])
        s.commit()


def _report(params: str = "") -> dict:
    resp = _client().get(f"/api/dealers/compatibility{params}")
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_summary_counts_every_verdict() -> None:
    _seed()
    summary = _report()["summary"]
    assert summary["hosts_total"] == 4, summary      # the 'running' run is excluded
    assert summary["ready"] == 3, summary
    assert summary["unsupported"] == 1, summary
    assert summary["billable"] == 1, summary
    assert summary["onboarded"] == 1, summary
    assert summary["distinct_cms_platforms"] == 1, summary


def test_billable_sites_sort_first_so_they_are_impossible_to_miss() -> None:
    _seed()
    items = _report()["items"]
    assert items[0]["host"] == "tricky-dealer.ca", items[0]
    assert items[0]["requires_custom_work"] is True


def test_billable_only_filter_is_the_quoting_list() -> None:
    _seed()
    items = _report("?billable_only=true")["items"]
    assert [i["host"] for i in items] == ["tricky-dealer.ca"], items


def test_shared_cms_is_reported_as_reuse_not_new_work() -> None:
    _seed()
    by_host = {i["host"]: i for i in _report()["items"]}
    # One scraper already covers this platform, so a second dealer on it is reuse.
    assert by_host["dealera.ca"]["hosts_sharing_cms"] == 2
    assert by_host["dealerb.ca"]["cms_platform"] == "DealerOn"
    assert by_host["dealera.ca"]["requires_custom_work"] is False


def test_evaluated_sites_are_distinguished_from_onboarded_ones() -> None:
    _seed()
    by_host = {i["host"]: i for i in _report()["items"]}
    assert by_host["clutch.ca"]["onboarded"] is True
    assert by_host["clutch.ca"]["dealer_name"] == "Clutch"
    # Discovered before any dealer row exists — still belongs in the report.
    assert by_host["tricky-dealer.ca"]["onboarded"] is False
    assert by_host["tricky-dealer.ca"]["dealer_id"] is None


def test_hosts_are_normalised_so_www_does_not_split_a_site() -> None:
    _seed()
    hosts = {i["host"] for i in _report()["items"]}
    assert "clutch.ca" in hosts and "www.clutch.ca" not in hosts


def test_report_requires_authentication() -> None:
    _seed()
    from fastapi.testclient import TestClient

    from api.main import app

    resp = TestClient(app).get("/api/dealers/compatibility")
    assert resp.status_code == 401, resp.text


def main() -> None:
    test_summary_counts_every_verdict()
    test_billable_sites_sort_first_so_they_are_impossible_to_miss()
    test_billable_only_filter_is_the_quoting_list()
    test_shared_cms_is_reported_as_reuse_not_new_work()
    test_evaluated_sites_are_distinguished_from_onboarded_ones()
    test_hosts_are_normalised_so_www_does_not_split_a_site()
    test_report_requires_authentication()
    print("COMPATIBILITY REPORT TESTS PASSED — supported vs billable sites, CMS reuse")


if __name__ == "__main__":
    main()
