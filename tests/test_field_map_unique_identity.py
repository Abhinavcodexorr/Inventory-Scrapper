"""A guessed `url`/`external_id` mapping must tell cars apart.

CarPages feeds (autocitygta.ca, dealonwheels.ca) carry `contact.url`, the dealer's
homepage, on every vehicle. Mapping it as the vehicle link made every row look
like the same car: "43 rows but only 1 were distinct vehicles".

Run:  python -m pytest tests/test_field_map_unique_identity.py
"""
from __future__ import annotations

from dealer_scraper.discovery.net.fieldmap import _build_field_map


def _carpages_item(n: int) -> dict:
    return {
        "id": 15000000 + n,
        "vin": f"1HGCM82633A{n:06d}",
        "stock_num": None,
        "year": 2017,
        "price": 10800 + n,
        "make": {"name": "Kia"},
        "model": {"name": "Forte"},
        "contact": {"url": "https://dealer.example"},  # same for every car
    }


def test_url_that_repeats_across_cars_is_not_mapped():
    items = [_carpages_item(n) for n in range(15)]
    field_map = _build_field_map(items)
    assert "url" not in field_map, field_map
    # Identity still comes from fields that differ per car.
    assert field_map.get("vin") == "vin"


def test_url_that_differs_per_car_is_kept():
    items = [{**_carpages_item(n), "vdp_url": f"https://dealer.example/inventory/car-{n}"}
             for n in range(15)]
    field_map = _build_field_map(items)
    assert field_map.get("url") == "vdp_url", field_map


def test_two_row_sample_is_too_small_to_judge():
    items = [_carpages_item(n) for n in range(2)]
    assert "url" in _build_field_map(items)
