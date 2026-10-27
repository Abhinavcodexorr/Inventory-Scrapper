"""Two rules this project relies on, both learned from real failures:

  1. If a scrape isn't working, we must know WHY. A run that extracts nothing
     reports `incomplete` with an explanation — never "completed / success".
  2. A website the engine cannot read must not be onboarded in the first place,
     or it becomes a dealership permanently stuck at zero inventory.

Both were observed live:
  * grcars.ca — the listing renders client-side (Algolia). The readiness wait was
    satisfied by the site's own "Inventory" NAV links, which match card_anchor but
    not id_regex, so the scraper collected before any vehicle existed, extracted 0,
    and reported Success.
  * The same dealer was allowed to be added even though discovery had already
    verified 0 vehicles for it.

Run:  DATABASE_URL="sqlite:///./.silent_test.db" python -m tests.test_never_silent_failure
"""
from __future__ import annotations

import os
import re
import time
from datetime import datetime, timezone

from dealer_scraper.adapters.stub import StubAdapter
from dealer_scraper.adapters.waits import count_matching, wait_count_stable, wait_for_vehicles
from dealer_scraper.db import SessionLocal, engine
from dealer_scraper.engine import run_dealer
from dealer_scraper.models import Base, Dealer, DiscoveryRun

from . import require_disposable_db

# --- the grcars.ca DOM shape -------------------------------------------------
NAV_LINKS = ["/inventory/", "/inventory/?refinementList%5Bmake%5D%5B0%5D=Audi"]
VEHICLE_LINKS = [f"/inventory/2023-some-car-{i}/{2500 + i}/" for i in range(50)]
ID_REGEX = r"/inventory/[\w-]+/(\d+)"


class _FakePage:
    """Nav links from t=0 (server-rendered); vehicles only after `delay`
    (client-side render), which is exactly what defeated the old wait."""

    def __init__(self, delay_seconds: float):
        self._t0 = time.monotonic()
        self._delay = delay_seconds

    def _hrefs(self) -> list[str]:
        rendered = time.monotonic() - self._t0 >= self._delay
        return NAV_LINKS + (VEHICLE_LINKS if rendered else [])

    def evaluate(self, _js, arg=None):
        if isinstance(arg, dict) and "pattern" in arg:
            pattern = re.compile(arg["pattern"])
            return sum(1 for h in self._hrefs() if pattern.search(h))
        return len(self._hrefs())


def test_nav_links_alone_must_not_count_as_a_rendered_grid() -> None:
    """count_matching ignores nav links because they can't match a detail pattern."""
    page = _FakePage(delay_seconds=99)          # vehicles never render
    assert count_matching(page, "a", ID_REGEX) == 0
    # ...while a bare selector count is happily non-zero — the original bug.
    assert page.evaluate("count", None) == len(NAV_LINKS)


def test_old_stability_wait_settles_on_nav_links() -> None:
    """Regression witness: this is what shipped, and why grcars returned zero."""
    settled = wait_count_stable(_FakePage(delay_seconds=1.0), "a", cap_ms=5000)
    assert settled == len(NAV_LINKS), settled


def test_wait_for_vehicles_waits_for_the_real_grid() -> None:
    found = wait_for_vehicles(_FakePage(delay_seconds=1.0), "a", ID_REGEX, cap_ms=8000)
    assert found == len(VEHICLE_LINKS), found


def test_wait_for_vehicles_returns_zero_when_nothing_ever_renders() -> None:
    """Zero must be reported so the caller can raise a diagnostic, not proceed."""
    found = wait_for_vehicles(_FakePage(delay_seconds=99), "a", ID_REGEX, cap_ms=1000)
    assert found == 0


# --- rule 1: a zero-vehicle run is never a success ---------------------------
def _fresh_dealer() -> int:
    require_disposable_db()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with SessionLocal() as s:
        d = Dealer(name="D", base_url="https://d.example",
                   adapter_mode="stub", scrape_config={})
        s.add(d)
        s.commit()
        return d.id


def test_first_sync_scraping_nothing_is_incomplete_not_success() -> None:
    dealer_id = _fresh_dealer()
    with SessionLocal() as s:
        run = run_dealer(s, s.get(Dealer, dealer_id), adapter=StubAdapter([]))
    assert run.status == "incomplete", run.status
    assert run.scraped_count == 0
    assert run.error, "a zero-vehicle run must explain itself"
    assert "0 vehicles" in run.error


def test_zero_scrape_never_marks_existing_inventory_removed() -> None:
    """The safety gate still holds: absence proves nothing when we saw nothing."""
    dealer_id = _fresh_dealer()
    stock = [{"external_id": "S1", "make": "Honda", "model": "Civic",
              "year": 2020, "price": 20000, "url": "https://d.example/1"}]
    with SessionLocal() as s:
        run_dealer(s, s.get(Dealer, dealer_id), adapter=StubAdapter(stock))
    with SessionLocal() as s:
        run = run_dealer(s, s.get(Dealer, dealer_id), adapter=StubAdapter([]))
    assert run.status == "incomplete"
    assert run.removed_count == 0, "a broken scrape must not wipe inventory"


def test_vehicles_with_no_details_are_incomplete_not_success() -> None:
    """The cardora.ca shape: links found, cards unreadable, every field NULL.

    A wrong card_selector still yields one row per vehicle — the anchors match,
    so scraped_count looks perfectly healthy — while year/make/model/price are
    all empty. 528 vehicles were reported as a successful sync this way. Row
    count cannot detect it; the share of rows that actually describe a vehicle
    can.
    """
    dealer_id = _fresh_dealer()
    empty = [{"external_id": f"S{i}", "url": f"https://d.example/inventory/{i}"}
             for i in range(40)]
    with SessionLocal() as s:
        run = run_dealer(s, s.get(Dealer, dealer_id), adapter=StubAdapter(empty))

    assert run.status == "incomplete", "40 empty records is not a successful sync"
    assert run.scraped_count == 40, "the rows were still collected and stored"
    assert run.error and "card_selector" in run.error, (
        f"the reason must point at the likely cause: {run.error}")


def test_a_few_sparse_listings_do_not_fail_the_run() -> None:
    """Real inventories carry the odd bare listing; the gate exists to catch a
    broken extractor, not to police sparse cards."""
    dealer_id = _fresh_dealer()
    items = [{"external_id": f"S{i}", "url": f"https://d.example/{i}",
              "make": "Honda", "model": "Civic", "year": 2020, "price": 20000}
             for i in range(36)]
    items += [{"external_id": f"B{i}", "url": f"https://d.example/b{i}"} for i in range(4)]
    with SessionLocal() as s:
        run = run_dealer(s, s.get(Dealer, dealer_id), adapter=StubAdapter(items))

    assert run.status == "completed", run.error
    assert run.scraped_count == 40


class _NoTotalStub(StubAdapter):
    """A listing that publishes no "N results" count — very common, and the
    case where the removal gate had nothing to check against."""

    def fetch_total_count(self):
        return None


def test_partial_scrape_without_a_reported_total_must_not_wipe_inventory() -> None:
    """The clutch.ca failure shape, at the point where it would destroy data.

    A config that silently degrades to page 1 returns a small slice of a large
    lot. The site publishes no total, so `reported_total` is None — which used
    to short-circuit the safety gate straight to "reconciled" and mark every
    unseen vehicle removed (then sold, SOLD_AFTER_DAYS later). With no total
    from the site, the dealer's own prior inventory is the expectation.
    """
    dealer_id = _fresh_dealer()
    full_lot = [{"external_id": f"S{i}", "make": "Honda", "model": "Civic",
                 "year": 2020, "price": 20000, "url": f"https://d.example/{i}"}
                for i in range(100)]
    with SessionLocal() as s:
        first = run_dealer(s, s.get(Dealer, dealer_id), adapter=_NoTotalStub(full_lot))
    assert first.scraped_count == 100

    # Next run reaches only the first "page" — 3 of 100.
    with SessionLocal() as s:
        run = run_dealer(s, s.get(Dealer, dealer_id), adapter=_NoTotalStub(full_lot[:3]))

    assert run.status == "incomplete", (
        "a run that sees 3 of 100 held vehicles cannot be a success")
    assert run.removed_count == 0, "a partial scrape must not wipe inventory"
    assert run.error, "an incomplete run must explain itself"
    assert "100" in run.error, f"the expectation should be stated: {run.error}"


def test_genuine_shrinkage_is_still_reconciled() -> None:
    """The guard must not freeze a dealer that really did sell down. A normal
    day's turnover stays above reconcile_min_fraction and reconciles."""
    dealer_id = _fresh_dealer()
    lot = [{"external_id": f"S{i}", "make": "Honda", "model": "Civic",
            "year": 2020, "price": 20000, "url": f"https://d.example/{i}"}
           for i in range(100)]
    with SessionLocal() as s:
        run_dealer(s, s.get(Dealer, dealer_id), adapter=_NoTotalStub(lot))
    with SessionLocal() as s:
        run = run_dealer(s, s.get(Dealer, dealer_id), adapter=_NoTotalStub(lot[:95]))

    assert run.status == "completed", run.error
    assert run.removed_count == 5


# --- rule 2: unreadable sites are refused at onboarding ----------------------
def _client():
    from fastapi.testclient import TestClient

    from api.main import app

    return TestClient(app, headers={"X-API-Key": os.environ.get("API_KEY", "test-key")})


def _seed_discovery(base_url: str, verified: int) -> None:
    require_disposable_db()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with SessionLocal() as s:
        s.add(DiscoveryRun(base_url=base_url, status="completed",
                           verdict="ready" if verified else "unsupported",
                           vehicles_verified=verified,
                           finished_at=datetime.now(timezone.utc)))
        s.commit()


def _add(url: str, **extra) -> "object":
    body = {"name": "T", "base_url": url, "adapter_mode": "browser",
            "scrape_config": {"list_url": f"{url}/inventory",
                              "card_anchor": "a", "id_regex": ID_REGEX},
            **extra}
    return _client().post("/api/dealers", json=body)


def test_site_with_zero_verified_vehicles_is_refused() -> None:
    _seed_discovery("https://www.grcars.ca", verified=0)
    resp = _add("https://www.grcars.ca")
    assert resp.status_code == 409, resp.text
    assert "could not extract any vehicles" in resp.json()["detail"]


def test_site_never_checked_is_refused() -> None:
    _seed_discovery("https://other.ca", verified=10)
    resp = _add("https://unchecked.ca")
    assert resp.status_code == 409, resp.text
    assert "Detect settings" in resp.json()["detail"]


def test_proven_site_is_accepted() -> None:
    _seed_discovery("https://clutch.ca", verified=25)
    assert _add("https://clutch.ca").status_code == 201


def test_force_allows_a_deliberate_override() -> None:
    _seed_discovery("https://www.grcars.ca", verified=0)
    assert _add("https://www.grcars.ca", force=True).status_code == 201


def main() -> None:
    test_nav_links_alone_must_not_count_as_a_rendered_grid()
    test_old_stability_wait_settles_on_nav_links()
    test_wait_for_vehicles_waits_for_the_real_grid()
    test_wait_for_vehicles_returns_zero_when_nothing_ever_renders()
    test_first_sync_scraping_nothing_is_incomplete_not_success()
    test_zero_scrape_never_marks_existing_inventory_removed()
    test_vehicles_with_no_details_are_incomplete_not_success()
    test_a_few_sparse_listings_do_not_fail_the_run()
    test_partial_scrape_without_a_reported_total_must_not_wipe_inventory()
    test_genuine_shrinkage_is_still_reconciled()
    test_site_with_zero_verified_vehicles_is_refused()
    test_site_never_checked_is_refused()
    test_proven_site_is_accepted()
    test_force_allows_a_deliberate_override()
    print("NEVER-SILENT-FAILURE TESTS PASSED — zero-vehicle runs explain themselves, "
          "unreadable sites are refused at onboarding")


if __name__ == "__main__":
    main()
