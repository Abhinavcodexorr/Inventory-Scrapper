"""Discovery must PROVE pagination and structured data, not infer them.

Both gaps were observed live on clutch.ca, and both came from probing a
client-rendered listing too early / too indirectly:

  * pagination -> "scroll".  Detection only looked for `<a href="?page=2">`
    anchors. A React listing renders its pager as buttons, so no anchors exist —
    yet ?page=2 works perfectly. The scraper collected page 1 (32 of ~3,100 cars)
    and stopped, reporting Incomplete.
  * extract -> "dom".  The JSON-LD count was taken at first paint, before the
    SPA injected its 32 schema.org Vehicle blocks, so clean structured data was
    ignored in favour of guessing fields from card text.

Run:  python -m tests.test_pagination_verification
"""
from __future__ import annotations

from dealer_scraper.discovery.strategies.pagination import (
    count_jsonld_vehicles,
    verify_url_pagination,
)

PAGE_1 = {f"/vehicles/{i}" for i in range(1, 33)}
PAGE_2 = {f"/vehicles/{i}" for i in range(33, 65)}
SEGMENTS = ["vehicles"]


class _FakePage:
    """A listing whose page-2 behaviour we control.

    mode:
      paginates -> page 2 serves different vehicles (clutch.ca)
      ignores   -> page 2 serves page 1 again (parameter ignored)
      empty     -> page 2 renders nothing
      dead      -> navigation itself fails
    """

    def __init__(self, mode: str, jsonld: int = 32):
        self.mode = mode
        self.jsonld = jsonld
        self.url: str | None = None

    def goto(self, url: str, **_kw) -> None:
        if self.mode == "dead":
            raise RuntimeError("net::ERR_ABORTED")
        self.url = url

    def evaluate(self, js: str, _arg=None):
        if "application/ld+json" in js:
            return self.jsonld
        on_page_2 = bool(self.url and "page=2" in self.url)
        if not on_page_2:
            return list(PAGE_1)
        if self.mode == "paginates":
            return list(PAGE_2)
        if self.mode == "ignores":
            return list(PAGE_1)
        return []


def test_spa_without_page_anchors_is_still_proven_by_fetching_page_2() -> None:
    """The clutch.ca case: no anchors to infer from, but ?page=N genuinely works."""
    result = verify_url_pagination(
        _FakePage("paginates"), "https://clutch.ca/cars", PAGE_1, 5000, SEGMENTS)
    assert result["verified"] is True, result
    assert result["page_url"] == "https://clutch.ca/cars?page={page}"
    assert "not present on page 1" in result["reason"]


def test_site_that_ignores_the_page_param_is_not_verified() -> None:
    """Identical content on page 2 must never count as working pagination —
    that would loop over page 1 forever and report false totals."""
    result = verify_url_pagination(
        _FakePage("ignores"), "https://x.ca/inventory", PAGE_1, 5000, SEGMENTS)
    assert result["verified"] is False
    assert "same vehicles" in result["reason"]


def test_empty_page_2_is_not_verified() -> None:
    result = verify_url_pagination(
        _FakePage("empty"), "https://x.ca/inventory", PAGE_1, 1000, SEGMENTS)
    assert result["verified"] is False
    assert "no vehicles" in result["reason"]


def test_failed_navigation_is_reported_not_raised() -> None:
    """A dead page 2 must degrade to 'unverified', never break discovery."""
    result = verify_url_pagination(
        _FakePage("dead"), "https://x.ca/inventory", PAGE_1, 1000, SEGMENTS)
    assert result["verified"] is False
    assert "did not load" in result["reason"]


def test_page_url_template_respects_an_existing_query_string() -> None:
    result = verify_url_pagination(
        _FakePage("paginates"), "https://x.ca/inv?sort=new", PAGE_1, 5000, SEGMENTS)
    assert result["page_url"] == "https://x.ca/inv?sort=new&page={page}"


def test_jsonld_is_counted_from_the_rendered_page() -> None:
    assert count_jsonld_vehicles(_FakePage("paginates", jsonld=32)) == 32
    assert count_jsonld_vehicles(_FakePage("paginates", jsonld=0)) == 0


def main() -> None:
    test_spa_without_page_anchors_is_still_proven_by_fetching_page_2()
    test_site_that_ignores_the_page_param_is_not_verified()
    test_empty_page_2_is_not_verified()
    test_failed_navigation_is_reported_not_raised()
    test_page_url_template_respects_an_existing_query_string()
    test_jsonld_is_counted_from_the_rendered_page()
    print("PAGINATION VERIFICATION TESTS PASSED — ?page=2 proven empirically, "
          "JSON-LD counted after render")


if __name__ == "__main__":
    main()
