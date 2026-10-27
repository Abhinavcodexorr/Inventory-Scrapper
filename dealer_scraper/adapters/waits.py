"""Bounded, condition-based waits that replace the old fixed `wait_for_timeout`
sleeps in the browser adapter (dealer_scraper/adapters/browser.py).

Every wait here degrades gracefully: `wait_for_function` is tried first (fast
and event-driven — Playwright resolves it the instant the DOM condition goes
true, not on some polling tick); if that raises for any reason (an odd CSP, a
navigating/destroyed execution context, an old Chromium build) we fall back to
a manual `page.evaluate()` poll bounded by the exact same cap, so a wait here
never sleeps longer than the caller's `cap_ms` regardless of which path it
takes. `cap_ms` is a ceiling, not a target — on an already-settled page these
return in one or two poll ticks, not the full cap.
"""
from __future__ import annotations

import asyncio
import time
from typing import Awaitable, Callable

_COUNT_JS = "(sel) => document.querySelectorAll(sel).length"
_GROWTH_JS = "([sel, n]) => document.querySelectorAll(sel).length > n"


def _count(page, selector: str) -> int:
    try:
        return int(page.evaluate(_COUNT_JS, selector))
    except Exception:
        return -1  # execution context gone / mid-navigation — "can't tell", not zero


def _poll_until(predicate: Callable[[], bool], cap_ms: int, poll_ms: int = 150) -> bool:
    deadline = time.monotonic() + cap_ms / 1000
    while True:
        try:
            if predicate():
                return True
        except Exception:
            pass
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(poll_ms / 1000, remaining))


def wait_for_growth(page, selector: str, prev_count: int, cap_ms: int) -> bool:
    """True if `selector`'s match count grows past `prev_count` within `cap_ms`.

    A timeout (False) is the caller's stagnation signal, not an error — e.g. an
    infinite-scroll loop uses it directly to decide whether the last scroll
    surfaced anything new.
    """
    try:
        page.wait_for_function(_GROWTH_JS, arg=[selector, prev_count], timeout=cap_ms)
        return True
    except Exception:
        pass
    return _poll_until(lambda: _count(page, selector) > prev_count, cap_ms)


_MATCH_COUNT_JS = """
({sel, pattern}) => {
  const re = new RegExp(pattern);
  let n = 0;
  for (const a of document.querySelectorAll(sel)) {
    const h = a.getAttribute('href') || '';
    if (re.test(h)) n++;
  }
  return n;
}
"""


def count_matching(page, selector: str, pattern: str) -> int:
    """How many `selector` links have an href matching `pattern` (the config's
    id_regex). Unlike a bare selector count this cannot be satisfied by the
    site's own navigation."""
    try:
        return int(page.evaluate(_MATCH_COUNT_JS, {"sel": selector, "pattern": pattern}))
    except Exception:
        return -1


def wait_for_vehicles(page, selector: str, pattern: str, cap_ms: int,
                      min_count: int = 1, poll_ms: int = 200,
                      quiet_polls: int = 2) -> int:
    """Wait until real vehicle links are present AND their count has settled.

    Why this exists: `wait_count_stable(anchor)` is satisfied by ANY element
    matching the anchor — and on a dealer site the header nav almost always
    contains a link on the listing path ("Inventory"), which is in the initial
    HTML and never changes. So the count is "stable" at ~10 within 400ms, the
    scraper collects, every nav link fails id_regex, and the run reports zero
    vehicles with no error. Observed live on grcars.ca.

    Counting only hrefs that match id_regex removes that false positive: nav
    links cannot match a detail-page pattern, so a non-zero count is proof the
    grid actually rendered.

    Returns the final matching count — 0 means genuinely nothing was found
    within the cap, which callers must treat as a failure, not an empty lot.
    """
    deadline = time.monotonic() + cap_ms / 1000
    last = -2
    quiet = 0
    count = count_matching(page, selector, pattern)
    while True:
        # Settling only counts once we actually have vehicles; a steady zero is
        # "still loading", not "stable".
        if count >= min_count and count == last:
            quiet += 1
            if quiet >= quiet_polls:
                return count
        else:
            quiet = 0
        last = count
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return count
        time.sleep(min(poll_ms / 1000, remaining))
        count = count_matching(page, selector, pattern)


def wait_count_stable(page, selector: str, cap_ms: int, poll_ms: int = 200,
                      quiet_polls: int = 2) -> int:
    """Poll `selector`'s match count until it holds steady for `quiet_polls`
    consecutive polls, or `cap_ms` elapses — whichever comes first. Returns the
    last observed count.

    This is the direct replacement for `page.wait_for_timeout(settle_ms)`: on
    an already-rendered page it exits in ~poll_ms*quiet_polls instead of always
    burning the full cap, which is the entire latency win of this function.
    """
    deadline = time.monotonic() + cap_ms / 1000
    last = -2  # never equals a real count or the -1 "unknown" sentinel on poll 1
    quiet = 0
    count = _count(page, selector)
    while True:
        if count == last:
            quiet += 1
            if quiet >= quiet_polls:
                return count
        else:
            quiet = 0
        last = count
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return count
        time.sleep(min(poll_ms / 1000, remaining))
        count = _count(page, selector)


# -- async-Playwright mirrors (dealer_scraper/adapters/browser_async.py) --
# Identical semantics to the sync versions above; only the page calls and the
# poll sleep need `await` under the async API.

async def _count_async(page, selector: str) -> int:
    try:
        return int(await page.evaluate(_COUNT_JS, selector))
    except Exception:
        return -1


async def _poll_until_async(predicate: Callable[[], Awaitable[bool]], cap_ms: int,
                             poll_ms: int = 150) -> bool:
    deadline = time.monotonic() + cap_ms / 1000
    while True:
        try:
            if await predicate():
                return True
        except Exception:
            pass
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        await asyncio.sleep(min(poll_ms / 1000, remaining))


async def wait_for_growth_async(page, selector: str, prev_count: int, cap_ms: int) -> bool:
    try:
        await page.wait_for_function(_GROWTH_JS, arg=[selector, prev_count], timeout=cap_ms)
        return True
    except Exception:
        pass

    async def _grew() -> bool:
        return (await _count_async(page, selector)) > prev_count

    return await _poll_until_async(_grew, cap_ms)


async def wait_count_stable_async(page, selector: str, cap_ms: int, poll_ms: int = 200,
                                  quiet_polls: int = 2) -> int:
    deadline = time.monotonic() + cap_ms / 1000
    last = -2
    quiet = 0
    count = await _count_async(page, selector)
    while True:
        if count == last:
            quiet += 1
            if quiet >= quiet_polls:
                return count
        else:
            quiet = 0
        last = count
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return count
        await asyncio.sleep(min(poll_ms / 1000, remaining))
        count = await _count_async(page, selector)
