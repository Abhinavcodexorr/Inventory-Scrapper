"""Two HillzDealer sites, two different data sources.

22G Auto Sales (older template, getStaticProps) serves its WHOLE inventory in
/_next/data/<buildId>/cars.json at pageProps.preFetchedData.vehiclesData.

Formula 7 Motors (newer template, getServerSideProps) serves only the first 10
vehicles at pageProps.vehiclesData — the same 10 whatever page/limit is passed —
and loads the rest from api.hillzusers.com, which Cloudflare blocks for plain
HTTP clients. Every vehicle in that response also carries `fullIds`: {id, slug}
for the entire inventory, and each slug's detail page is a public server-
rendered page with the full record at pageProps.data. So Formula 7 is scraped as
one list request + one detail request per vehicle not already listed.

Fixtures are real responses captured 2026-10-22, trimmed and sanitised; the
reference vehicle (2014 Ram 1500, stock 150429) is the dealer's own listing.

Run:  python -m pytest tests/test_hillz_providers.py
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from dealer_scraper.adapters.json_api import JsonApiAdapter
from dealer_scraper.adapters.registry import validate_scrape_config
from dealer_scraper.adapters.response_inspector import ListPathError
from dealer_scraper.config_lint import structural_problems

FIX = Path(__file__).parent / "fixtures" / "hillz"
META = json.loads((FIX / "formula7_meta.json").read_text())
BUILD_ID = META["buildId"]

# Verbatim from the working production config.
CONFIG_22G = {
    "headers": {"Accept": "application/json"},
    "endpoint": "https://22gautosales.com/_next/data/FEY-NIZVXw7p4PVRcEO0F/cars.json",
    "field_map": {
        "vin": "Vehicle.vin_number", "make": "Vehicle.make", "trim": "Vehicle.trim",
        "year": "Vehicle.model_year", "model": "Vehicle.model", "price": "sell_price",
        "mileage": "odometer", "body_type": "Vehicle.body_style",
        "fuel_type": "Vehicle.fuel_type", "drivetrain": "Vehicle.drive_type",
        "external_id": "stock_NO", "transmission": "Vehicle.Transmission.name",
        "exterior_color": "Vehicle.exterior_color.name",
        "interior_color": "Vehicle.interior_color.name",
    },
    "list_path": "pageProps.preFetchedData.vehiclesData",
    "max_pages": 500,
    "page_size": 92,
    "start_page": 1,
}

CONFIG_FORMULA7 = {
    "headers": {"Accept": "application/json"},
    "endpoint": "https://formula7motors.ca/_next/data/{build_id}/cars.json",
    "next_build_id_url": "https://formula7motors.ca/cars",
    "list_path": "pageProps.vehiclesData",
    "total_path": "pageProps.vehiclesData.0.fullSearchCount",
    "detail": {
        "index_path": "pageProps.vehiclesData.0.fullIds",
        "key": "id",
        "url": "https://formula7motors.ca/_next/data/{build_id}{slug}.json",
        "item_path": "pageProps.data",
    },
    "url_base": "https://formula7motors.ca",
    "field_map": {**CONFIG_22G["field_map"], "url": "_index.slug"},
    "page_size": 10,
    "start_page": 1,
}


def _client(routes: dict[str, tuple[int, str, str]], log: list[str]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        log.append(url)
        if url not in routes:
            return httpx.Response(404, text="<html><title>404</title></html>",
                                  headers={"content-type": "text/html"})
        status, ctype, body = routes[url]
        return httpx.Response(status, content=body.encode(), headers={"content-type": ctype})
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


def _adapter(cfg: dict, routes: dict, log: list[str]) -> JsonApiAdapter:
    a = JsonApiAdapter({**cfg, "delay_seconds": 0, "use_proxy": False})
    a._client.close()
    a._client = _client(routes, log)
    return a


def _json(name: str) -> tuple[int, str, str]:
    return 200, "application/json", (FIX / name).read_text()


F7_ROUTES = {
    "https://formula7motors.ca/cars": (200, "text/html; charset=utf-8",
                                       (FIX / "formula7_cars_page.html").read_text()),
    f"https://formula7motors.ca/_next/data/{BUILD_ID}/cars.json": _json("formula7_cars.json"),
    f"https://formula7motors.ca/_next/data/{BUILD_ID}/cars/used/2014-ram-1500-638322.json":
        _json("formula7_detail_150429.json"),
    # The fifth index entry sold between the list and its detail request.
}


# ------------------------------------------------------------------ 22G
def test_22g_config_still_validates_unchanged() -> None:
    validate_scrape_config("json_api", CONFIG_22G, "https://22gautosales.com")
    assert structural_problems("json_api", CONFIG_22G) == []


def test_22g_scrape_is_unchanged() -> None:
    log: list[str] = []
    a = _adapter(CONFIG_22G, {CONFIG_22G["endpoint"]: _json("22g_cars.json")}, log)
    got = list(a.iter_vehicles())
    assert log == [CONFIG_22G["endpoint"]], "one request, no build-id lookup, no paging"
    raw = json.loads((FIX / "22g_cars.json").read_text())["pageProps"]["preFetchedData"]["vehiclesData"]
    assert [v.external_id for v in got] == [r["stock_NO"] for r in raw]
    first = got[0]
    assert first.vin == raw[0]["Vehicle"]["vin_number"].upper()  # normalize() upper-cases VINs
    assert first.make == raw[0]["Vehicle"]["make"]
    assert first.transmission == raw[0]["Vehicle"]["Transmission"]["name"]
    assert first.exterior_color == raw[0]["Vehicle"]["exterior_color"]["name"]
    assert a.timer.snapshot()["counters"]["vehicles_mapped"] == len(raw)


# ------------------------------------------------------------ Formula 7
def test_22g_config_against_formula7_fails_loudly_with_the_right_path() -> None:
    """The root cause: a copied 22G config 'succeeded' with 0 vehicles."""
    log: list[str] = []
    cfg = {**CONFIG_22G, "endpoint": f"https://formula7motors.ca/_next/data/{BUILD_ID}/cars.json"}
    a = _adapter(cfg, F7_ROUTES, log)
    with pytest.raises(ListPathError) as exc:
        list(a.iter_vehicles())
    assert "'pageProps.vehiclesData'" in str(exc.value)


def test_formula7_config_validates() -> None:
    validate_scrape_config("json_api", CONFIG_FORMULA7, "https://formula7motors.ca")
    assert structural_problems("json_api", CONFIG_FORMULA7) == []


def test_formula7_reads_listed_vehicles_plus_index_via_detail_pages() -> None:
    log: list[str] = []
    a = _adapter(CONFIG_FORMULA7, F7_ROUTES, log)
    assert a.fetch_total_count() == len(META["index"])
    got = {v.external_id: v for v in a.iter_vehicles()}

    listed = json.loads((FIX / "formula7_cars.json").read_text())["pageProps"]["vehiclesData"]
    expected = {v["stock_NO"] for v in listed} | {"150429"}
    assert set(got) == expected
    assert a.timer.snapshot()["counters"]["detail_gone"] == 1

    # Build id looked up once; list fetched once despite fetch_total_count();
    # only the vehicles NOT in the list response were requested individually.
    assert log.count("https://formula7motors.ca/cars") == 1
    assert log.count(f"https://formula7motors.ca/_next/data/{BUILD_ID}/cars.json") == 1
    detail_reqs = [u for u in log if "/cars/used/" in u]
    assert len(detail_reqs) == len(META["index"]) - len(listed)
    assert len(detail_reqs) == len(set(detail_reqs))


def test_formula7_reference_vehicle_matches_the_live_listing() -> None:
    log: list[str] = []
    ram = {v.external_id: v for v in _adapter(CONFIG_FORMULA7, F7_ROUTES, log).iter_vehicles()}["150429"]
    assert (ram.year, ram.make, ram.model) == (2014, "Ram", "1500")
    assert ram.price == Decimal("10990")
    assert ram.mileage == 247000                      # odometer_type 2 = km
    assert ram.transmission == "Automatic"
    assert ram.drivetrain == "4X4"
    assert ram.vin == "1C6RR7FG2ES150429"
    assert ram.url == "https://formula7motors.ca/cars/used/2014-ram-1500-638322"


def test_formula7_listed_vehicles_get_their_url_from_the_index() -> None:
    log: list[str] = []
    got = list(_adapter(CONFIG_FORMULA7, F7_ROUTES, log).iter_vehicles())
    assert all(v.url and v.url.startswith("https://formula7motors.ca/cars/used/") for v in got)


def test_build_id_follows_a_redeploy() -> None:
    """A new deploy changes the buildId; the config must not need editing."""
    log: list[str] = []
    page = (FIX / "formula7_cars_page.html").read_text().replace(BUILD_ID, "NewBuild123")
    routes = {k.replace(BUILD_ID, "NewBuild123"): v for k, v in F7_ROUTES.items()}
    routes["https://formula7motors.ca/cars"] = (200, "text/html", page)
    got = list(_adapter(CONFIG_FORMULA7, routes, log).iter_vehicles())
    assert got and all(BUILD_ID not in u for u in log)


def test_detail_host_cannot_be_steered_by_the_response() -> None:
    """A slug like '@evil.example/x' must not move the request off-site."""
    data = json.loads((FIX / "formula7_cars.json").read_text())
    data["pageProps"]["vehiclesData"][0]["fullIds"].append({"id": 1, "slug": "@evil.example/x"})
    routes = {**F7_ROUTES,
              f"https://formula7motors.ca/_next/data/{BUILD_ID}/cars.json":
                  (200, "application/json", json.dumps(data))}
    log: list[str] = []
    list(_adapter(CONFIG_FORMULA7, routes, log).iter_vehicles())
    assert all(httpx.URL(u).host == "formula7motors.ca" for u in log)


# ------------------------------------------------- discovery on Hillz sites
def _prefetched(total: int) -> dict:
    data = json.loads((FIX / "22g_cars.json").read_text())
    data["pageProps"]["preFetchedData"]["advanceSearchData"] = {"vehiclesCount": total}
    return data


def test_discovery_prefers_the_listing_route_over_a_prefetched_form_page() -> None:
    """drivenautosales.ca: every Next.js page carries the full inventory, and the
    link prefetch for /forms/finance.json was captured before /cars.json."""
    from dealer_scraper.discovery.net.scorer import _best_vehicle_json

    body = _prefetched(3)
    hits = [{"url": f"https://d.example/_next/data/B1/{route}.json", "json": body}
            for route in ("forms/finance", "cars", "cars/sold")]
    assert _best_vehicle_json(hits)["url"].endswith("/forms/finance.json")
    best = _best_vehicle_json(hits, listing_url="https://d.example/cars")
    assert best["url"] == "https://d.example/_next/data/B1/cars.json"


def test_discovery_derives_the_verified_22g_field_map() -> None:
    """vehicle_site_detail mirrors Vehicle's keys with true/false display flags;
    discovery used to map vin/make/model/year to those flags, and missed price
    (sell_price), drivetrain (drive_type) and the nested Transmission/colour
    objects."""
    from dealer_scraper.discovery.net.fieldmap import _build_field_map, _find_total_path

    data = _prefetched(91)
    items = data["pageProps"]["preFetchedData"]["vehiclesData"]
    assert _build_field_map(items) == CONFIG_22G["field_map"]
    # 22G reports vehiclesCount 91 and lists 89. As a total on an endpoint
    # without {page}, config_lint refuses it; the adapter never reads it there.
    assert _find_total_path(data, len(items)) is None


def test_discovery_prefers_a_filled_reference_object_over_a_sparse_flat_field() -> None:
    """drivenautosales.ca: Vehicle.transmission is set on 6 of 54 cars,
    Vehicle.Transmission.name on all of them."""
    from dealer_scraper.discovery.net.fieldmap import _build_field_map

    items = [{"stock_NO": str(i), "Vehicle": {
        "transmission": "Automatic" if i == 0 else None,
        "Transmission": {"id": 7, "name": "Automatic"}}} for i in range(8)]
    assert _build_field_map(items)["transmission"] == "Vehicle.Transmission.name"


def main() -> None:
    """`python -m tests.test_hillz_providers` — several tests take pytest fixtures
    (parametrize, monkeypatch, caplog), so each named test runs through pytest."""
    import sys

    import pytest as _pytest

    tests = (
        test_22g_config_still_validates_unchanged,
        test_22g_scrape_is_unchanged,
        test_22g_config_against_formula7_fails_loudly_with_the_right_path,
        test_formula7_config_validates,
        test_formula7_reads_listed_vehicles_plus_index_via_detail_pages,
        test_formula7_reference_vehicle_matches_the_live_listing,
        test_formula7_listed_vehicles_get_their_url_from_the_index,
        test_build_id_follows_a_redeploy,
        test_detail_host_cannot_be_steered_by_the_response,
        test_discovery_prefers_the_listing_route_over_a_prefetched_form_page,
        test_discovery_derives_the_verified_22g_field_map,
        test_discovery_prefers_a_filled_reference_object_over_a_sparse_flat_field,
    )
    sys.exit(_pytest.main([__file__, "-q", "-p", "no:cacheprovider",
                           "-k", " or ".join(t.__name__ for t in tests)]))


if __name__ == "__main__":
    main()
