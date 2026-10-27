"""In-memory adapter — yields a fixed list of vehicles, no network.

Two construction paths, both supported:
  * tests:    StubAdapter([{...}, {...}])            — a plain list of raw dicts
  * registry: build_adapter("stub", scrape_config)   — reads scrape_config["items"]

Used by the seeded sample dealers (website_provider="SampleData") so the full
Create -> Sync -> Inventory flow can be exercised locally without hitting a live
site, and by tests.
"""
from __future__ import annotations

import time
from typing import Iterator

from ..normalize import NormalizedVehicle, normalize


class StubAdapter:
    mode = "stub"

    def __init__(self, config: list[dict] | dict | None = None):
        if isinstance(config, dict):
            items = config.get("items") or []
            # Optional per-item pause — lets a demo/test observe a sync that is
            # still 'running' (progress bar, duplicate-sync guard).
            self._delay = float(config.get("delay_seconds") or 0)
        else:
            items = config or []
            self._delay = 0.0
        self._items: list[dict] = list(items)

    def fetch_total_count(self) -> int | None:
        return len(self._items)

    def iter_vehicles(self) -> Iterator[NormalizedVehicle]:
        for it in self._items:
            if self._delay:
                time.sleep(self._delay)
            data = dict(it)
            data["_raw"] = dict(it)
            yield normalize(data)
