"""Say what the endpoint actually sent, and where the vehicles actually are.

A list_path that does not exist used to read as "0 vehicles"; an HTML page used
to be called a bot challenge whatever it was. Fixtures under tests/fixtures/
hillz are real responses captured from formula7motors.ca and 22gautosales.com
(trimmed and sanitised; structure untouched).

Run:  python -m pytest tests/test_response_inspector.py
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from dealer_scraper.adapters.json_api import BlockedByTarget, JsonApiAdapter
from dealer_scraper.adapters.response_inspector import (
    ListPathError, ResponseFormatError, dig, find_vehicle_arrays, resolve_list,
)
from dealer_scraper.adapters.response_inspector import main as inspector_cli

FIX = Path(__file__).parent / "fixtures" / "hillz"


def _load(name: str):
    return json.loads((FIX / name).read_text())


def _adapter(handler, **cfg) -> JsonApiAdapter:
    a = JsonApiAdapter({"endpoint": "https://d.example/feed.json", "list_path": "vehicles",
                        "field_map": {"external_id": "stock"}, "delay_seconds": 0,
                        "use_proxy": False, **cfg})
    a._client = httpx.Client(transport=httpx.MockTransport(handler))
    return a


# ------------------------------------------------------------ content types
def test_cloudflare_block_page_is_reported_as_a_challenge() -> None:
    html = (FIX / "cloudflare_block.html").read_text()
    a = _adapter(lambda r: httpx.Response(403 if False else 200, text=html,
                                          headers={"content-type": "text/html"}))
    with pytest.raises(BlockedByTarget, match="Attention Required"):
        a._get(a.endpoints[0], 1)


def test_ordinary_html_is_not_parsed_as_json_nor_called_a_challenge() -> None:
    page = "<!DOCTYPE html><html><head><title>Used Cars | Dealer</title></head><body>hi</body></html>"
    a = _adapter(lambda r: httpx.Response(200, text=page, headers={"content-type": "text/html"}))
    with pytest.raises(ResponseFormatError) as exc:
        a._get(a.endpoints[0], 1)
    assert not isinstance(exc.value, BlockedByTarget)
    assert "Used Cars | Dealer" in str(exc.value) and "instead of JSON" in str(exc.value)


def test_login_page_is_named() -> None:
    page = '<html><title>Sign in</title><form><input type="password"></form></html>'
    a = _adapter(lambda r: httpx.Response(200, text=page, headers={"content-type": "text/html"}))
    with pytest.raises(ResponseFormatError, match="login page"):
        a._get(a.endpoints[0], 1)


def test_json_with_wrong_content_type_is_still_json() -> None:
    a = _adapter(lambda r: httpx.Response(200, content=b'{"vehicles": [{"stock": "1"}]}',
                                          headers={"content-type": "text/plain"}))
    assert a._get(a.endpoints[0], 1) == {"vehicles": [{"stock": "1"}]}


def test_truncated_json_is_a_format_error_not_a_crash() -> None:
    a = _adapter(lambda r: httpx.Response(200, content=b'{"vehicles": [',
                                          headers={"content-type": "application/json"}))
    with pytest.raises(ResponseFormatError, match="not valid JSON"):
        a._get(a.endpoints[0], 1)


def test_error_object_is_reported_as_such() -> None:
    a = _adapter(lambda r: httpx.Response(200, json={"error": "Invalid dealer id"}))
    with pytest.raises(ListPathError, match="error object.*Invalid dealer id"):
        list(a.iter_vehicles())


# ------------------------------------------------------------ path resolution
def test_nested_paths_resolve() -> None:
    v = _load("22g_cars.json")["pageProps"]["preFetchedData"]["vehiclesData"][0]
    assert isinstance(dig(v, "Vehicle.vin_number"), str) and len(dig(v, "Vehicle.vin_number")) == 17
    assert dig(v, "Vehicle.Transmission.name")
    assert dig(v, "Vehicle.exterior_color.name")
    assert dig({"a": [{"b": 1}]}, "a.0.b") == 1
    assert dig(v, "Vehicle.nope.name") is None


def test_missing_list_path_suggests_the_real_one() -> None:
    """The Formula 7 failure: 22G's list_path against Formula 7's response."""
    data = _load("formula7_cars.json")
    with pytest.raises(ListPathError) as exc:
        resolve_list(data, "pageProps.preFetchedData.vehiclesData")
    msg = str(exc.value)
    assert "is missing" in msg and "'pageProps.vehiclesData'" in msg


def test_candidates_rank_vehicles_above_lookup_lists() -> None:
    data = _load("formula7_cars.json")
    cands = find_vehicle_arrays(data)
    assert cands[0].path == "pageProps.vehiclesData"
    assert "stock_NO" in cands[0].sample_keys
    # The index of slugs is an array of objects too, but not of vehicles.
    assert all(not c.path.endswith("fullIds") or c.vehicle_score < cands[0].vehicle_score
               for c in cands)


def test_present_but_empty_list_is_an_empty_inventory() -> None:
    assert resolve_list({"pageProps": {"vehiclesData": []}}, "pageProps.vehiclesData") == []


def test_cli_suggests_paths(capsys) -> None:
    assert inspector_cli([str(FIX / "formula7_cars.json")]) == 0
    assert "pageProps.vehiclesData" in capsys.readouterr().out
    assert inspector_cli([str(FIX / "cloudflare_block.html")]) == 1
    assert "challenge" in capsys.readouterr().out


# ------------------------------------------------------------ field mapping
def test_missing_optional_fields_do_not_crash_and_are_counted() -> None:
    items = [
        {"stock_NO": "1", "Vehicle": {"make": "Ram", "vin_number": "1C6RR7FG2ES150429"}},
        {"stock_NO": "2", "Vehicle": {"make": "Kia"}},             # no VIN, no colours
        {"stock_NO": "3"},                                         # no Vehicle at all
    ]
    a = _adapter(lambda r: httpx.Response(200, json={"vehicles": items}), field_map={
        "external_id": "stock_NO", "vin": "Vehicle.vin_number", "make": "Vehicle.make",
        "transmission": "Vehicle.Transmission.name",
        "exterior_color": "Vehicle.exterior_color.name"})
    got = list(a.iter_vehicles())
    assert [v.external_id for v in got] == ["1", "2", "3"]
    assert got[1].vin is None and got[2].make is None and got[0].transmission is None
    counters = a.timer.snapshot()["counters"]
    assert counters["vehicles_mapped"] == 3
    assert counters["missing_vin"] == 2 and counters["missing_make"] == 1


def main() -> None:
    """`python -m tests.test_response_inspector` — several tests take pytest fixtures
    (parametrize, monkeypatch, caplog), so each named test runs through pytest."""
    import sys

    import pytest as _pytest

    tests = (
        test_cloudflare_block_page_is_reported_as_a_challenge,
        test_ordinary_html_is_not_parsed_as_json_nor_called_a_challenge,
        test_login_page_is_named,
        test_json_with_wrong_content_type_is_still_json,
        test_truncated_json_is_a_format_error_not_a_crash,
        test_error_object_is_reported_as_such,
        test_nested_paths_resolve,
        test_missing_list_path_suggests_the_real_one,
        test_candidates_rank_vehicles_above_lookup_lists,
        test_present_but_empty_list_is_an_empty_inventory,
        test_cli_suggests_paths,
        test_missing_optional_fields_do_not_crash_and_are_counted,
    )
    sys.exit(_pytest.main([__file__, "-q", "-p", "no:cacheprovider",
                           "-k", " or ".join(t.__name__ for t in tests)]))


if __name__ == "__main__":
    main()
