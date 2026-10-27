"""An endpoint that cannot paginate must not be paginated.

cccmotors.ca was onboarded with

    endpoint:  https://cccmotors.ca/_next/data/<buildId>/cars.json
    list_path: pageProps.preFetchedData.vehiclesData

and no {page} placeholder. `.format(page=k)` therefore returned the identical
URL every iteration, the same 199-vehicle array came back forever, and the
"stop when a page is empty" condition never fired. One run emitted 1,072,212
vehicles across 90 minutes before the task soft-limit killed it — holding the
only worker slot and billing residential proxy traffic throughout. Two earlier
runs did the same (735,504 and 1,027,768).

The existing `len(first_items) < page_size` guard did not catch it: 199 items
against a default page_size of 25 looks exactly like "there are more pages".

Run:  python -m tests.test_json_api_single_page
"""
from __future__ import annotations

from dealer_scraper.adapters.json_api import JsonApiAdapter

FIELD_MAP = {"external_id": "stock_no", "make": "make"}


def _payload(n: int) -> dict:
    return {"vehiclesData": [{"stock_no": f"S{i}", "make": "Audi"} for i in range(n)]}


class Recorder(JsonApiAdapter):
    """Counts fetches instead of performing them."""

    def __init__(self, cfg: dict, items: int) -> None:
        super().__init__(cfg)
        self.items = items
        self.fetches: list[str] = []

    def _get(self, endpoint: str, page: int):
        self.fetches.append(endpoint.format(page=page, page_size=self.page_size))
        return _payload(self.items)


BASE = {
    "list_path": "vehiclesData",
    "field_map": FIELD_MAP,
    "page_size": 25,
    "delay_seconds": 0,
}


def test_an_endpoint_without_a_page_placeholder_is_fetched_once() -> None:
    """The CCC Motors case: 199 items, page_size 25, static URL."""
    a = Recorder({**BASE, "endpoint": "https://x.test/_next/data/abc/cars.json"}, items=199)
    got = list(a.iter_vehicles())
    assert len(a.fetches) == 1, f"expected a single fetch, made {len(a.fetches)}"
    assert len(got) == 199


def test_a_paginating_endpoint_still_pages() -> None:
    """The guard must not disable pagination for correctly-configured feeds."""
    a = Recorder({**BASE, "endpoint": "https://x.test/api?page={page}",
                  "max_pages": 3}, items=25)
    list(a.iter_vehicles())
    assert len(a.fetches) > 1, "an endpoint with {page} must keep paginating"
    assert a.fetches[0] != a.fetches[1], "each page must request a different URL"


def test_a_short_first_page_still_short_circuits() -> None:
    a = Recorder({**BASE, "endpoint": "https://x.test/api?page={page}"}, items=5)
    got = list(a.iter_vehicles())
    assert len(a.fetches) == 1 and len(got) == 5


def test_an_empty_feed_yields_nothing() -> None:
    a = Recorder({**BASE, "endpoint": "https://x.test/_next/data/abc/cars.json"}, items=0)
    assert list(a.iter_vehicles()) == []
    assert len(a.fetches) == 1


def main() -> None:
    test_an_endpoint_without_a_page_placeholder_is_fetched_once()
    test_a_paginating_endpoint_still_pages()
    test_a_short_first_page_still_short_circuits()
    test_an_empty_feed_yields_nothing()
    print("SINGLE PAGE TESTS PASSED — a static endpoint is fetched once, not forever")


if __name__ == "__main__":
    main()
