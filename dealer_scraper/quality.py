"""Plausibility checks on scraped vehicle values.

A config whose field_map points at the wrong JSON path does not fail — it reads
a boolean or a flag where the make should be. coliseumauto.ca mapped
`vehicle_site_detail.make`, which is the literal `true`, so 124 "vehicles" were
stored with make "True", model "True" and year 1. Counting rows reports that as
a healthy run; asking whether a value could describe a car does not.
"""
from __future__ import annotations

import re
from datetime import date

_JUNK_TEXT = frozenset({"true", "false", "none", "null", "nan", "undefined", "n/a", "na", "-", "0", "1"})
_VIN_RE = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")


def is_plausible_year(year: object) -> bool:
    return isinstance(year, int) and not isinstance(year, bool) and 1950 <= year <= date.today().year + 2


def is_plausible_text(value: object) -> bool:
    """Free text (make/model) that is a real word, not a flag or placeholder."""
    return isinstance(value, str) and value.strip().lower() not in _JUNK_TEXT and bool(value.strip())


def is_valid_vin(vin: object) -> bool:
    return isinstance(vin, str) and bool(_VIN_RE.match(vin.strip().upper()))


def is_described(v) -> bool:
    """True when the vehicle carries at least one value that could really describe
    a car. Replaces a bare truthiness test, under which year=1 and make="True"
    both counted as 'described'."""
    return (
        is_plausible_year(v.year)
        or is_plausible_text(v.make)
        or is_plausible_text(v.model)
        or (v.price is not None and v.price > 0)
    )
