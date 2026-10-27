"""Zop dealers: finding the feed, mapping it, and reusing it safely.

newcastleauto.ca runs Zop's WordPress template, which never calls the dealer's
inventory feed from the page, so Detect settings reported "not supported" for a
platform already onboarded (cardora.ca). Shapes below are trimmed from the live
feeds captured 2026-10-26.

Run:  python -m tests.test_zop_platform
"""
from __future__ import annotations

import httpx

from api.routers.dealers import _retarget_config
from dealer_scraper.config_lint import find_identity_conflict
from dealer_scraper.discovery.net.fieldmap import _build_field_map
from dealer_scraper.discovery.platforms import zop

HTML = '<link href="https://zopsoftware-asset.b-cdn.net/x.css"><script>var zopdealer=1</script>'


def _vehicle(stock: str, host: str, status: str = "Instock") -> dict:
    return {"inventory_id": stock[-3:], "stock_no": stock, "vin": "JF2GTACC6KH324951",
            "year": "2019", "make": "Subaru", "model": "Crosstrek", "trim": "Touring",
            "price": "14800.00", "odometer": "129132", "status": status,
            "body_type": "SUV", "transmission": "CVT", "url": None,
            "page_url": f"https://{host}/inventory/2019-subaru-crosstrek-touring/{stock[-3:]}/"}


def _feed(host: str):
    def handler(request: httpx.Request) -> httpx.Response:
        if not request.url.host.endswith("zopsoftware.com"):
            return httpx.Response(404)
        page = int(request.url.params.get("page", 1))
        items = [_vehicle(f"N{page}{i:02d}", host, "Sold" if i % 3 else "Instock") for i in range(10)]
        return httpx.Response(200, json={"inventory": items, "pager": {"total": 777, "perPage": 10}})
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_feed_found_for_a_zop_site_and_shaped_as_captured_traffic(monkeypatch) -> None:
    monkeypatch.setattr(zop, "validate_public_url", lambda url: url)
    hits = zop.probe_feed("https://www.newcastleauto.ca", HTML, client=_feed("www.newcastleauto.ca"))
    assert [h["url"] for h in hits] == [
        "https://newcastleauto.zopsoftware.com/api/website/inventory?page=1",
        "https://newcastleauto.zopsoftware.com/api/website/inventory?page=2",
    ]
    assert hits[0]["method"] == "GET" and hits[0]["json"]["pager"]["total"] == 777


def test_a_feed_listing_another_dealers_cars_is_refused(monkeypatch) -> None:
    """The slug is a guess from the domain; a wrong guess must never hand this
    dealer someone else's inventory."""
    monkeypatch.setattr(zop, "validate_public_url", lambda url: url)
    assert zop.probe_feed("https://www.newcastleauto.ca", HTML, client=_feed("www.cardora.ca")) == []


def test_non_zop_sites_are_left_alone() -> None:
    assert not zop.is_zop_site("<html>Dealer Inspire</html>")
    assert zop.is_zop_site(HTML)


def test_slug_named_in_the_page_is_tried_first() -> None:
    html = HTML + '<script src="https://newcastle.zopsoftware.com/api/website/x.js">'
    assert zop.feed_slugs("https://www.newcastleauto.ca", html) == ["newcastle", "newcastleauto"]


def test_zop_field_map_includes_link_and_lifecycle() -> None:
    items = [_vehicle(f"8{i:03d}A", "www.newcastleauto.ca", "Sold" if i % 2 else "Instock") for i in range(6)]
    fm = _build_field_map(items)
    assert fm["external_id"] == "stock_no" and fm["mileage"] == "odometer"
    assert fm["url"] == "page_url"        # not the always-null "url" key
    assert fm["source_status"] == "status"


def test_a_numeric_status_code_is_not_mapped() -> None:
    """HillzDealer's vehicle_status is 4 for every car — not a lifecycle word."""
    items = [{"stock_NO": f"S{i}", "vin": "1FTFW1E50NFA00000", "make": "Ford", "model": "F-150",
              "year": 2022, "price": 40000, "status": 4} for i in range(4)]
    assert "source_status" not in _build_field_map(items)


ZOP_CARDORA = {
    "endpoint": "https://cardora.zopsoftware.com/api/website/inventory?page={page}",
    "list_path": "inventory", "total_path": "pager.total",
    "field_map": {"external_id": "stock_no", "source_status": "status", "url": "page_url"},
}


def test_reusing_a_zop_config_points_the_feed_at_the_new_dealer() -> None:
    out = _retarget_config(ZOP_CARDORA, "https://cardora.ca", "https://www.newcastleauto.ca")
    assert out["endpoint"] == "https://newcastleauto.zopsoftware.com/api/website/inventory?page={page}"
    assert out["field_map"] == ZOP_CARDORA["field_map"]


def test_reuse_moves_nested_and_build_id_urls_too() -> None:
    hillz = {"endpoint": "https://faraauto.com/_next/data/{build_id}/cars.json",
             "next_build_id_url": "https://faraauto.com/cars",
             "detail": {"url": "https://faraauto.com/_next/data/{build_id}{slug}.json"}}
    out = _retarget_config(hillz, "https://faraauto.com", "https://cccmotors.ca")
    assert out["next_build_id_url"] == "https://cccmotors.ca/cars"
    assert out["detail"]["url"] == "https://cccmotors.ca/_next/data/{build_id}{slug}.json"
    assert out["endpoint"] == "https://cccmotors.ca/_next/data/{build_id}/cars.json"


def test_two_dealers_on_the_same_zop_feed_are_refused() -> None:
    """A verbatim copy (feed still on cardora.zopsoftware.com) has only ?page= in
    its query, which the old identity check could not see."""
    assert find_identity_conflict(ZOP_CARDORA, [(1, "Cardora", ZOP_CARDORA)]) is not None
    newcastle = _retarget_config(ZOP_CARDORA, "https://cardora.ca", "https://www.newcastleauto.ca")
    assert find_identity_conflict(newcastle, [(1, "Cardora", ZOP_CARDORA)]) is None


def test_pages_alone_are_not_an_identity() -> None:
    """Two browser dealers on one platform share list_url shapes, not inventory."""
    a = {"list_url": "https://a.example/inventory", "base_url": "https://a.example"}
    assert find_identity_conflict(a, [(1, "A twin", dict(a))]) is None


def main() -> None:
    """`python -m tests.test_zop_platform` — the probe tests take monkeypatch,
    so the whole file runs through pytest."""
    import sys

    import pytest as _pytest

    tests = (
        test_feed_found_for_a_zop_site_and_shaped_as_captured_traffic,
        test_a_feed_listing_another_dealers_cars_is_refused,
        test_non_zop_sites_are_left_alone,
        test_slug_named_in_the_page_is_tried_first,
        test_zop_field_map_includes_link_and_lifecycle,
        test_a_numeric_status_code_is_not_mapped,
        test_reusing_a_zop_config_points_the_feed_at_the_new_dealer,
        test_reuse_moves_nested_and_build_id_urls_too,
        test_two_dealers_on_the_same_zop_feed_are_refused,
        test_pages_alone_are_not_an_identity,
    )
    sys.exit(_pytest.main([__file__, "-q", "-p", "no:cacheprovider",
                           "-k", " or ".join(t.__name__ for t in tests)]))


if __name__ == "__main__":
    main()
