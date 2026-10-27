"""Onboarding a dealer on a platform we already support must be cheap.

HANDOFF/03 Task 1. Dealer websites cluster by vendor, not by dealership — the
per-website-structure pricing model assumes the second dealer on a known
platform costs a database row, not a fresh discovery run. Without config reuse
every dealership is a bespoke investigation, which is what does not scale to a
hundred sites.

Covers: base_url normalisation (so the duplicate-409 is real), config reuse by
provider and by fingerprinted platform, URL retargeting (the dangerous part),
and list pagination.

Run:  DATABASE_URL="sqlite:///./.reuse_test.db" python -m tests.test_dealer_onboarding_reuse
"""
from __future__ import annotations

import os

from dealer_scraper.db import SessionLocal, engine
from dealer_scraper.models import Base, Dealer

from . import require_disposable_db

TEMPLATE_CONFIG = {
    "list_url": "https://alpha-motors.ca/inventory",
    "base_url": "https://alpha-motors.ca",
    "card_anchor": "a[href*='/vehicles/']",
    "wait_selector": "a[href*='/vehicles/']",
    "id_regex": "/vehicles/(\\d+)",
    "extract": "jsonld",
    "paginate": "url",
    "page_url": "https://alpha-motors.ca/inventory?page={page}",
    "max_pages": 50,
}


def _client():
    from fastapi.testclient import TestClient

    from api.main import app

    return TestClient(app, headers={"X-API-Key": os.environ.get("API_KEY", "test-key")})


def _reset(with_template: bool = True) -> None:
    require_disposable_db()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    if not with_template:
        return
    with SessionLocal() as s:
        s.add(Dealer(
            name="Alpha Motors", base_url="https://alpha-motors.ca",
            website_provider="dealeron", detected_platform="dealeron",
            adapter_mode="browser", scrape_config=dict(TEMPLATE_CONFIG),
            inventory_count=412, active=True,
        ))
        s.commit()


# --- base_url normalisation --------------------------------------------------
def test_trailing_slash_and_case_do_not_create_a_second_dealer() -> None:
    """Without normalisation these are three rows, all passing the 409, all
    scraping the same lot into separate inventories."""
    _reset(with_template=False)
    c = _client()
    first = c.post("/api/dealers", json={
        "name": "Beta", "base_url": "https://beta-cars.ca",
        "adapter_mode": "stub", "scrape_config": {"items": []}})
    assert first.status_code == 201, first.text

    for variant in ("https://beta-cars.ca/", "HTTPS://Beta-Cars.CA", "https://beta-cars.ca///"):
        dup = c.post("/api/dealers", json={
            "name": "Beta again", "base_url": variant,
            "adapter_mode": "stub", "scrape_config": {"items": []}})
        assert dup.status_code == 409, f"{variant} -> {dup.status_code}"


def test_normalisation_preserves_path_case() -> None:
    """Host is case-insensitive, path is not — lowercasing the path would break
    sites with case-sensitive routes."""
    _reset(with_template=False)
    c = _client()
    r = c.post("/api/dealers", json={
        "name": "Gamma", "base_url": "HTTPS://Gamma.CA/Used-Inventory/",
        "adapter_mode": "stub", "scrape_config": {"items": []}})
    assert r.status_code == 201, r.text
    assert r.json()["base_url"] == "https://gamma.ca/Used-Inventory"


# --- config reuse ------------------------------------------------------------
def test_same_provider_reuses_the_proven_config() -> None:
    _reset()
    c = _client()
    r = c.post("/api/dealers", json={
        "name": "Bravo Auto", "base_url": "https://bravo-auto.ca",
        "website_provider": "dealeron"})
    assert r.status_code == 201, r.text
    body = r.json()

    assert body["scraper_reused"] is True
    assert body["scraper_reused_from"] == "Alpha Motors"
    assert body["adapter_mode"] == "browser"
    assert body["scrape_config"]["extract"] == "jsonld"
    assert body["scrape_config"]["paginate"] == "url"


def test_reused_config_points_at_the_new_dealers_own_site() -> None:
    """The dangerous part. A verbatim copy would keep alpha-motors.ca URLs, so
    Bravo would scrape Alpha's inventory and store it under Bravo's name."""
    _reset()
    c = _client()
    body = c.post("/api/dealers", json={
        "name": "Bravo Auto", "base_url": "https://bravo-auto.ca",
        "website_provider": "dealeron"}).json()
    cfg = body["scrape_config"]

    for key in ("list_url", "base_url", "page_url"):
        assert "alpha-motors.ca" not in cfg[key], f"{key} still points at the template site"
        assert "bravo-auto.ca" in cfg[key], f"{key} was not retargeted: {cfg[key]}"

    # The platform-shaped parts are exactly what's worth reusing — keep them.
    assert cfg["list_url"].endswith("/inventory")
    assert cfg["page_url"].endswith("?page={page}")
    assert cfg["id_regex"] == TEMPLATE_CONFIG["id_regex"]


def test_explicit_config_is_never_overwritten_by_a_template() -> None:
    _reset()
    c = _client()
    mine = {"items": []}
    body = c.post("/api/dealers", json={
        "name": "Charlie", "base_url": "https://charlie.ca",
        "website_provider": "dealeron", "adapter_mode": "stub",
        "scrape_config": mine}).json()
    assert body["scraper_reused"] is False
    assert body["scrape_config"] == mine


def test_unknown_provider_gets_no_template() -> None:
    _reset()
    c = _client()
    r = c.post("/api/dealers", json={
        "name": "Delta", "base_url": "https://delta.ca",
        "website_provider": "some-platform-we-have-never-seen",
        "adapter_mode": "stub", "scrape_config": {"items": []}})
    assert r.status_code == 201, r.text
    assert r.json()["scraper_reused"] is False


def test_template_must_have_actually_scraped_something() -> None:
    """Copying a config that has never produced a vehicle would spread a broken
    template across every dealer on that platform."""
    _reset(with_template=False)
    with SessionLocal() as s:
        s.add(Dealer(name="Never Worked", base_url="https://never.ca",
                     website_provider="dealeron", adapter_mode="browser",
                     scrape_config=dict(TEMPLATE_CONFIG), inventory_count=0))
        s.commit()
    c = _client()
    r = c.post("/api/dealers", json={
        "name": "Echo", "base_url": "https://echo.ca",
        "website_provider": "dealeron", "adapter_mode": "stub",
        "scrape_config": {"items": []}})
    assert r.status_code == 201, r.text
    assert r.json()["scraper_reused"] is False


def test_reuse_satisfies_the_proven_site_gate() -> None:
    """A config proven on another dealer of the same platform is its own evidence
    the site is readable, so reuse must not also demand a discovery run."""
    _reset()
    c = _client()
    r = c.post("/api/dealers", json={
        "name": "Foxtrot", "base_url": "https://foxtrot.ca",
        "website_provider": "dealeron"})
    assert r.status_code == 201, r.text
    assert r.json()["scraper_reused"] is True


def test_no_reuse_still_requires_a_proven_site() -> None:
    """The 409 gate must stay intact for genuinely new platforms."""
    _reset(with_template=False)
    c = _client()
    r = c.post("/api/dealers", json={
        "name": "Golf", "base_url": "https://golf.ca", "adapter_mode": "browser",
        "scrape_config": dict(TEMPLATE_CONFIG, list_url="https://golf.ca/inventory",
                              base_url="https://golf.ca",
                              page_url="https://golf.ca/inventory?page={page}")})
    assert r.status_code == 409, r.text


# --- pagination --------------------------------------------------------------
def test_list_is_paged_and_reports_the_total() -> None:
    _reset(with_template=False)
    c = _client()
    for i in range(7):
        c.post("/api/dealers", json={
            "name": f"D{i:02d}", "base_url": f"https://d{i}.ca",
            "adapter_mode": "stub", "scrape_config": {"items": []}})

    all_rows = c.get("/api/dealers")
    assert len(all_rows.json()) == 7
    assert all_rows.headers["X-Total-Count"] == "7"

    page = c.get("/api/dealers", params={"limit": 3, "offset": 3})
    assert page.status_code == 200
    assert len(page.json()) == 3
    assert page.headers["X-Total-Count"] == "7", "total must be the match count, not the page size"
    assert [d["name"] for d in page.json()] == ["D03", "D04", "D05"]


def test_total_reflects_filters() -> None:
    _reset(with_template=False)
    c = _client()
    for i in range(4):
        c.post("/api/dealers", json={
            "name": f"Keep{i}", "base_url": f"https://k{i}.ca",
            "adapter_mode": "stub", "scrape_config": {"items": []}})
    c.post("/api/dealers", json={
        "name": "Other", "base_url": "https://other.ca",
        "adapter_mode": "stub", "scrape_config": {"items": []}})

    r = c.get("/api/dealers", params={"q": "Keep", "limit": 2})
    assert r.headers["X-Total-Count"] == "4"
    assert len(r.json()) == 2


def main() -> None:
    test_trailing_slash_and_case_do_not_create_a_second_dealer()
    test_normalisation_preserves_path_case()
    test_same_provider_reuses_the_proven_config()
    test_reused_config_points_at_the_new_dealers_own_site()
    test_explicit_config_is_never_overwritten_by_a_template()
    test_unknown_provider_gets_no_template()
    test_template_must_have_actually_scraped_something()
    test_reuse_satisfies_the_proven_site_gate()
    test_no_reuse_still_requires_a_proven_site()
    test_list_is_paged_and_reports_the_total()
    test_total_reflects_filters()
    print("PASSED")


if __name__ == "__main__":
    main()
