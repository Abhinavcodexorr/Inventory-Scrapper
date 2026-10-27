"""Walking off the end of a listing is success, not failure.

`max_pages` is a safety cap, deliberately set above the real page count — so
every healthy run requests at least one page that does not exist. Sites answer
that in three ways:

  * an empty grid                  -> handled by the empty-page streak
  * a 404 / 5xx (goto raises)      -> handled here
  * a redirect back to page 1      -> handled by the streak (no new vehicles)

The second case discarded a complete scrape in production: clutch.ca returned
an HTTP error for page 105 of a ~96-page listing, the exception propagated out
of the adapter, and a nightly run that had already collected 3,263 vehicles was
recorded as `failed` with nothing saved. The click-based paginator already
treated a dead "load more" control as the end of the list; the URL paginator
did not.

The distinction that matters: a navigation failure with vehicles already
collected is the end of the list. One with nothing collected is a real failure
and must stay loud, or a site that breaks on page 1 would silently look like an
empty dealership.

Run:  python -m tests.test_pagination_end_of_list
"""
from __future__ import annotations

from dealer_scraper.adapters.registry import build_adapter

CONFIG = {
    "list_url": "https://example.test/cars",
    "base_url": "https://example.test",
    "page_url": "https://example.test/cars?page={page}",
    "card_anchor": "a[href*='/vehicles/']",
    "id_regex": r"/vehicles/(\d+)",
    "paginate": "url",
    "max_pages": 10,          # deliberately above the 3 pages that exist
    "settle_ms": 1,
    "nav_timeout": 100,
}


class _FakePage:
    """A listing with `real_pages` pages; anything beyond raises, as a 404 does.

    Note `_iter_url` does NOT navigate for page 1 — the caller has already
    loaded it — so the "nothing collected" path is reached by page 1 rendering
    empty and the page-2 navigation then failing.
    """

    def __init__(self, real_pages: int):
        self.real_pages = real_pages
        self.page = 1

    def goto(self, url, **_kw):
        n = int(url.rsplit("page=", 1)[1])
        if n > self.real_pages:
            raise RuntimeError("net::ERR_HTTP_RESPONSE_CODE_FAILURE")
        self.page = n

    def wait_for_selector(self, *_a, **_k):
        return None

    def evaluate(self, js, arg=None):
        # Collect: 2 vehicles per page, unique per page. No real pages means
        # even page 1 (already loaded by the caller) renders empty.
        if "querySelectorAll" in js and isinstance(arg, dict) and "anchorSel" in arg:
            if self.real_pages == 0:
                return []
            base = (self.page - 1) * 2
            return [{"href": f"/vehicles/{base + i}", "text": f"2020 Honda Civic $2{base + i},000"}
                    for i in (1, 2)]
        return 2


def _collect(page) -> list:
    adapter = build_adapter("browser", CONFIG)
    return list(adapter._iter_url(page, set()))


def test_error_past_the_end_is_the_end_not_a_failure() -> None:
    """Three real pages, cap of ten: page 4 raises and the run keeps its cars."""
    vehicles = _collect(_FakePage(real_pages=3))
    assert len(vehicles) == 6, f"expected 3 pages x 2 vehicles, got {len(vehicles)}"


def test_navigation_failure_with_nothing_collected_still_raises() -> None:
    """A site broken from the start must fail loudly.

    Swallowing this would turn "the listing is broken" into "this dealership
    has no cars" — the exact silent-zero failure the engine exists to prevent.
    """
    try:
        _collect(_FakePage(real_pages=0))
    except RuntimeError:
        return
    raise AssertionError("a navigation failure with no vehicles must not be swallowed")


def test_single_page_listing_completes() -> None:
    assert len(_collect(_FakePage(real_pages=1))) == 2


def test_cap_is_respected_when_pages_never_run_out() -> None:
    """A site that answers every page number must still stop at max_pages
    rather than loop to the cap's limit forever."""
    vehicles = _collect(_FakePage(real_pages=99))
    assert len(vehicles) == CONFIG["max_pages"] * 2


def main() -> None:
    test_error_past_the_end_is_the_end_not_a_failure()
    test_navigation_failure_with_nothing_collected_still_raises()
    test_single_page_listing_completes()
    test_cap_is_respected_when_pages_never_run_out()
    print("END-OF-LIST TESTS PASSED — a 404 past the last page completes the run, "
          "a first-page failure still fails it")


if __name__ == "__main__":
    main()
