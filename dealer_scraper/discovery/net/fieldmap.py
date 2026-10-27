"""Field mapping: guess our_field -> dotted source path from sample JSON items,
by matching normalised key names (with a nested-reference-object pass for
enterprise-shaped APIs). Ported from dealer_scraper/discovery.py (pre-refactor).

Phase 3 adds value-shape validation: each field is checked against sanity rules
(VIN check-digit, price/year/mileage numeric ranges) so invalid paths are rejected.
"""
from __future__ import annotations

import re
from typing import Any

from ..heuristics import DEFAULT_HEURISTICS, HeuristicsConfig


def _flatten(obj: dict, prefix: str = "", depth: int = 4) -> dict:
    """Dotted-path -> value for a dict, recursing into nested dicts (not lists)."""
    out: dict = {}
    if depth < 0 or not isinstance(obj, dict):
        return out
    for k, v in obj.items():
        p = f"{prefix}.{k}" if prefix else str(k)
        out[p] = v
        if isinstance(v, dict):
            out.update(_flatten(v, p, depth - 1))
    return out


def _best_label_leaf(container: dict, heuristics: HeuristicsConfig) -> str | None:
    """Inside a nested 'reference object' (e.g. {"id":5,"name":"Chevrolet",...},
    or a locale-paired {"colorEn":"Black","colorFr":"Noir",...}), find the leaf
    key most likely to hold the human-readable value."""
    keys_norm = {
        re.sub(r"[_\-]", "", k).lower(): k
        for k, v in container.items() if isinstance(v, str) and v.strip()
    }
    for cand in heuristics.label_leaf_priority:
        if cand in keys_norm:
            return keys_norm[cand]
    # Locale-paired leaves (colorEn/colorFr, nameEn/nameFr) — take the "En" one,
    # but only when a matching "Fr" sibling proves it really is a locale pair
    # (otherwise "isFrozen"-style keys would false-positive on "...en$").
    for norm, orig in keys_norm.items():
        if norm.endswith("en") and len(norm) > 2 and (norm[:-2] + "fr") in keys_norm:
            return orig
    for cand in heuristics.label_leaf_fallback:
        if cand in keys_norm:
            return keys_norm[cand]
    return None


def _best_url_leaf(container: dict, pattern: re.Pattern, heuristics: HeuristicsConfig) -> str | None:
    """Inside a nested container, find the leaf most likely to hold a real
    link: absolute (http) values beat root-relative ones, and a list of links
    beats a single one (tier 0 = absolute list ... tier 3 = relative single)."""
    best_tier, best_key = None, None
    for k, v in container.items():
        norm = re.sub(r"[_\-]", "", k).lower()
        if not pattern.search(norm) or heuristics.link_leaf_exclude_re.search(norm):
            continue
        if isinstance(v, list) and v and isinstance(v[0], str) and v[0].strip():
            tier = 0 if v[0].startswith("http") else 2
        elif isinstance(v, str) and v.strip():
            tier = 1 if v.startswith("http") else 3
        else:
            continue
        if best_tier is None or tier < best_tier:
            best_tier, best_key = tier, k
    return best_key


def _build_field_map(sample_items: list[dict], heuristics: HeuristicsConfig = DEFAULT_HEURISTICS) -> dict[str, str]:
    """our_field -> dotted source path, guessed by matching normalised key names
    across a handful of sample items (prefers a key that actually has a value).

    Two passes: a direct leaf-name match handles flat/sensibly-named APIs; a
    second pass matches nested "reference object" containers (make/model/trim/
    bodyStyle/exteriorColor/... as {"id":..,"name":..} or locale-paired dicts),
    which is a very common shape in enterprise dealer-inventory APIs.
    """
    seen: dict[str, tuple[int, str, object]] = {}
    filled: dict[str, int] = {}  # path -> how many sample items have a value there
    containers: dict[str, tuple[str, dict]] = {}
    # Reference objects one level down (HillzDealer: Vehicle.Transmission,
    # Vehicle.exterior_color). Consulted after the top-level ones.
    nested: dict[str, tuple[str, dict]] = {}
    for item in sample_items:
        if not isinstance(item, dict):
            continue
        for path, value in _flatten(item).items():
            leaf = path.split(".")[-1]
            norm = re.sub(r"[_\-]", "", leaf).lower()
            # A bool is never a vehicle field's value, and a dict/list is
            # skipped below — neither may shadow a real scalar of the same
            # name. HillzDealer pairs Vehicle.vin_number ("1FT...") with a
            # display flag vehicle_site_detail.vin_number (true).
            has_value = 1 if (value not in (None, "", [])
                              and not isinstance(value, (bool, dict, list))) else 0
            filled[path] = filled.get(path, 0) + has_value
            cur = seen.get(norm)
            if cur is None or filled[path] > cur[0]:
                seen[norm] = (filled[path], path, value)
            if "." in path and isinstance(value, dict) and value:
                nested.setdefault(norm, (path, value))
        for k, v in item.items():
            if isinstance(v, dict) and v:
                norm_k = re.sub(r"[_\-]", "", k).lower()
                if norm_k not in containers:
                    containers[norm_k] = (k, v)
    for norm_k, entry in nested.items():
        containers.setdefault(norm_k, entry)

    field_map: dict[str, str] = {}
    # Pass-1 hits deferred to pass 2: the value was null/empty in more than
    # half the sample items (a nested container's populated leaf, e.g.
    # exteriorColor.colorEn, beats a same-named but empty sibling
    # exteriorColor.color pass 1 would otherwise lock in; HillzDealer fills
    # Vehicle.transmission for a few cars and Vehicle.Transmission.name for all).
    weak: dict[str, str] = {}
    n_items = sum(1 for item in sample_items if isinstance(item, dict))

    for our_field, synonyms in heuristics.field_synonyms.items():
        # Keep scanning every synonym for a match with an actual (non-null)
        # value — the first NAME match isn't necessarily the best one (e.g. a
        # site's "salePrice" is often null while "originalPrice" is populated).
        fallback_path, fallback_filled = None, -1
        for syn in synonyms:
            hit = seen.get(syn)
            if not hit:
                continue
            n_filled, path, value = hit
            if isinstance(value, (dict, list, bool)):
                continue
            if n_filled > fallback_filled:
                fallback_path, fallback_filled = path, n_filled
            if n_filled and n_filled * 2 >= n_items:
                field_map[our_field] = path
                break
        else:
            if fallback_path is not None:
                weak[our_field] = fallback_path

    for our_field, synonyms in heuristics.field_synonyms.items():
        if our_field in field_map:
            continue
        for norm_key, (orig_key, container) in containers.items():
            if not any(norm_key.startswith(syn) or syn.startswith(norm_key) for syn in synonyms):
                continue
            if our_field == "url":
                leaf = _best_url_leaf(container, heuristics.link_leaf_re, heuristics)
            else:
                leaf = _best_label_leaf(container, heuristics)
            if leaf:
                field_map[our_field] = f"{orig_key}.{leaf}"
                break

    for our_field, path in weak.items():
        field_map.setdefault(our_field, path)

    # A "status" key means a vehicle's lifecycle on some feeds and a numeric
    # code or record flag on others (HillzDealer: vehicle_status = 4). Map it
    # only when its values are words the engine understands; anything else
    # would be silently ignored at sync time anyway.
    if "source_status" in field_map:
        from ...normalize import map_source_status
        from ...adapters.json_api import _dig

        path = field_map["source_status"]
        if not any(map_source_status(_dig(item, path)) for item in sample_items
                   if isinstance(item, dict)):
            del field_map["source_status"]

    return field_map


def _find_total_path(root, array_len: int, heuristics: HeuristicsConfig = DEFAULT_HEURISTICS) -> str | None:
    """Dotted path to a numeric 'how many total' field, if the response has one.

    Selects the largest plausible total to avoid under-reporting inventory
    (which would affect removal safety gates). A response with resultCount: 24
    (page size) and totalCount: 1200 should return totalCount, not resultCount.
    """
    if not isinstance(root, dict):
        return None
    candidates = []
    for path, value in _flatten(root, depth=4).items():
        leaf = path.split(".")[-1]
        norm = re.sub(r"[_\-]", "", leaf).lower()
        if norm in heuristics.total_synonyms and isinstance(value, (int, float)) and not isinstance(value, bool):
            if value >= array_len:
                candidates.append((value, path))
    if not candidates:
        return None
    # Select the LARGEST total (not smallest as before), to avoid under-reporting inventory
    candidates.sort(key=lambda t: t[0], reverse=True)
    return candidates[0][1]


def _validate_vin(vin: str) -> bool:
    """Check VIN format and verify checksum (ISO 3779)."""
    if not isinstance(vin, str) or len(vin) != 17:
        return False
    # Uppercase for consistent validation (VINs are case-insensitive)
    vin = vin.upper()
    if not vin.isalnum() or "I" in vin or "O" in vin or "Q" in vin:
        return False
    # Checksum: position 9 should match computed check digit
    # Transliterator maps each character to its numeric equivalent for the weighted sum
    transliterator = "0123456789.ABCDEFGH..JKLMN.P.R..STUVWXYZ"
    weights = [8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2]
    total = sum(
        (transliterator.index(vin[i]) % 10) * w
        for i, w in enumerate(weights) if i != 8
    )
    # Calculate check digit: 0-9 or X (representing 10)
    check_digit_value = total % 11
    expected_check = vin[8]
    # The check digit is X if value is 10, otherwise the digit itself
    if check_digit_value == 10:
        return expected_check == "X"
    else:
        try:
            return int(expected_check) == check_digit_value
        except ValueError:
            return False


def _validate_year(year: int | str) -> bool:
    """Check year is in valid vehicle range."""
    try:
        y = int(year)
        return 1900 <= y <= 2050
    except (TypeError, ValueError):
        return False


def _validate_price(price: int | float | str) -> bool:
    """Check price is in valid range."""
    try:
        p = float(price)
        return 0 < p < 1_000_000_000
    except (TypeError, ValueError):
        return False


def _validate_mileage(mileage: int | str) -> bool:
    """Check mileage is in valid range."""
    try:
        m = int(mileage)
        return 0 <= m < 1_000_000
    except (TypeError, ValueError):
        return False


def validate_field_value(field_name: str, value: Any) -> bool:
    """Check if a field value is valid for its type.

    Used during field mapping to reject obviously-broken paths
    (e.g. a field marked as 'year' but containing a string that's not a number).
    Returns False on any exception (malformed values).
    """
    try:
        if value is None or value == "":
            return True  # Null is OK; we're just checking non-null values

        if field_name == "vin":
            return isinstance(value, str) and _validate_vin(value)

        if field_name == "year":
            return _validate_year(value)

        if field_name == "price" or field_name == "msrp":
            return _validate_price(value)

        if field_name == "mileage":
            return _validate_mileage(value)

        # Other fields: just check it's not a bizarre type
        if field_name in ("make", "model", "trim", "body_type", "condition", "exterior_color", "interior_color"):
            return isinstance(value, str) and len(str(value).strip()) > 0

        if field_name == "external_id":
            return isinstance(value, (str, int)) and str(value).strip()

        return True  # Unknown field: assume OK
    except Exception:
        return False


def _needs_url_base(field_map: dict[str, str], sample_items: list[dict]) -> bool:
    """True if the mapped url value is root-relative ("/inventory/…") and so
    needs scrape_config.url_base to become a real link."""
    from ...adapters.json_api import _dig

    src = field_map.get("url")
    if not src:
        return False
    for item in sample_items:
        val = _dig(item, src)
        if isinstance(val, str) and val.startswith("/"):
            return True
        if isinstance(val, list) and val and isinstance(val[0], str) and val[0].startswith("/"):
            return True
    return False
