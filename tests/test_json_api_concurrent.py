"""Regression tests for JsonApiAdapter's concurrent pagination (Phase 3).

Uses httpx.MockTransport so no real network is touched — the adapter's own
httpx.Client is swapped for one built on the mock transport after
construction, keeping every other code path (tenacity retry, _Pacer,
_RateGate, field mapping) exactly as production runs it.

Run:  python -m tests.test_json_api_concurrent
"""
from __future__ import annotations

import threading
import time
from unittest.mock import patch

import httpx

from dealer_scraper.adapters.json_api import JsonApiAdapter

BASE_CFG = {
    "endpoint": "https://x.test/api?page={page}&limit={page_size}",
    "list_path": "results.vehicles",
    "total_path": "results.total",
    "field_map": {"external_id": "id", "make": "make", "model": "model",
                  "year": "year", "price": "price"},
    "page_size": 5,
    "delay_seconds": 0.0,
}


def _item(page: int, idx: int) -> dict:
    return {"id": f"{page}-{idx}", "make": "Honda", "model": "Civic", "year": 2020, "price": 20000}


class _Concurrency:
    """Tracks how many mock requests were in flight at once."""

    def __init__(self):
        self._lock = threading.Lock()
        self.in_flight = 0
        self.max_in_flight = 0

    def enter(self):
        with self._lock:
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)

    def exit(self):
        with self._lock:
            self.in_flight -= 1


def make_handler(pages: dict, total: int | None, *, request_delay: float = 0.0,
                 fetch_log: list | None = None, concurrency: _Concurrency | None = None,
                 status_overrides: dict | None = None):
    """status_overrides: {page: n} -> that page returns 429 for its first n
    requests (across ALL retries, including a worker's own manual retry),
    then 200 from the (n+1)th request on."""
    status_overrides = status_overrides or {}
    request_counts: dict[int, int] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params.get("page", "1"))
        request_counts[page] = request_counts.get(page, 0) + 1
        if fetch_log is not None:
            fetch_log.append((time.monotonic(), page))
        if concurrency is not None:
            concurrency.enter()
        if request_delay:
            time.sleep(request_delay)
        if concurrency is not None:
            concurrency.exit()

        limit = status_overrides.get(page, 0)
        if request_counts[page] <= limit:
            return httpx.Response(429, json={"error": "rate limited"})

        body = {"results": {"vehicles": pages.get(page, [])}}
        if total is not None:
            body["results"]["total"] = total
        return httpx.Response(200, json=body)

    handler.request_counts = request_counts
    return handler


def _adapter(cfg: dict, handler) -> JsonApiAdapter:
    a = JsonApiAdapter(cfg)
    a._client = httpx.Client(transport=httpx.MockTransport(handler))
    return a


# --------------------------------------------------------------------- tests
def test_known_total_fetches_remaining_pages_concurrently():
    pages = {i: [_item(i, j) for j in range(5)] for i in range(1, 5)}  # 4 pages x 5 = 20
    conc = _Concurrency()
    handler = make_handler(pages, total=20, request_delay=0.05, concurrency=conc)
    adapter = _adapter({**BASE_CFG, "fetch_concurrency": 4}, handler)
    vehicles = list(adapter.iter_vehicles())
    assert len(vehicles) == 20, len(vehicles)
    assert conc.max_in_flight >= 2, (
        f"expected overlapping fetches at concurrency=4, saw max {conc.max_in_flight} in flight")
    print(f"  known total (20 items/4 pages) -> all 20 fetched, max {conc.max_in_flight} "
          f"concurrent requests observed OK")


def test_pacer_enforces_minimum_spacing_between_request_starts():
    pages = {i: [_item(i, 0)] for i in range(1, 6)}
    fetch_log: list = []
    handler = make_handler(pages, total=5, request_delay=0.02, fetch_log=fetch_log)
    adapter = _adapter({**BASE_CFG, "page_size": 1, "fetch_concurrency": 4,
                        "delay_seconds": 0.08}, handler)
    vehicles = list(adapter.iter_vehicles())
    assert len(vehicles) == 5, len(vehicles)
    starts = sorted(t for t, _p in fetch_log)
    gaps = [b - a for a, b in zip(starts, starts[1:])]
    assert all(g >= 0.08 - 0.015 for g in gaps), (
        f"pacer should space every request start by >= delay_seconds regardless of "
        f"concurrency; gaps={gaps}")
    print("  _Pacer spaces every request START by >= delay_seconds even with "
          "fetch_concurrency=4 (politeness rate unchanged) OK")


def test_unknown_total_stops_at_first_short_page():
    pages = {
        1: [_item(1, j) for j in range(5)],
        2: [_item(2, j) for j in range(5)],
        3: [_item(3, j) for j in range(5)],
        4: [_item(4, j) for j in range(2)],   # short page -> the real end
        5: [_item(5, 0)],                     # must NEVER be yielded despite being fetched
    }
    handler = make_handler(pages, total=None)  # no total field -> unknown-total path
    adapter = _adapter({**BASE_CFG, "fetch_concurrency": 4}, handler)
    vehicles = list(adapter.iter_vehicles())
    assert len(vehicles) == 17, len(vehicles)  # 5+5+5+2, page 5's item excluded
    stats = adapter.timer.snapshot()
    assert stats["counters"].get("speculative_pages_wasted", 0) >= 1, stats["counters"]
    print("  unknown-total windowed fetch stops exactly at the short page 4, "
          "page 5's speculative fetch discarded (not yielded) and counted as wasted OK")


def test_persistent_429_triggers_backoff_and_recovers_via_retry():
    pages = {1: [_item(1, 0)], 2: [_item(2, 0)], 3: [_item(3, 0)]}  # page 4+ empty -> stop
    handler = make_handler(pages, total=None, status_overrides={2: 4})  # blocked its first 4 tries
    adapter = _adapter({**BASE_CFG, "page_size": 1, "fetch_concurrency": 2,
                        "delay_seconds": 0.0}, handler)
    with patch("time.sleep", lambda s: None):  # skip tenacity's + the pacer's real waits
        vehicles = list(adapter.iter_vehicles())
    assert len(vehicles) == 3, len(vehicles)
    assert handler.request_counts[2] == 5, handler.request_counts  # 4 blocked + 1 that succeeded
    print("  a page stuck at 429 through all of tenacity's own retries is caught by the "
          "backoff gate and recovered with one more (now-serialized) retry OK")


def test_majority_429_fails_the_run_instead_of_under_reporting():
    pages = {i: [_item(i, 0)] for i in range(1, 5)}
    # pages 2-4 permanently blocked (limit=999 effectively forever within the test).
    handler = make_handler(pages, total=4, status_overrides={2: 999, 3: 999, 4: 999})
    adapter = _adapter({**BASE_CFG, "page_size": 1, "fetch_concurrency": 4}, handler)
    with patch("time.sleep", lambda s: None):
        try:
            list(adapter.iter_vehicles())
            raised = False
        except Exception:
            raised = True
    assert raised, "a majority of pages failing twice should fail the run, not silently under-report"
    print("  a majority of pages still 429ing after the retry pass fails the run visibly OK")


def main() -> None:
    test_known_total_fetches_remaining_pages_concurrently()
    test_pacer_enforces_minimum_spacing_between_request_starts()
    test_unknown_total_stops_at_first_short_page()
    test_persistent_429_triggers_backoff_and_recovers_via_retry()
    test_majority_429_fails_the_run_instead_of_under_reporting()
    print("JSON API CONCURRENT TESTS PASSED")


if __name__ == "__main__":
    main()
