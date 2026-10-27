"""An empty page is usually a slow page, not the end of the listing.

`_iter_url` stops after two consecutive pages yield no new vehicles — the
correct way to detect the end of a listing whose length is unknown. But
tabangimotors.com paints its grid roughly 30 seconds after domcontentloaded
and sometimes misses the wait window entirely, so two unlucky pages in a row
ended a healthy run at 165 of 517 and reported it as a completed scrape of a
shrinking dealer.

Retrying a page that rendered nothing separates the two cases: a genuinely
past-the-end page stays empty however many times it is loaded, a slow one does
not. `empty_streak` still ends the run, only after the retries are spent.

Run:  python -m tests.test_url_page_retry
"""
from __future__ import annotations

from dealer_scraper.adapters.browser import BrowserAdapter

CFG = {
    "list_url": "https://x.test/cars?page=1",
    "page_url": "https://x.test/cars?page={page}",
    "base_url": "https://x.test",
    "card_anchor": "a[href*='/v/']",
    "id_regex": r"/v/(\d+)",
    "paginate": "url",
    "max_pages": 8,
    "settle_ms": 10,
    "nav_timeout": 1000,
}


class FakePage:
    """`pages` maps page number -> list of loads; each load is a list of ids.

    A load of [] simulates a page whose grid had not painted yet.
    """

    def __init__(self, pages: dict[int, list[list[str]]]) -> None:
        self.pages = pages
        self.loads: list[int] = []
        self._current = 1

    # -- navigation -------------------------------------------------------
    def goto(self, url, **_kw):
        self._current = int(url.rsplit("=", 1)[1])
        self.loads.append(self._current)

    def wait_for_selector(self, *_a, **_kw):
        return None

    def wait_for_timeout(self, *_a, **_kw):
        return None

    def eval_on_selector_all(self, *_a, **_kw):
        return len(self._payload(peek=True))

    def evaluate(self, *_a, **_kw):
        ids = self._payload()
        return [{"href": f"/v/{i}", "text": f"2024 Car {i} $10,000", "attrs": {}} for i in ids]

    # -- helpers ----------------------------------------------------------
    def _payload(self, peek: bool = False):
        loads = self.pages.get(self._current, [])
        if not loads:
            return []
        n = min(self.loads.count(self._current) - (0 if peek else 0), len(loads)) - 1
        n = max(0, min(n, len(loads) - 1))
        return loads[n]


def _adapter(**over) -> BrowserAdapter:
    return BrowserAdapter({**CFG, **over})


def _run(page, adapter) -> list[str]:
    seen: set[str] = set()
    return [v.external_id for v in adapter._iter_url(page, seen)]


def test_a_slow_page_is_retried_and_its_vehicles_kept() -> None:
    """Page 2 is empty on first load, populated on reload. Before the retry
    this lost page 2 AND started an empty streak."""
    page = FakePage({1: [["1", "2"]], 2: [[], ["3", "4"]], 3: [["5"]], 4: [[]], 5: [[]]})
    got = _run(page, _adapter(page_retries=2))
    assert "3" in got and "4" in got, got
    assert got.count("3") == 1


def test_run_no_longer_truncates_at_the_first_slow_page() -> None:
    page = FakePage({1: [["1"]], 2: [[], ["2"]], 3: [[], ["3"]], 4: [[]], 5: [[]]})
    got = _run(page, _adapter(page_retries=2))
    assert set(got) == {"1", "2", "3"}, got


def test_the_real_end_of_the_listing_still_ends_the_run() -> None:
    """Retries must not turn a finished listing into max_pages of reloads."""
    page = FakePage({1: [["1"]], 2: [["2"]], 3: [[], []], 4: [[], []], 5: [[], []]})
    got = _run(page, _adapter(page_retries=2))
    assert set(got) == {"1", "2"}
    assert max(page.loads) <= 4, f"kept loading past the end: {page.loads}"


def test_a_page_is_not_reloaded_when_it_worked_first_time() -> None:
    page = FakePage({1: [["1"]], 2: [["2"]], 3: [[]], 4: [[]]})
    _run(page, _adapter(page_retries=3))
    assert page.loads.count(2) == 1, "a healthy page must cost exactly one load"


def test_retries_are_configurable_and_default_on() -> None:
    assert _adapter().page_retries == 2
    assert _adapter(page_retries=5).page_retries == 5
    assert _adapter(page_retries=0).page_retries == 1, "must never be zero"


def main() -> None:
    test_a_slow_page_is_retried_and_its_vehicles_kept()
    test_run_no_longer_truncates_at_the_first_slow_page()
    test_the_real_end_of_the_listing_still_ends_the_run()
    test_a_page_is_not_reloaded_when_it_worked_first_time()
    test_retries_are_configurable_and_default_on()
    print("URL PAGE RETRY TESTS PASSED — a slow page no longer ends the run")


if __name__ == "__main__":
    main()
