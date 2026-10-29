"""HillzDealer's newer template: Detect settings must emit the `detail` block.

formula7motors.ca and motorpalacecanada.ca render /cars on the server, so the
page never fetches its data in the browser. Discovery fell back to the grid and
synced 10 of 541. The probe reads the page data directly and derives the
detail fan-out from the `fullIds` index.

Run:  python -m tests.test_hillz_discovery
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import httpx

from dealer_scraper.discovery.heuristics import DEFAULT_HEURISTICS
from dealer_scraper.discovery.net.scorer import _best_vehicle_json
from dealer_scraper.discovery.platforms import hillz

FIXTURES = Path(__file__).parent / "fixtures" / "hillz"
F7 = json.loads((FIXTURES / "formula7_cars.json").read_text())
URL = "https://formula7motors.ca/_next/data/ABC123/cars.json"
HTML = ('<link href="https://hillzcdn.ca/x.css"><script id="__NEXT_DATA__" '
        'type="application/json">{"buildId":"ABC123","props":{}}</script>')


def _hit(data: dict) -> dict:
    return {"url": URL, "json": data, "headers": {}, "method": "GET", "post_body": None}


def test_probe_reads_cars_page_data_with_the_build_id(monkeypatch) -> None:
    monkeypatch.setattr(hillz, "validate_public_url", lambda url: url)
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json=F7)

    hits = hillz.probe_feed("https://formula7motors.ca", HTML,
                            client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert seen == [URL] and hits[0]["url"] == URL


def test_probe_follows_the_search_links_to_a_non_cars_listing(monkeypatch) -> None:
    """llautoinc.com lists at /used-cars and 404s on /cars; discovery fell back
    to a browser config on the homepage, which never shows a vehicle card."""
    monkeypatch.setattr(hillz, "validate_public_url", lambda url: url)
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path.endswith("/used-cars.json"):
            return httpx.Response(200, json=F7)
        return httpx.Response(404, text="Not found")

    html = HTML + '<a href="/used-cars?bodystyle=Suv">SUV</a><a href="/used-cars?make=">All</a>'
    hits = hillz.probe_feed("https://llautoinc.com/", html,
                            client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert seen == ["/_next/data/ABC123/used-cars.json"]
    assert hits[0]["url"] == "https://llautoinc.com/_next/data/ABC123/used-cars.json"
    assert hillz.listing_page(hits[0]["url"]) == "https://llautoinc.com/used-cars"


def test_probe_falls_back_to_cars_when_the_linked_page_has_no_data(monkeypatch) -> None:
    monkeypatch.setattr(hillz, "validate_public_url", lambda url: url)
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(200, json=F7) if request.url.path.endswith("/cars.json") else httpx.Response(404)

    hits = hillz.probe_feed("https://formula7motors.ca", HTML + '<a href="/inventory?make=Ford">',
                            client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert seen == ["/_next/data/ABC123/inventory.json", "/_next/data/ABC123/cars.json"]
    assert hits[0]["url"] == URL


def test_non_hillz_or_no_build_id_is_left_alone() -> None:
    assert not hillz.is_hillz_site("<html>zopsoftware</html>")
    assert hillz.probe_feed("https://x.example", "<html>hillzcdn.ca</html>") == []


def test_newer_template_gets_a_detail_block() -> None:
    best = _best_vehicle_json([_hit(F7)], DEFAULT_HEURISTICS)
    assert hillz.detail_block(best["root"], best["list_path"], URL) == {
        "index_path": "pageProps.vehiclesData.0.fullIds", "key": "id",
        "url": "https://formula7motors.ca/_next/data/{build_id}{slug}.json",
        "item_path": "pageProps.data",
    }


def test_older_template_needs_no_detail_block() -> None:
    """cccmotors.ca: the whole inventory is in preFetchedData already."""
    data = json.loads((FIXTURES / "22g_cars.json").read_text())
    best = _best_vehicle_json([_hit(data)], DEFAULT_HEURISTICS)
    assert hillz.detail_block(best["root"], best["list_path"], URL) is None


def test_featured_widget_does_not_beat_the_indexed_main_list() -> None:
    """motorpalacecanada.ca also carries a 37-vehicle widget at vehiclesData2.data."""
    data = copy.deepcopy(F7)
    listed = data["pageProps"]["vehiclesData"]
    widget = [{"id": i, "stock_NO": f"W{i}", "sell_price": 1000 + i, "odometer": 5,
               "Vehicle": {"make": "Kia", "model": "Niro", "model_year": 2019}} for i in range(37)]
    data["pageProps"]["vehiclesData2"] = {"data": widget}
    best = {"url": URL, "root": data, "array": widget, "list_path": "pageProps.vehiclesData2.data"}
    picked = hillz.prefer_listing(best)
    assert picked["list_path"] == "pageProps.vehiclesData" and picked["array"] == listed


def main() -> None:
    """`python -m tests.test_hillz_discovery` — the probe test takes
    monkeypatch, so the whole file runs through pytest."""
    import sys

    import pytest as _pytest

    tests = (
        test_probe_reads_cars_page_data_with_the_build_id,
        test_probe_follows_the_search_links_to_a_non_cars_listing,
        test_probe_falls_back_to_cars_when_the_linked_page_has_no_data,
        test_non_hillz_or_no_build_id_is_left_alone,
        test_newer_template_gets_a_detail_block,
        test_older_template_needs_no_detail_block,
        test_featured_widget_does_not_beat_the_indexed_main_list,
    )
    sys.exit(_pytest.main([__file__, "-q", "-p", "no:cacheprovider",
                           "-k", " or ".join(t.__name__ for t in tests)]))


if __name__ == "__main__":
    main()
