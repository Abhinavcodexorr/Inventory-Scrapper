"""Unit tests for the JSON-API sniffing heuristics in dealer_scraper.discovery.

Pure functions only — no Playwright, no browser, no DB needed.

Run:  python -m tests.test_discovery_parse
"""
from __future__ import annotations

from dealer_scraper.discovery import (
    _DETAIL_LINKS_JS,
    _FIND_MORE_BUTTON_JS,
    _best_vehicle_json,
    _build_detail_id_regex,
    _build_field_map,
    _detect_body_pagination,
    _detect_pagination,
    _extra_request_headers,
    _find_total_path,
    _find_vehicle_array,
    _needs_url_base,
    _template_body,
    _template_endpoint,
    _verify_dom_pagination,
)
from dealer_scraper.discovery.strategies.pagination import _DETECT_PAGINATION_HINTS_JS, _detect_dom_pagination
from dealer_scraper.adapters.json_api import JsonApiAdapter

# A DealerOn/Clutch-style envelope: array of vehicles nested under results.vehicles,
# with a total count alongside it.
SAMPLE_PAYLOAD = {
    "results": {
        "total": 143,
        "vehicles": [
            {
                "id": "9915201",
                "vin": "1C4RJFAG0EC123456",
                "make": "Chrysler",
                "model": "Pacifica",
                "year": 2024,
                "trim": "Touring-L",
                "mileage_km": 30690,
                "price": 44990,
                "vdp_url": "/vehicles/9915201",
            },
            {
                "id": "9915202",
                "vin": "2C4RJFBG1FC654321",
                "make": "Honda",
                "model": "Civic",
                "year": 2022,
                "trim": "LX",
                "mileage_km": 12000,
                "price": 24990,
                "vdp_url": "/vehicles/9915202",
            },
            {
                "id": "9915203",
                "vin": "3C4RJFCG2GC112233",
                "make": "Toyota",
                "model": "RAV4",
                "year": 2023,
                "trim": "XLE",
                "mileage_km": 8000,
                "price": 32990,
                "vdp_url": "/vehicles/9915203",
            },
        ],
    }
}

# Traffic noise: an analytics/config payload that must NOT be mistaken for a
# vehicle feed.
NOISE_PAYLOAD = {
    "featureFlags": {"newNav": True, "darkMode": False},
    "translations": [{"key": "greeting", "value": "hello"}, {"key": "bye", "value": "bye"}],
}


def test_find_vehicle_array():
    found = _find_vehicle_array(SAMPLE_PAYLOAD)
    assert found["path"] == "results.vehicles", found
    assert len(found["array"]) == 3
    assert found["score"] > 0

    noise = _find_vehicle_array(NOISE_PAYLOAD)
    assert noise["array"] is None, noise


def test_best_vehicle_json_ignores_noise():
    hits = [
        {"url": "https://dealer.example.com/api/config", "json": NOISE_PAYLOAD},
        {"url": "https://dealer.example.com/api/srp/vehicles?page=1&limit=25", "json": SAMPLE_PAYLOAD},
    ]
    best = _best_vehicle_json(hits)
    assert best is not None
    assert best["list_path"] == "results.vehicles"
    assert len(best["array"]) == 3


def test_build_field_map_maps_identity_and_core_fields():
    field_map = _build_field_map(SAMPLE_PAYLOAD["results"]["vehicles"])
    assert field_map["external_id"] == "id"
    assert field_map["vin"] == "vin"
    assert field_map["make"] == "make"
    assert field_map["model"] == "model"
    assert field_map["year"] == "year"
    assert field_map["price"] == "price"
    assert field_map["mileage"] == "mileage_km"
    assert field_map["url"] == "vdp_url"


def test_find_total_path():
    path = _find_total_path(SAMPLE_PAYLOAD, array_len=3)
    assert path == "results.total", path


def test_needs_url_base_true_for_root_relative_urls():
    field_map = _build_field_map(SAMPLE_PAYLOAD["results"]["vehicles"])
    assert _needs_url_base(field_map, SAMPLE_PAYLOAD["results"]["vehicles"]) is True


def test_needs_url_base_false_for_absolute_urls():
    items = [{"vdp_url": "https://dealer.example.com/vehicles/1"}]
    field_map = {"url": "vdp_url"}
    assert _needs_url_base(field_map, items) is False


def test_detect_pagination_page_style():
    urls = [
        "https://api.example.com/v1/vehicles?page=1&limit=25",
        "https://api.example.com/v1/vehicles?page=2&limit=25",
    ]
    info = _detect_pagination(urls)
    assert info is not None
    assert info["param"] == "page"
    assert info["start"] == 1
    assert info["step"] == 1
    assert info["size_param"] == "limit"
    assert info["size_value"] == 25

    endpoint = _template_endpoint(info["template_url"], {"page": "{page}", "limit": "{page_size}"})
    assert endpoint == "https://api.example.com/v1/vehicles?page={page}&limit={page_size}", endpoint


def test_detect_pagination_offset_style():
    urls = [
        "https://api.example.com/srp/vehicles/17485?pn=24&pt=0",
        "https://api.example.com/srp/vehicles/17485?pn=24&pt=24",
    ]
    info = _detect_pagination(urls)
    assert info is not None
    assert info["param"] == "pt"
    assert info["start"] == 0
    assert info["step"] == 24
    assert info["size_param"] == "pn"
    assert info["size_value"] == 24


def test_detect_pagination_rejects_ambiguous_or_noisy_diffs():
    # More than one query param differs -> too ambiguous to trust.
    assert _detect_pagination([
        "https://api.example.com/v1/vehicles?page=1&sort=price",
        "https://api.example.com/v1/vehicles?page=2&sort=year",
    ]) is None

    # Only one distinct call seen -> nothing to diff.
    assert _detect_pagination([
        "https://api.example.com/v1/vehicles?page=1",
        "https://api.example.com/v1/vehicles?page=1",
    ]) is None

    # Huge values look like a cache-busting timestamp, not a page cursor.
    assert _detect_pagination([
        "https://api.example.com/v1/vehicles?_=1699999999",
        "https://api.example.com/v1/vehicles?_=1700000005",
    ]) is None


def test_detect_pagination_implicit_default_on_first_call():
    # The site's first/default request omits "page" entirely (server defaults
    # to page 1); only a later request makes it explicit. Must still be
    # recognised, using the well-known "page"-style default of 1.
    urls = [
        "https://api.example.com/v1/vehicles?limit=18",
        "https://api.example.com/v1/vehicles?limit=18&page=2",
    ]
    info = _detect_pagination(urls)
    assert info is not None, info
    assert info["param"] == "page"
    assert info["start"] == 1
    assert info["step"] == 1
    assert info["template_url"] == urls[1]  # only this one actually has "page="


def test_detect_pagination_implicit_default_unknown_name_declines():
    # Same shape, but the param name isn't a recognised page/offset convention
    # -> must not guess.
    urls = [
        "https://api.example.com/v1/vehicles?limit=18",
        "https://api.example.com/v1/vehicles?limit=18&cursor=abc123",
    ]
    assert _detect_pagination(urls) is None


# A real-world-shaped enterprise inventory API: make/model/trim/colors are
# nested "reference objects" ({"id":..,"name":..} or locale-paired dicts)
# rather than plain scalars.
NESTED_PAYLOAD = {
    "vehicles": [
        {
            "vehicleId": 38907933,
            "serialNo": "1GCPTCEK3P1243081",
            "stockNo": "ST045A",
            "year": 2023,
            "listPrice": 40995.0,
            "odometer": 25000,
            "make": {"id": 575, "slug": "chevrolet", "name": "Chevrolet"},
            "model": {"id": 2647, "slug": "colorado", "name": "Colorado"},
            "trim": {"id": None, "slug": None, "name": "4WD LT Crew"},
            "bodyStyle": {"slug": None, "name": "Pickup - Crew Cab"},
            "exteriorColor": {"id": 1, "color": None, "colorEn": "Black", "colorFr": "Noir"},
            "interiorColor": {"id": 1, "color": None, "colorEn": "Black", "colorFr": "Noir"},
        },
        {
            "vehicleId": 38627866,
            "serialNo": "1FMCU9MZ5PUA60160",
            "stockNo": "ST099B",
            "year": 2022,
            "listPrice": 24873.0,
            "odometer": 12000,
            "make": {"id": 12, "slug": "honda", "name": "Honda"},
            "model": {"id": 55, "slug": "civic", "name": "Civic"},
            "trim": {"id": None, "slug": None, "name": "LX"},
            "bodyStyle": {"slug": None, "name": "Sedan"},
            "exteriorColor": {"id": 2, "color": None, "colorEn": "White", "colorFr": "Blanc"},
            "interiorColor": {"id": 2, "color": None, "colorEn": "Grey", "colorFr": "Gris"},
        },
    ],
    "pagination": {"pageNumber": 1, "pageSize": 18, "numberOfItems": 13781},
}


def test_build_field_map_resolves_nested_reference_objects():
    field_map = _build_field_map(NESTED_PAYLOAD["vehicles"])
    assert field_map["external_id"] == "vehicleId"
    assert field_map["vin"] == "serialNo"
    assert field_map["make"] == "make.name"
    assert field_map["model"] == "model.name"
    assert field_map["trim"] == "trim.name"
    assert field_map["body_type"] == "bodyStyle.name"
    # Locale-paired leaves with no plain "name" key: colorEn wins over the
    # null-valued "color" sibling that a blunt exact-name match would grab.
    assert field_map["exterior_color"] == "exteriorColor.colorEn"
    assert field_map["interior_color"] == "interiorColor.colorEn"


def test_find_total_path_matches_numberofitems_synonym():
    path = _find_total_path(NESTED_PAYLOAD, array_len=2)
    assert path == "pagination.numberOfItems", path


def test_build_field_map_prefers_populated_price_over_null_sibling():
    # Real-world APIs often carry several price-like fields where the
    # "obvious" one (salePrice) is null on some listings while another
    # (originalPrice) is reliably populated — must not lock onto the first
    # name match found, the way pass 1 used to.
    items = [
        {"id": 1, "salePrice": None, "originalPrice": 26995.0, "vehicleYear": 2020},
        {"id": 2, "salePrice": 40995.0, "originalPrice": 40995.0, "vehicleYear": 2022},
    ]
    field_map = _build_field_map(items)
    assert field_map["price"] == "salePrice", field_map  # populated on item 2
    assert field_map["year"] == "vehicleYear", field_map


def test_detect_body_pagination_single_field():
    bodies = [{"pageNo": 1, "pageSize": 20}, {"pageNo": 2, "pageSize": 20}]
    info = _detect_body_pagination(bodies)
    assert info == {"kind": "single", "key": "pageNo", "start": 1, "step": 1}, info


def test_detect_body_pagination_start_end_pair():
    bodies = [
        {"sortCode": "recents", "startIndex": 1, "endIndex": 24, "searchKey": ""},
        {"sortCode": "recents", "startIndex": 25, "endIndex": 48, "searchKey": ""},
    ]
    info = _detect_body_pagination(bodies)
    assert info is not None, info
    assert info["kind"] == "pair"
    assert info["start_key"] == "startIndex"
    assert info["end_key"] == "endIndex"
    assert info["start"] == 1
    assert info["step"] == 24


def test_detect_body_pagination_rejects_mismatched_or_noisy_pairs():
    # Two fields change, but by different amounts -> not a real window pair.
    assert _detect_body_pagination([
        {"a": 1, "b": 10}, {"a": 2, "b": 30},
    ]) is None
    # Only one body seen -> nothing to diff.
    assert _detect_body_pagination([{"a": 1}]) is None
    # No numeric field changed at all.
    assert _detect_body_pagination([{"a": 1, "x": "same"}, {"a": 1, "x": "same"}]) is None


def test_template_body_substitutes_nested_placeholders():
    body = {"sortCode": "recents", "startIndex": 1, "endIndex": 24,
            "options": {"update": False, "platform": "ECOMM"}}
    templated = _template_body(body, {"startIndex": "{page}", "endIndex": "{page_end}"})
    assert templated == {
        "sortCode": "recents", "startIndex": "{page}", "endIndex": "{page_end}",
        "options": {"update": False, "platform": "ECOMM"},
    }, templated


def test_extra_request_headers_keeps_auth_drops_boilerplate():
    captured = {
        "authorization": "Basic abc123",
        "referer": "https://dealer.example.com/",
        "user-agent": "Mozilla/5.0 ...",
        "accept-encoding": "gzip, deflate, br",
        "cookie": "session=xyz",
        "sec-ch-ua": '"Chromium";v="131"',
        "content-length": "42",
        "": "",
    }
    extra = _extra_request_headers(captured)
    assert extra == {"authorization": "Basic abc123", "referer": "https://dealer.example.com/"}, extra


def test_build_detail_id_regex_prefers_trailing_numeric_id():
    # /{seg}/<slug>/<numeric-id>/ shape: several different physical units
    # commonly share the same slug (same year/make/model/trim), so the
    # slug alone is not a safe external_id — must capture the numeric tail.
    samples = [
        "/inventory/2024-toyota-rav4-le-awd/2516",
        "/inventory/2024-toyota-rav4-le-awd/2515",
        "/inventory/2018-hyundai-elantra-sel/2543",
    ]
    pattern = _build_detail_id_regex("inventory", samples)
    assert pattern == r"/inventory/[\w-]+/(\d+)", pattern
    import re
    assert re.search(pattern, samples[0]).group(1) == "2516"
    assert re.search(pattern, samples[1]).group(1) == "2515"


def test_build_detail_id_regex_falls_back_to_slug_when_no_numeric_tail():
    # /{seg}/<id>/ shape (two segments, id is the whole trailing token) —
    # no deeper numeric segment to prefer, so keep capturing it directly.
    samples = ["/vehicles/9915201", "/vehicles/9915202"]
    assert _build_detail_id_regex("vehicles", samples) == r"/vehicles/([\w-]+)"

    # Mixed/inconsistent shapes -> don't guess, fall back to the safe default.
    mixed = ["/inventory/2024-toyota-rav4-le-awd/2516", "/inventory/some-slug-only"]
    assert _build_detail_id_regex("inventory", mixed) == r"/inventory/([\w-]+)"


def test_build_detail_id_regex_skips_fixed_segment_that_collapses_ids():
    # driftmotors.ca: /cars/used/<slug>. Capturing the segment after /cars/
    # gave every vehicle the id "used" and a sync collapsed 88 rows into 1.
    import re
    samples = ["/cars/used/2019-honda-civic-lx-7781", "/cars/used/2020-toyota-camry-se-7790",
               "/cars/used/2018-ford-f-150-xlt-7712"]
    pattern = _build_detail_id_regex("cars", samples)
    ids = [re.search(pattern, s).group(1) for s in samples]
    assert len(set(ids)) == 3, (pattern, ids)
    assert re.search(pattern, "https://www.driftmotors.ca/cars/used/2019-honda-civic-lx-7781/").group(1) \
        == "2019-honda-civic-lx-7781"

    # Deep shape (tabangimotors): the per-vehicle id is the last segment.
    deep = ["/vehicles/2015/honda/civic/mississauga/on/12345678/",
            "/vehicles/2015/honda/civic/mississauga/on/12345679/"]
    pattern = _build_detail_id_regex("vehicles", deep)
    assert [re.search(pattern, d).group(1) for d in deep] == ["12345678", "12345679"]


# ---- pagination detection (fake Playwright page — still no real browser) ----
class _FakeHandle:
    def __init__(self, on_click):
        self._on_click = on_click

    def click(self):
        self._on_click()


class _FakeMouse:
    def __init__(self, owner):
        self._owner = owner

    def wheel(self, dx, dy):
        self._owner.scrolls += 1


class _FakePage:
    """Minimal Playwright Page stub for _detect_dom_pagination."""

    def __init__(self, base_links, scroll_adds=None, button=None,
                 url_paging_hint=False):
        self.base_links = list(base_links)
        self.scroll_adds = scroll_adds or {}  # {total scroll count: [new links]}
        self.button = button
        self.url_paging_hint = url_paging_hint
        self.url = ""
        self.scrolls = 0
        self.mouse = _FakeMouse(self)

    def goto(self, url, **kwargs):
        self.url = url
        self.scrolls = 0

    def wait_for_timeout(self, ms):
        pass

    def evaluate(self, script, arg=None):
        if script is _FIND_MORE_BUTTON_JS:
            return self.button
        if script is _DETECT_PAGINATION_HINTS_JS:
            if self.url_paging_hint:
                return {
                    "urlPaging": True,
                    "pageUrlTemplate": "https://d.example.com/inventory?page={page}",
                }
            return {"urlPaging": False, "pageUrlTemplate": None}
        assert script is _DETAIL_LINKS_JS
        links = list(self.base_links)
        for n in range(1, self.scrolls + 1):
            links += self.scroll_adds.get(n, [])
        return links

    def query_selector(self, sel):
        return None


_BASE_LINKS = [f"/inventory/car-{n}/{1000 + n}" for n in range(12)]


def test_detect_dom_pagination_url_mode_from_dom_hints():
    page = _FakePage(_BASE_LINKS, url_paging_hint=True)
    pag = _detect_dom_pagination(page, "https://d.example.com/inventory", 1000)
    assert pag["mode"] == "url", pag
    assert not pag["verified"], pag
    assert pag["page_url"] == "https://d.example.com/inventory?page={page}"


def test_detect_dom_pagination_scroll_mode_when_lazy_load_grows():
    # After reveal_lazy_cards (3 scrolls), link count grows — infinite scroll inferred.
    page = _FakePage(_BASE_LINKS, scroll_adds={3: [f"/inventory/more-{n}/{3000 + n}" for n in range(6)]})
    pag = _detect_dom_pagination(page, "https://d.example.com/inventory", 1000)
    assert pag["mode"] == "scroll", pag
    assert not pag["verified"], pag
    assert pag["observed"] == 18, pag
    assert any("Lazy-loaded" in n for n in pag["notes"]), pag["notes"]


def test_detect_dom_pagination_click_mode_emits_next_selector():
    page = _FakePage(_BASE_LINKS,
                     button={"selector": "button.load-more", "kind": "more", "label": "Load More"})
    pag = _detect_dom_pagination(page, "https://d.example.com/inventory", 1000)
    assert pag["mode"] == "click", pag
    assert not pag["verified"], pag
    assert pag["next_selector"] == "button.load-more"


def test_detect_dom_pagination_single_page_defaults_to_scroll():
    page = _FakePage(_BASE_LINKS)
    pag = _detect_dom_pagination(page, "https://d.example.com/inventory", 1000)
    assert pag["mode"] == "scroll" and not pag["verified"], pag
    assert pag["observed"] == len(_BASE_LINKS), pag


def test_verify_dom_pagination_is_detection_alias():
    page = _FakePage(_BASE_LINKS, url_paging_hint=True)
    assert _verify_dom_pagination(page, "https://d.example.com/inventory", 1000) == \
        _detect_dom_pagination(page, "https://d.example.com/inventory", 1000)


def test_json_api_adapter_post_pagination_renders_placeholders():
    cfg = {
        "endpoint": "https://api.example.com/inventory",
        "method": "POST",
        "body": {"sortCode": "recents", "startIndex": "{page}", "endIndex": "{page_end}",
                  "options": {"platform": "ECOMM"}},
        "list_path": "vehicles",
        "field_map": {"external_id": "id"},
        "page_size": 24,
        "start_page": 1,
        "page_step": 24,
    }
    adapter = JsonApiAdapter(cfg)
    assert adapter.method == "POST"
    assert adapter._render_body(1) == {
        "sortCode": "recents", "startIndex": 1, "endIndex": 24,
        "options": {"platform": "ECOMM"},
    }
    assert adapter._render_body(25) == {
        "sortCode": "recents", "startIndex": 25, "endIndex": 48,
        "options": {"platform": "ECOMM"},
    }
    # Page-value placeholders must render as real ints, not stringified ones —
    # the source APIs expect JSON numbers, not "1"/"24" strings.
    body = adapter._render_body(1)
    assert isinstance(body["startIndex"], int) and isinstance(body["endIndex"], int)


def main() -> None:
    test_find_vehicle_array()
    test_best_vehicle_json_ignores_noise()
    test_build_field_map_maps_identity_and_core_fields()
    test_find_total_path()
    test_needs_url_base_true_for_root_relative_urls()
    test_needs_url_base_false_for_absolute_urls()
    test_detect_pagination_page_style()
    test_detect_pagination_offset_style()
    test_detect_pagination_rejects_ambiguous_or_noisy_diffs()
    test_detect_pagination_implicit_default_on_first_call()
    test_detect_pagination_implicit_default_unknown_name_declines()
    test_build_field_map_resolves_nested_reference_objects()
    test_find_total_path_matches_numberofitems_synonym()
    test_build_field_map_prefers_populated_price_over_null_sibling()
    test_detect_body_pagination_single_field()
    test_detect_body_pagination_start_end_pair()
    test_detect_body_pagination_rejects_mismatched_or_noisy_pairs()
    test_build_detail_id_regex_prefers_trailing_numeric_id()
    test_build_detail_id_regex_falls_back_to_slug_when_no_numeric_tail()
    test_build_detail_id_regex_skips_fixed_segment_that_collapses_ids()
    test_detect_dom_pagination_url_mode_from_dom_hints()
    test_detect_dom_pagination_scroll_mode_when_lazy_load_grows()
    test_detect_dom_pagination_click_mode_emits_next_selector()
    test_detect_dom_pagination_single_page_defaults_to_scroll()
    test_verify_dom_pagination_is_detection_alias()
    test_template_body_substitutes_nested_placeholders()
    test_extra_request_headers_keeps_auth_drops_boilerplate()
    test_json_api_adapter_post_pagination_renders_placeholders()
    print("DISCOVERY JSON-API PARSE TEST PASSED - array/field_map/total/pagination detection")


if __name__ == "__main__":
    main()
