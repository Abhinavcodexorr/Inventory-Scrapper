"""Structured data parsing: extract vehicle records from JSON-LD, microdata, hydration.

This analyzer looks for structured data in HTML:
- <script type="application/ld+json"> for JSON-LD
- itemscope/itemtype attributes for microdata
- __NEXT_DATA__, __NUXT__, __INITIAL_STATE__ for hydration state
- __APOLLO_STATE__ for Apollo client cache
"""
from __future__ import annotations

import json
import re
from typing import Any


def extract_jsonld(html: str) -> list[dict[str, Any]]:
    """Extract JSON-LD objects from <script type="application/ld+json"> tags."""
    results = []
    pattern = r'<script\s+type=["\']?application/ld\+json["\']?[^>]*>(.*?)</script>'

    for match in re.finditer(pattern, html, re.DOTALL | re.IGNORECASE):
        try:
            text = match.group(1).strip()
            data = json.loads(text)
            if isinstance(data, list):
                results.extend(data)
            elif isinstance(data, dict):
                results.append(data)
        except json.JSONDecodeError:
            pass

    return results


def extract_next_data(html: str) -> dict[str, Any] | None:
    """Extract __NEXT_DATA__ window variable from HTML."""
    pattern = r'<script\s+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>'
    match = re.search(pattern, html, re.DOTALL)

    if match:
        try:
            text = match.group(1).strip()
            return json.loads(text)
        except json.JSONDecodeError:
            pass

    return None


def extract_nuxt_state(html: str) -> dict[str, Any] | None:
    """Extract __NUXT__ window variable from HTML."""
    pattern = r'window\.__NUXT__\s*=\s*({.*?});'
    match = re.search(pattern, html, re.DOTALL)

    if match:
        try:
            text = match.group(1)
            return json.loads(text)
        except json.JSONDecodeError:
            pass

    return None


def extract_initial_state(html: str) -> dict[str, Any] | None:
    """Extract __INITIAL_STATE__ window variable from HTML."""
    pattern = r'window\.__INITIAL_STATE__\s*=\s*({.*?});'
    match = re.search(pattern, html, re.DOTALL)

    if match:
        try:
            text = match.group(1)
            return json.loads(text)
        except json.JSONDecodeError:
            pass

    return None


def extract_apollo_state(html: str) -> dict[str, Any] | None:
    """Extract __APOLLO_STATE__ from Apollo client cache."""
    pattern = r'window\.__APOLLO_STATE__\s*=\s*({.*?});'
    match = re.search(pattern, html, re.DOTALL)

    if match:
        try:
            text = match.group(1)
            return json.loads(text)
        except json.JSONDecodeError:
            pass

    return None


def find_vehicle_records(data: Any) -> list[dict[str, Any]]:
    """Recursively search a data structure for vehicle-like records.

    A vehicle-like record has at least 2 of: vin, make, model, year, mileage,
    price, body_type, color, trim, condition, external_id, etc.
    """
    vehicle_keys = {
        "vin", "make", "model", "year", "mileage", "price", "body_type",
        "color", "exterior_color", "trim", "condition", "external_id",
        "stock_number", "msrp", "transmission", "engine", "fuel_type",
        "interior_color", "interior", "drivetrain", "doors"
    }

    results = []

    def recurse(obj: Any, depth: int = 0) -> None:
        if depth > 10:  # Prevent infinite recursion
            return

        if isinstance(obj, dict):
            # Check if this dict looks like a vehicle
            matching_keys = set(k.lower() for k in obj.keys()) & vehicle_keys
            if len(matching_keys) >= 2:
                results.append(obj)

            # Recurse into values
            for value in obj.values():
                recurse(value, depth + 1)

        elif isinstance(obj, (list, tuple)):
            for item in obj:
                recurse(item, depth + 1)

    recurse(data)
    return results


def extract_all_structured_data(html: str) -> dict[str, Any]:
    """Extract all available structured data from HTML.

    Returns a dict with keys: jsonld, next_data, nuxt_state, initial_state,
    apollo_state, and vehicle_records (aggregated across all sources).
    """
    jsonld = extract_jsonld(html)
    next_data = extract_next_data(html)
    nuxt_state = extract_nuxt_state(html)
    initial_state = extract_initial_state(html)
    apollo_state = extract_apollo_state(html)

    # Find vehicle records across all sources
    vehicle_records = []
    for data in [jsonld, next_data, nuxt_state, initial_state, apollo_state]:
        if data:
            vehicle_records.extend(find_vehicle_records(data))

    return {
        "jsonld": jsonld,
        "next_data": next_data,
        "nuxt_state": nuxt_state,
        "initial_state": initial_state,
        "apollo_state": apollo_state,
        "vehicle_records": vehicle_records,
    }
