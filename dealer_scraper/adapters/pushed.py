"""An adapter whose vehicles were scraped somewhere else and pushed to us.

Some dealer sites only answer from a particular country (mostwantedcars.ca is
Canada-only), and the server is not in that country. The scrape then runs on a
machine that can reach the site, and POSTs the finished rows to
`/api/dealers/{id}/ingest`. This adapter hands those rows to the normal engine
so they go through exactly the same matching, price history, removal safety
gate and run bookkeeping as a scrape the server did itself.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Iterator

from ..normalize import NormalizedVehicle, parse_date

_FIELDS = set(NormalizedVehicle.__dataclass_fields__)
_MONEY = ("price", "original_price")


def _to_vehicle(row: dict) -> NormalizedVehicle:
    data = {k: v for k, v in row.items() if k in _FIELDS}
    for key in _MONEY:
        if data.get(key) is not None:
            try:
                data[key] = Decimal(str(data[key]))
            except InvalidOperation:
                data[key] = None
    # Arrives as ISO text over JSON; the dataclass wants a date.
    data["stock_date"] = parse_date(data.get("stock_date"))
    data.setdefault("external_id", None)
    return NormalizedVehicle(**data)


class PushedAdapter:
    mode = "pushed"
    REQUIRED_CONFIG: tuple[str, ...] = ()

    def __init__(self, rows: list[dict], reported_total: int | None) -> None:
        self._rows = rows
        self._total = reported_total

    def fetch_total_count(self) -> int | None:
        return self._total

    def iter_vehicles(self) -> Iterator[NormalizedVehicle]:
        for row in self._rows:
            yield _to_vehicle(row)
