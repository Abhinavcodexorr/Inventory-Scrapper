"""Read the site's own JSON feed from inside the browser page.

tabangimotors.com renders its grid roughly 30 seconds after load and misses
that window on perhaps a third of loads. Reading the DOM got 332 of 495 —
pages 6, 11 and 12 came back blank three attempts each, and overlapping `pg`
results ate the difference.

Its ajax-vehicles.php answers HTTP 200 to the PAGE's own fetch and 403 to the
server, so the navigation the adapter already performs is what makes the feed
reachable. Paging that JSON needs no rendering, cannot lose a slow page, and
carries a real total for the removal gate.

Run:  python -m tests.test_page_api_mode
"""
from __future__ import annotations

import pytest

from dealer_scraper.adapters.browser import BrowserAdapter, _dig_path

CFG = {
    "list_url": "https://x.test/cars",
    "base_url": "https://x.test",
    "card_anchor": "a[href*='/v/']",
    "id_regex": r"/v/(\d+)",
    "api_url": "https://x.test/api?pg={page}&pc={page_size}",
    "api_page_size": 2,
    "api_list_path": "results",
    "api_total_path": "summary.total_vehicles",
    "api_field_map": {
        "external_id": "stock_no",
        "vin": "vin",
        "make": "make",
        "price": "price",
        "url": "vdp_url",
    },
}


def _car(n: int) -> dict:
    return {"stock_no": f"S{n}", "vin": f"VIN{n}", "make": "Audi",
            "price": "1000", "vdp_url": f"/vehicles/{n}/"}


class FakePage:
    """Serves `pages` (page number -> item list) to the in-page fetch."""

    def __init__(self, pages: dict[int, list[dict]], total: int = 5,
                 status: int = 200) -> None:
        self.pages = pages
        self.total = total
        self.status = status
        self.urls: list[str] = []

    def evaluate(self, _js, url):
        self.urls.append(url)
        pg = int(url.split("pg=")[1].split("&")[0])
        if self.status != 200:
            return {"status": self.status, "body": None, "raw": "denied"}
        return {"status": 200,
                "body": {"summary": {"total_vehicles": self.total},
                         "results": self.pages.get(pg, [])}}


def _run(page, adapter) -> list:
    seen: set[str] = set()
    return list(adapter._iter_page_api(page, seen))


def test_pages_until_a_short_page_ends_it() -> None:
    page = FakePage({1: [_car(1), _car(2)], 2: [_car(3), _car(4)], 3: [_car(5)]})
    got = _run(page, BrowserAdapter(CFG))
    assert [v.external_id for v in got] == ["S1", "S2", "S3", "S4", "S5"]
    assert len(page.urls) == 3, "a short page means the last page; stop there"


def test_the_total_becomes_the_removal_gate_denominator() -> None:
    """The DOM route never read a total, so a half-scraped run was compared
    against yesterday's inventory instead."""
    a = BrowserAdapter(CFG)
    _run(FakePage({1: [_car(1)]}, total=495), a)
    assert a.fetch_total_count() == 495


def test_duplicates_across_pages_are_collapsed() -> None:
    """Overlapping pg results are exactly what cost us vehicles on the DOM
    route — the same car on two pages must count once."""
    page = FakePage({1: [_car(1), _car(2)], 2: [_car(2), _car(3)]})
    got = _run(page, BrowserAdapter(CFG))
    assert [v.external_id for v in got] == ["S1", "S2", "S3"]


def test_fields_come_straight_from_the_feed() -> None:
    got = _run(FakePage({1: [_car(7)]}), BrowserAdapter(CFG))
    v = got[0]
    assert v.vin == "VIN7" and v.make == "Audi"
    assert v.url == "https://x.test/vehicles/7/", "root-relative url must be absolutised"


def test_a_block_on_the_first_page_is_loud() -> None:
    """Returning zero quietly is the failure mode this whole system exists to
    avoid — a 403 with nothing collected must raise."""
    with pytest.raises(RuntimeError, match="403"):
        _run(FakePage({1: [_car(1)]}, status=403), BrowserAdapter(CFG))


def test_a_block_mid_run_keeps_what_was_collected() -> None:
    """Losing page 4 of 10 should not discard pages 1-3."""
    class Flaky(FakePage):
        def evaluate(self, js, url):
            pg = int(url.split("pg=")[1].split("&")[0])
            if pg >= 3:
                return {"status": 429, "body": None, "raw": "slow down"}
            return super().evaluate(js, url)

    got = _run(Flaky({1: [_car(1), _car(2)], 2: [_car(3), _car(4)]}), BrowserAdapter(CFG))
    assert [v.external_id for v in got] == ["S1", "S2", "S3", "S4"]


def test_dig_path_walks_nested_payloads() -> None:
    assert _dig_path({"a": {"b": [{"c": 9}]}}, "a.b.0.c") == 9
    assert _dig_path({"a": 1}, "a.missing") is None
    assert _dig_path({"a": 1}, "") == {"a": 1}


def test_api_mode_is_opt_in() -> None:
    """Dealers without api_url must keep the DOM path untouched."""
    plain = BrowserAdapter({k: v for k, v in CFG.items() if not k.startswith("api_")})
    assert plain.api_url == ""


def main() -> None:
    test_pages_until_a_short_page_ends_it()
    test_the_total_becomes_the_removal_gate_denominator()
    test_duplicates_across_pages_are_collapsed()
    test_fields_come_straight_from_the_feed()
    try:
        test_a_block_on_the_first_page_is_loud()
    except Exception as exc:  # pytest.raises unavailable outside pytest runs
        raise AssertionError(exc)
    test_a_block_mid_run_keeps_what_was_collected()
    test_dig_path_walks_nested_payloads()
    test_api_mode_is_opt_in()
    print("PAGE API TESTS PASSED — the feed is read from inside the page, not the DOM")


if __name__ == "__main__":
    main()
