"""FakePage-based regression tests for BrowserAdapter's pagination loops.

No real Playwright/browser needed for most of these — a FakePage stands in
for Playwright's sync Page API, scripted with a sequence of DOM "snapshots"
so stop conditions and wait semantics can be asserted without a live site.
`_iter_scroll`/`_iter_url`/`_iter_click` take a bare `page` argument, so they
are exercised directly; only the retry-backoff test needs the full
`iter_vehicles()` orchestration, which additionally mocks `sync_playwright`.

This is the first pagination-path coverage this adapter has had — before
Phase 1, only the pure-function parsers (test_browser_parse.py) were tested.

Run:  python -m tests.test_browser_pagination
"""
from __future__ import annotations

from unittest.mock import patch

from dealer_scraper.adapters.browser import BrowserAdapter, _COLLECT_JS, _JSONLD_JS
from dealer_scraper.adapters.waits import _COUNT_JS, _GROWTH_JS

BASE_CFG = {
    "list_url": "https://x.test/cars",
    "base_url": "https://x.test",
    "card_anchor": "a[href*='/vehicles/']",
    "wait_selector": "a[href*='/vehicles/']",
    "settle_ms": 20,  # small cap: real time.sleep is monkeypatched out below anyway
}


def _card(n: int) -> dict:
    return {"href": f"/vehicles/{n}",
            "text": f"2020 Make{n} Model{n} • 10,000 km $20,000"}


class FakeMouse:
    def __init__(self, page):
        self._page = page

    def wheel(self, dx, dy):
        self._page.scroll_index = min(self._page.scroll_index + 1, len(self._page.rounds) - 1)


class FakeElement:
    def __init__(self, page, enabled: bool, visible: bool = True):
        self._page = page
        self._enabled = enabled
        self._visible = visible

    def is_enabled(self):
        return self._enabled

    def is_visible(self):
        return self._visible

    def click(self, timeout: float | None = None):
        # Playwright's ElementHandle.click takes a timeout; the adapter passes a
        # short one so a control that vanishes mid-click ends pagination instead
        # of burning the 30s actionability default and failing the whole run.
        self._page.click_index += 1


class FakePage:
    """Two pagination shapes, chosen by which kwarg is supplied:
      * `rounds`      — cumulative card lists per scroll/click round (index 0 =
                         initial state). Drives scroll_index / click_index.
      * `url_pages`    — {page_number: card_list}, keyed by the `page=N` query
                         param `goto()` is called with. Drives paginate="url".
    """

    def __init__(self, rounds=None, url_pages=None, next_button_rounds=None,
                 next_button_visible_rounds=None):
        self.rounds = rounds or [[]]
        self.url_pages = url_pages or {}
        self.scroll_index = 0
        self.click_index = 0
        self.current_url_page = 1
        self.next_button_rounds = next_button_rounds
        self.next_button_visible_rounds = next_button_visible_rounds
        self.mouse = FakeMouse(self)
        self.url = "https://x.test/cars"
        self.goto_calls: list[str] = []
        self.wait_for_timeout_calls: list[int] = []
        self._collect_seen: set[str] = set()

    # -- navigation ---------------------------------------------------
    def goto(self, url, wait_until=None, timeout=None):
        self.goto_calls.append(url)
        if "page=" in url:
            self.current_url_page = int(url.rsplit("page=", 1)[1])
        self._collect_seen = set()  # a real navigation drops any window state

    def wait_for_selector(self, selector, timeout=None):
        return None

    def wait_for_load_state(self, state=None, timeout=None):
        return None

    def wait_for_timeout(self, ms):
        self.wait_for_timeout_calls.append(ms)

    def title(self):
        return "Fake"

    def text_content(self, selector):
        return ""

    def screenshot(self, path=None, full_page=False):
        return None

    def query_selector(self, selector):
        if self.next_button_rounds is None:
            return None
        idx = self.click_index
        enabled = self.next_button_rounds[idx] if idx < len(self.next_button_rounds) else False
        visible = True
        if self.next_button_visible_rounds is not None:
            visible = (self.next_button_visible_rounds[idx]
                      if idx < len(self.next_button_visible_rounds) else False)
        return FakeElement(self, enabled, visible)

    # -- the surface waits.py / browser.py actually drive --------------
    def _current_cards(self) -> list[dict]:
        if self.url_pages:
            return self.url_pages.get(self.current_url_page, [])
        idx = self.click_index if self.next_button_rounds is not None else self.scroll_index
        return self.rounds[min(idx, len(self.rounds) - 1)]

    def evaluate(self, js, arg=None):
        if js == _COLLECT_JS:
            incremental = arg.get("incremental")
            out = []
            for c in self._current_cards():
                if incremental and c["href"] in self._collect_seen:
                    continue
                if incremental:
                    self._collect_seen.add(c["href"])
                out.append(c)
            return out
        if js == _JSONLD_JS:
            return []
        if js == _COUNT_JS:
            return len(self._current_cards())
        if js == _GROWTH_JS:
            _sel, n = arg
            return len(self._current_cards()) > n
        raise AssertionError(f"unexpected evaluate() call: {js[:80]!r}")

    def wait_for_function(self, js, arg=None, timeout=None):
        if js == _GROWTH_JS:
            if self.evaluate(js, arg):
                return None
            raise TimeoutError("no growth within cap")  # mirrors real Playwright timeout
        raise AssertionError(f"unexpected wait_for_function() call: {js[:80]!r}")


def _hrefs(vehicles) -> list[str]:
    return [v.url.rsplit("/", 1)[-1] for v in vehicles]


# --------------------------------------------------------------------- tests
def test_url_mode_stops_after_two_empty_pages():
    page = FakePage(url_pages={
        1: [_card(0), _card(1)],
        2: [_card(2)],
        3: [],
        4: [],
        5: [_card(99)],  # must never be reached — stopped at page 4
    })
    adapter = BrowserAdapter({**BASE_CFG, "paginate": "url",
                              "page_url": "https://x.test/cars?page={page}",
                              "max_pages": 10})
    seen: set[str] = set()
    got = list(adapter._iter_url(page, seen))
    assert sorted(_hrefs(got)) == ["0", "1", "2"], got
    # An empty page is loaded twice — once more before it is believed — so each
    # of the two empty pages appears twice, and page 5 is still never reached.
    assert page.goto_calls == [
        "https://x.test/cars?page=2",
        "https://x.test/cars?page=3", "https://x.test/cars?page=3",
        "https://x.test/cars?page=4", "https://x.test/cars?page=4",
    ], page.goto_calls
    print("  url mode stops after 2 consecutive empty pages, never overruns OK")


def test_scroll_stagnation_exits_without_full_budget():
    growing = [[_card(0)], [_card(0), _card(1)], [_card(0), _card(1), _card(2)]]
    rounds = growing + [growing[-1]] * 5  # then nothing new, ever
    page = FakePage(rounds=rounds)
    adapter = BrowserAdapter({**BASE_CFG, "paginate": "scroll", "scroll_rounds": 40})
    seen: set[str] = set()
    got = list(adapter._iter_scroll(page, seen))
    assert sorted(_hrefs(got)) == ["0", "1", "2"], got
    # 3 growing rounds + 3 stagnant rounds to confirm -> stopped well short of 40.
    assert page.scroll_index <= 6, page.scroll_index
    assert page.wait_for_timeout_calls == [], (
        "scroll settle must go through wait_for_growth(), never a fixed sleep")
    print("  scroll stagnation (3 dead rounds) exits well short of scroll_rounds cap, "
          "zero fixed sleeps OK")


def test_incremental_collect_returns_only_new_records():
    page = FakePage(rounds=[[_card(0), _card(1)], [_card(0), _card(1), _card(2)]])
    adapter = BrowserAdapter({**BASE_CFG, "paginate": "scroll"})
    first = adapter._collect(page, incremental=True)
    assert sorted(r["href"] for r in first) == ["/vehicles/0", "/vehicles/1"], first
    second = adapter._collect(page, incremental=True)  # same round, nothing new yet
    assert second == [], second
    page.scroll_index = 1
    third = adapter._collect(page, incremental=True)
    assert [r["href"] for r in third] == ["/vehicles/2"], third
    print("  incremental collect returns only records not already seen this session OK")


def test_click_mode_stops_on_growth_failure():
    rounds = [[_card(0)], [_card(0), _card(1)], [_card(0), _card(1)]]  # 3rd click: no growth
    page = FakePage(rounds=rounds, next_button_rounds=[True, True, True])
    adapter = BrowserAdapter({**BASE_CFG, "paginate": "click",
                              "next_selector": "button.next", "max_pages": 20})
    seen: set[str] = set()
    got = list(adapter._iter_click(page, seen))
    assert sorted(_hrefs(got)) == ["0", "1"], got
    assert page.click_index == 2, page.click_index  # clicked twice, 3rd click's growth check failed
    print("  click mode stops once a click yields no new cards within settle_ms, "
          "not just on a disabled/missing button OK")


def test_click_mode_stops_when_button_disappears():
    rounds = [[_card(0)], [_card(0), _card(1)]]
    page = FakePage(rounds=rounds, next_button_rounds=[True, False])
    adapter = BrowserAdapter({**BASE_CFG, "paginate": "click",
                              "next_selector": "button.next", "max_pages": 20})
    seen: set[str] = set()
    got = list(adapter._iter_click(page, seen))
    assert sorted(_hrefs(got)) == ["0", "1"], got
    print("  click mode still stops immediately when the button disables/disappears OK")


def test_click_mode_stops_when_button_is_enabled_but_invisible():
    """Real bug found live-testing against cardora.ca: a 'no more results'
    state left the Load More button in the DOM, still enabled, but CSS-
    hidden. is_enabled()-only used to click it anyway, burning Playwright's
    full 30s actionability-wait retrying against an invisible element."""
    rounds = [[_card(0)], [_card(0), _card(1)]]
    page = FakePage(rounds=rounds, next_button_rounds=[True, True],
                    next_button_visible_rounds=[True, False])
    adapter = BrowserAdapter({**BASE_CFG, "paginate": "click",
                              "next_selector": "button.next", "max_pages": 20})
    seen: set[str] = set()
    got = list(adapter._iter_click(page, seen))
    assert sorted(_hrefs(got)) == ["0", "1"], got
    assert page.click_index == 1, page.click_index  # never attempted the click on the hidden button
    print("  click mode stops on an enabled-but-invisible button instead of clicking it OK")


def test_retry_backoff_never_sleeps_after_the_final_failed_attempt():
    """browser.py:349 used to sleep 3000+attempt*2000 ms even after the LAST
    failed attempt, right before raising — pure waste on the failure path."""
    page = FakePage(rounds=[[]])

    class _NeverFound(FakePage):
        def wait_for_selector(self, selector, timeout=None):
            raise TimeoutError("never appears")

    never_page = _NeverFound(rounds=[[]])

    class FakeContext:
        def add_init_script(self, js):
            pass

        def new_page(self):
            return never_page

        def route(self, pattern, handler):
            pass

        def unroute(self, pattern, handler=None):
            pass

    class FakeBrowser:
        def new_context(self, **kw):
            return FakeContext()

        def close(self):
            pass

    class FakeChromium:
        def launch(self, **kw):
            return FakeBrowser()

    class FakePlaywright:
        chromium = FakeChromium()

    class FakeSyncPlaywrightCM:
        def __enter__(self):
            return FakePlaywright()

        def __exit__(self, *a):
            return False

    adapter = BrowserAdapter({**BASE_CFG, "load_retries": 3, "block_resources": False})
    with patch("playwright.sync_api.sync_playwright", lambda: FakeSyncPlaywrightCM()):
        try:
            list(adapter.iter_vehicles())
            raise AssertionError("expected RuntimeError: wait_selector never appeared")
        except RuntimeError as exc:
            assert "never appeared after 3 tries" in str(exc), exc
    # retries=3 -> backoff sleeps after attempts 1 and 2 only, NOT after 3.
    assert never_page.wait_for_timeout_calls == [5000, 7000], never_page.wait_for_timeout_calls
    print("  retry backoff sleeps retries-1 times, never after the final failed attempt OK")


def main() -> None:
    with patch("dealer_scraper.adapters.waits.time.sleep", lambda s: None):
        test_url_mode_stops_after_two_empty_pages()
        test_scroll_stagnation_exits_without_full_budget()
        test_incremental_collect_returns_only_new_records()
        test_click_mode_stops_on_growth_failure()
        test_click_mode_stops_when_button_disappears()
        test_click_mode_stops_when_button_is_enabled_but_invisible()
        test_retry_backoff_never_sleeps_after_the_final_failed_attempt()
    print("BROWSER PAGINATION TESTS PASSED")


if __name__ == "__main__":
    main()
