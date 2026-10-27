"""A count that stopped moving is not a finished grid.

`wait_count_stable` returns as soon as a selector count holds for ~400ms. On a
grid that paints progressively that fires mid-render, so the adapter scrapes
whatever happens to be on screen. tabangimotors.com serves 50 cards per page
and a full run collected 74 of 517 — each page measured while still drawing.

When the listing URL fixes the page size (`pc=50`), the page size is known, so
the adapter can wait for it instead of guessing from timing. Falling short is
logged rather than raised: the last page legitimately holds fewer.

Run:  python -m tests.test_expected_page_size
"""
from __future__ import annotations

from dealer_scraper.adapters.browser import BrowserAdapter

BASE = {
    "list_url": "https://x.test/vehicles/used/?pg=1&pc=50",
    "base_url": "https://x.test",
    "card_anchor": "a[href*='/vehicles/20']",
    "card_selector": ".vehicle-card",
    "id_regex": r"/vehicles/\d{4}/.+/(\d{6,})",
}


class FakePage:
    """Counts grow by `step` on each measurement, the way a grid paints in."""

    def __init__(self, counts: list[int]) -> None:
        self.counts = list(counts)
        self.selectors_seen: list[str] = []
        self.waits = 0

    def eval_on_selector_all(self, selector: str, _script: str) -> int:
        self.selectors_seen.append(selector)
        return self.counts.pop(0) if self.counts else 0

    def wait_for_timeout(self, _ms: int) -> None:
        self.waits += 1


def _adapter(**over) -> BrowserAdapter:
    return BrowserAdapter({**BASE, **over})


def test_waits_until_the_expected_card_count_is_present() -> None:
    a = _adapter(expect_per_page=50, expect_wait_ms=5000)
    page = FakePage([10, 28, 50])
    a._wait_for_expected(page, "Page 2")
    assert page.waits == 2, "should have polled through the two short reads"


def test_returns_immediately_when_the_page_is_already_full() -> None:
    a = _adapter(expect_per_page=50, expect_wait_ms=5000)
    page = FakePage([50])
    a._wait_for_expected(page, "Page 2")
    assert page.waits == 0


def test_measures_the_card_container_not_the_anchor() -> None:
    """Anchors are ~3 per card, so counting them against a page size of 50
    would be satisfied by 17 rendered cards."""
    a = _adapter(expect_per_page=50, expect_wait_ms=2000)
    page = FakePage([50])
    a._wait_for_expected(page, "Page 2")
    assert page.selectors_seen == [".vehicle-card"]


def test_falls_back_to_the_anchor_when_no_card_selector() -> None:
    cfg = {k: v for k, v in BASE.items() if k != "card_selector"}
    a = BrowserAdapter({**cfg, "expect_per_page": 20, "expect_wait_ms": 2000})
    page = FakePage([20])
    a._wait_for_expected(page, "Page 1")
    assert page.selectors_seen == ["a[href*='/vehicles/20']"]


def test_a_short_final_page_is_not_an_error() -> None:
    """Page 11 of 517-at-50-per-page holds 17. It must return, not raise."""
    a = _adapter(expect_per_page=50, expect_wait_ms=600)
    page = FakePage([17, 17, 17, 17, 17, 17, 17, 17, 17, 17])
    a._wait_for_expected(page, "Page 11")   # must simply return


def test_disabled_by_default_so_existing_dealers_are_untouched() -> None:
    a = _adapter()
    page = FakePage([1])
    a._wait_for_expected(page, "Page 1")
    assert page.selectors_seen == [], "no expect_per_page => no extra waiting"


def test_a_page_that_never_renders_still_returns() -> None:
    a = _adapter(expect_per_page=50, expect_wait_ms=600)
    page = FakePage([0])
    a._wait_for_expected(page, "Page 4")    # must not hang or raise


def main() -> None:
    test_waits_until_the_expected_card_count_is_present()
    test_returns_immediately_when_the_page_is_already_full()
    test_measures_the_card_container_not_the_anchor()
    test_falls_back_to_the_anchor_when_no_card_selector()
    test_a_short_final_page_is_not_an_error()
    test_disabled_by_default_so_existing_dealers_are_untouched()
    test_a_page_that_never_renders_still_returns()
    print("EXPECTED PAGE SIZE TESTS PASSED — pages are no longer measured mid-render")


if __name__ == "__main__":
    main()
