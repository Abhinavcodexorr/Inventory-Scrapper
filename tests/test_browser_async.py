"""FakeAsyncPage-based regression tests for AsyncBrowserAdapter.

Mocks the full `playwright.async_api.async_playwright()` entry point so the
whole producer-thread / bridge / parallel-fetch / backoff machinery runs for
real (just against a fake site) — no live browser needed.

Run:  python -m tests.test_browser_async
"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, patch

from dealer_scraper.adapters.browser import _JSONLD_JS
from dealer_scraper.adapters.browser_async import AsyncBrowserAdapter, _COLLECT_JS, _MAX_PAGE_JS
from dealer_scraper.adapters.waits import _COUNT_JS, _GROWTH_JS

BASE_CFG = {
    "list_url": "https://x.test/cars",
    "base_url": "https://x.test",
    "card_anchor": "a[href*='/vehicles/']",
    "wait_selector": "a[href*='/vehicles/']",
    "settle_ms": 20,
    "block_resources": False,
}


def _card(n: int) -> dict:
    return {"href": f"/vehicles/{n}",
            "text": f"2020 Make{n} Model{n} • 10,000 km $20,000"}


# --------------------------------------------------------------- fake site
class FakeSite:
    """Shared state every fake page/context in one test run reads from."""

    def __init__(self, pages: dict, control_max: int = 0, block_pages=frozenset(),
                 always_block_pages=frozenset(), first_page_never_loads: bool = False,
                 page_delay: float = 0.0):
        self.pages = pages
        self.control_max = control_max
        self.block_pages = set(block_pages)          # 403 once, then recovers on retry
        self.always_block_pages = set(always_block_pages)  # 403 forever
        self._blocked_once: set[int] = set()
        self.first_page_never_loads = first_page_never_loads
        self.page_delay = page_delay
        self.in_flight = 0
        self.max_in_flight = 0
        self.fetched: list[int] = []

    def cards_for(self, n: int) -> list:
        return self.pages.get(n, [])

    def status_for(self, n: int) -> int:
        self.fetched.append(n)
        if n in self.always_block_pages:
            return 403
        if n in self.block_pages and n not in self._blocked_once:
            self._blocked_once.add(n)
            return 403
        return 200


# --------------------------------------------------------------- fake playwright
class FakeMouse:
    async def wheel(self, dx, dy):
        return None


class FakeAsyncPage:
    def __init__(self, site: FakeSite):
        self.site = site
        self.current_page = 1
        self.url = "https://x.test/cars"
        self.mouse = FakeMouse()

    async def goto(self, url, wait_until=None, timeout=None):
        n = 1
        if "page=" in url:
            n = int(url.rsplit("page=", 1)[1])
        self.current_page = n
        self.url = url
        self.site.in_flight += 1
        self.site.max_in_flight = max(self.site.max_in_flight, self.site.in_flight)
        if self.site.page_delay:
            await asyncio.sleep(self.site.page_delay)
        self.site.in_flight -= 1

        class _Resp:
            status = self.site.status_for(n)
        return _Resp()

    async def wait_for_load_state(self, state=None, timeout=None):
        return None

    async def wait_for_selector(self, selector, timeout=None):
        if self.site.first_page_never_loads:
            raise TimeoutError("never appears")
        return None

    async def wait_for_function(self, js, arg=None, timeout=None):
        if js == _GROWTH_JS:
            sel, n = arg
            if len(self.site.cards_for(self.current_page)) > n:
                return None
            raise TimeoutError("no growth")
        raise AssertionError(f"unexpected wait_for_function: {js[:60]!r}")

    async def evaluate(self, js, arg=None):
        if js == _COLLECT_JS:
            return list(self.site.cards_for(self.current_page))
        if js == _JSONLD_JS:
            return []
        if js == _COUNT_JS:
            return len(self.site.cards_for(self.current_page))
        if js == _MAX_PAGE_JS:
            return self.site.control_max
        raise AssertionError(f"unexpected evaluate: {js[:60]!r}")

    async def text_content(self, selector):
        return ""

    async def title(self):
        return "Fake"

    async def screenshot(self, path=None, full_page=False):
        return None

    async def query_selector(self, selector):
        return None

    async def close(self):
        return None


class FakeAsyncContext:
    def __init__(self, site: FakeSite):
        self.site = site

    async def add_init_script(self, js):
        return None

    async def new_page(self):
        return FakeAsyncPage(self.site)

    async def route(self, pattern, handler):
        return None

    async def unroute(self, pattern, handler=None):
        return None


class FakeAsyncBrowser:
    def __init__(self, site: FakeSite):
        self.site = site

    async def new_context(self, **kw):
        return FakeAsyncContext(self.site)

    async def close(self):
        return None


class FakeChromium:
    def __init__(self, site: FakeSite):
        self.site = site

    async def launch(self, **kw):
        return FakeAsyncBrowser(self.site)


class FakePlaywrightObj:
    def __init__(self, site: FakeSite):
        self.chromium = FakeChromium(site)


class FakeAsyncPlaywrightCM:
    def __init__(self, site: FakeSite):
        self.site = site

    async def __aenter__(self):
        return FakePlaywrightObj(self.site)

    async def __aexit__(self, *a):
        return False


def _run_adapter(adapter: AsyncBrowserAdapter, site: FakeSite):
    """Drives iter_vehicles() to completion (or exception) with the
    playwright.async_api entry point mocked out."""
    vehicles = []
    error = None
    with patch("playwright.async_api.async_playwright", lambda: FakeAsyncPlaywrightCM(site)):
        try:
            for v in adapter.iter_vehicles():
                vehicles.append(v)
        except Exception as exc:
            error = exc
    return vehicles, error


def _hrefs(vehicles) -> list[str]:
    return sorted(v.url.rsplit("/", 1)[-1] for v in vehicles)


# --------------------------------------------------------------------- tests
def test_parallel_url_pagination_yields_each_vehicle_once():
    pages = {i: [_card(i * 10 + j) for j in range(3)] for i in range(1, 11)}
    site = FakeSite(pages, control_max=10, page_delay=0.05)
    cfg = {**BASE_CFG, "paginate": "url", "page_url": "https://x.test/cars?page={page}",
          "max_pages": 20, "fetch_concurrency": 4}
    adapter = AsyncBrowserAdapter(cfg)
    vehicles, err = _run_adapter(adapter, site)
    assert err is None, err
    expected = sorted(str(i * 10 + j) for i in range(1, 11) for j in range(3))
    assert _hrefs(vehicles) == expected, (len(vehicles), len(expected))
    assert site.max_in_flight >= 2, (
        f"expected overlapping fetches at concurrency=4, saw max {site.max_in_flight} in flight")
    print(f"  10 pages @ concurrency 4 -> every vehicle exactly once, max {site.max_in_flight} "
          f"concurrent fetches observed OK")


def test_producer_exception_propagates():
    site = FakeSite({1: []}, first_page_never_loads=True)
    cfg = {**BASE_CFG, "paginate": "url", "page_url": "https://x.test/cars?page={page}",
          "load_retries": 2}
    adapter = AsyncBrowserAdapter(cfg)
    with patch("asyncio.sleep", AsyncMock(return_value=None)):
        vehicles, err = _run_adapter(adapter, site)
    assert vehicles == [], vehicles
    assert isinstance(err, RuntimeError), err
    assert "never appeared after 2 tries" in str(err), err
    print("  a page that never renders raises RuntimeError out of iter_vehicles() OK")


def test_generator_close_stops_producer_promptly():
    pages = {i: [_card(i)] for i in range(1, 51)}
    site = FakeSite(pages, control_max=50, page_delay=0.05)
    cfg = {**BASE_CFG, "paginate": "url", "page_url": "https://x.test/cars?page={page}",
          "max_pages": 50, "fetch_concurrency": 2}
    adapter = AsyncBrowserAdapter(cfg)
    with patch("playwright.async_api.async_playwright", lambda: FakeAsyncPlaywrightCM(site)):
        gen = adapter.iter_vehicles()
        next(gen)  # pull the first (page-1) vehicle, forcing the producer to actually start
        t0 = time.monotonic()
        gen.close()
        elapsed = time.monotonic() - t0
    assert elapsed < 5, f"generator.close() took {elapsed:.2f}s to stop the producer"
    print(f"  closing the generator mid-stream stops the producer within {elapsed:.2f}s "
          f"(well under the 50 remaining pages' worth of work) OK")


def test_backoff_recovers_a_blocked_page_via_retry():
    pages = {i: [_card(i)] for i in range(1, 9)}
    site = FakeSite(pages, control_max=8, block_pages={3})
    cfg = {**BASE_CFG, "paginate": "url", "page_url": "https://x.test/cars?page={page}",
          "max_pages": 8, "fetch_concurrency": 4}
    adapter = AsyncBrowserAdapter(cfg)
    vehicles, err = _run_adapter(adapter, site)
    assert err is None, err
    assert _hrefs(vehicles) == [str(i) for i in range(1, 9)], _hrefs(vehicles)
    assert 3 in site.fetched, "page 3 should have been attempted at least once"
    print("  a 403'd page is retried once after backoff and its vehicles are still collected OK")


def test_majority_block_fails_the_run_instead_of_under_reporting():
    pages = {i: [_card(i)] for i in range(1, 9)}
    # 5 of 7 fanned-out pages (2..8) permanently blocked -> majority failure,
    # surviving even the one retry pass.
    site = FakeSite(pages, control_max=8, always_block_pages={2, 3, 4, 5, 6})
    cfg = {**BASE_CFG, "paginate": "url", "page_url": "https://x.test/cars?page={page}",
          "max_pages": 8, "fetch_concurrency": 4}
    adapter = AsyncBrowserAdapter(cfg)
    vehicles, err = _run_adapter(adapter, site)
    assert err is not None, "expected the run to fail rather than silently return partial data"
    print("  a majority of pages blocked even after retry fails the run visibly OK")


def test_backpressure_does_not_deadlock_with_a_slow_consumer():
    pages = {i: [_card(i * 10 + j) for j in range(5)] for i in range(1, 21)}  # 100 vehicles
    site = FakeSite(pages, control_max=20)
    cfg = {**BASE_CFG, "paginate": "url", "page_url": "https://x.test/cars?page={page}",
          "max_pages": 20, "fetch_concurrency": 4}
    adapter = AsyncBrowserAdapter(cfg)
    got = 0
    with patch("playwright.async_api.async_playwright", lambda: FakeAsyncPlaywrightCM(site)):
        for _v in adapter.iter_vehicles():
            got += 1
            time.sleep(0.01)  # slow consumer — producer's queue.put() must throttle, not block forever
    assert got == 100, got
    print("  a slow consumer throttles the producer via the bounded queue without deadlocking OK")


def main() -> None:
    test_parallel_url_pagination_yields_each_vehicle_once()
    test_producer_exception_propagates()
    test_generator_close_stops_producer_promptly()
    test_backoff_recovers_a_blocked_page_via_retry()
    test_majority_block_fails_the_run_instead_of_under_reporting()
    test_backpressure_does_not_deadlock_with_a_slow_consumer()
    print("BROWSER ASYNC TESTS PASSED")


if __name__ == "__main__":
    main()
