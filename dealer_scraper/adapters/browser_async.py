"""Async-Playwright browser adapter — parallel page fetching behind the exact
same sync `Iterator[NormalizedVehicle]` contract every other adapter exposes.

engine.py never sees asyncio: `iter_vehicles()` spawns a daemon thread that
runs its own asyncio event loop (the "producer"), which pushes vehicles onto
a bounded queue; the sync generator returned to the caller (the "consumer")
just drains that queue. This lets `paginate="url"` fetch several list pages
concurrently — the thing the original sync adapter (browser.py) cannot do,
since Playwright's sync API is single-threaded by construction — while the
engine's dedup/commit loop keeps consuming NormalizedVehicle objects exactly
as it always has.

Reuses browser.py's parsing functions verbatim (parse_card_text, map_jsonld,
the in-page JS extractors, the stealth init script) so the two adapters can
never disagree on what a card means — only *how many pages get fetched at
once* differs.

scrape_config additions over browser.py's (see that module's docstring for
the shared keys):
{
  "browser_engine":    "async",   # "async" | "sync" — per-dealer override;
                                   # global default is settings.browser_engine_default
                                   # (config.py), currently "sync" — see that
                                   # setting's comment for why
  "fetch_concurrency": 4          # concurrent page fetches, paginate="url" only
}

Requires:  pip install playwright  &&  playwright install chromium
"""
from __future__ import annotations

import asyncio
import logging
import re
import threading
import time
from queue import Empty, Full, Queue
from typing import Iterator

from ..config import settings
from ..normalize import NormalizedVehicle, normalize
from ..timing import StageTimer
from .base import chromium_launch_args, configured_playwright_proxy, require_config
from .blocking import install_blocking_async, uninstall_blocking_async
from .browser import (
    _COLLECT_JS,
    _JSONLD_JS,
    _STEALTH_JS,
    _ensure_subprocess_capable_loop,
    map_jsonld,
    parse_card_text,
)
from .waits import wait_count_stable_async, wait_for_growth_async

log = logging.getLogger("scraper.browser_async")

# Finds the highest `page=N` among a listing page's own pagination controls —
# used to bound the parallel fan-out instead of guessing at max_pages.
_MAX_PAGE_JS = """
() => {
  let max = 0;
  for (const a of document.querySelectorAll('a[href*="page="]')) {
    const m = (a.getAttribute('href') || '').match(/[?&]page=(\\d+)/);
    if (m) { const n = parseInt(m[1], 10); if (n > max) max = n; }
  }
  return max;
}
"""

# Body phrases that mean "a WAF/anti-bot page, not the real listing" — used to
# tell a genuine transient error from a block worth backing off for.
_ANTIBOT_PHRASES = (
    "access denied", "are you a human", "captcha", "checking your browser",
    "unusual traffic", "request could not be satisfied", "bot detection",
    "attention required", "cloudflare",
)


class _Stopped(Exception):
    """Internal signal: the consumer generator was closed (GeneratorExit /
    ScrapeCancelled) — unwind the producer coroutine tree without treating it
    as a scrape failure."""


class _BackoffGate:
    """Downgrades concurrency on a WAF/rate-limit signal — never upgrades
    back, since a site that blocked once this run is assumed hostile for the
    rest of it. Shared by every concurrent fetch task for one scrape.

    Level 0: normal — up to `fetch_concurrency` pages in flight, no delay.
    Level 1: serialized (one page at a time) + 2s spacing + resource blocking
             disabled once (a browser that never loads an image/tracker is
             itself a bot signal on some sites).
    Level 2: serialized + 5s spacing.
    Level 3: exhausted — raises, failing the run visibly rather than silently
             returning a half-scraped inventory as if it were complete.
    """

    def __init__(self, context) -> None:
        self.level = 0
        self._context = context
        self._level_lock = asyncio.Lock()
        self.serial_lock = asyncio.Lock()
        self._unblocked = False

    @property
    def delay_seconds(self) -> float:
        return {1: 2.0, 2: 5.0}.get(self.level, 0.0)

    async def signal(self, reason: str) -> None:
        async with self._level_lock:
            if self.level >= 3:
                raise RuntimeError(f"WAF/rate-limit backoff exhausted ({reason})")
            self.level += 1
            log.warning("Backoff level -> %s (%s)", self.level, reason)
            if not self._unblocked:
                self._unblocked = True
                await uninstall_blocking_async(self._context)

    def classify(self, exc: Exception | None = None, status: int | None = None) -> str | None:
        if status in (403, 429):
            return f"http {status}"
        if exc is not None:
            msg = str(exc).lower()
            if any(p in msg for p in _ANTIBOT_PHRASES):
                return "antibot page"
            if "err_http2_protocol_error" in msg or "err_aborted" in msg or "err_connection" in msg:
                return "network error"
        return None

    async def acquire_slot(self) -> bool:
        """Serializes fetches once backing off. Returns whether the serial
        lock was taken (release_slot needs to know, since `level` can change
        between acquire and release)."""
        if self.level >= 1:
            await self.serial_lock.acquire()
            if self.delay_seconds:
                await asyncio.sleep(self.delay_seconds)
            return True
        return False

    def release_slot(self, held: bool) -> None:
        if held:
            self.serial_lock.release()


class _PagePool:
    """Up to `size` pages, created lazily, reused across fetches. A page that
    errors is replaced rather than returned to the pool, so a crashed
    renderer never gets handed to the next fetch."""

    def __init__(self, context, size: int) -> None:
        self._context = context
        self._size = max(1, size)
        self._queue: asyncio.Queue = asyncio.Queue()
        self._created = 0
        self._lock = asyncio.Lock()

    async def acquire(self):
        if not self._queue.empty():
            return self._queue.get_nowait()
        async with self._lock:
            if self._created < self._size:
                page = await self._context.new_page()
                self._created += 1
                return page
        return await self._queue.get()

    async def release(self, page) -> None:
        self._queue.put_nowait(page)

    async def replace(self, page):
        try:
            await page.close()
        except Exception:
            pass
        return await self._context.new_page()

    async def close_all(self) -> None:
        while not self._queue.empty():
            page = self._queue.get_nowait()
            try:
                await page.close()
            except Exception:
                pass


class AsyncBrowserAdapter:
    mode = "browser"
    REQUIRED_CONFIG = ("list_url",)

    def __init__(self, config: dict, *, timer: StageTimer | None = None):
        require_config(self.mode, config, *self.REQUIRED_CONFIG)
        self.cfg = config
        self.timer = timer or StageTimer()
        self.base_url: str = config.get("base_url", "")
        self.card_anchor: str = config.get("card_anchor", "a[href*='/vehicles/']")
        self.card_selector: str = config.get("card_selector", "")
        self.price_label: str = config.get("price_label", "")
        self.wait_selector: str = config.get("wait_selector", self.card_anchor)
        self.id_regex = re.compile(config.get("id_regex", r"/(?:vehicles|cars|inventory)/([\w-]+)"))
        self.paginate: str = config.get("paginate", "scroll")
        self.max_pages: int = int(config.get("max_pages", 60))
        self.scroll_rounds: int = int(config.get("scroll_rounds", 40))
        self.headless: bool = bool(config.get("headless", True))
        self.nav_timeout: int = int(config.get("nav_timeout", 45000))
        self.settle_ms: int = int(config.get("settle_ms", 1500))
        self.total_selector: str = config.get("total_selector", "")
        self.extract: str = config.get("extract", "dom")
        self.block_resources: bool = bool(config.get("block_resources", settings.resource_blocking_default))
        self.fetch_concurrency: int = max(1, int(
            config.get("fetch_concurrency", settings.per_dealer_fetch_concurrency)))
        self._total: int | None = None
        self._stop = threading.Event()

    # -- helpers (identical to BrowserAdapter's — pure Python, no I/O) -----
    def _to_vehicle(self, rec: dict) -> NormalizedVehicle | None:
        href = rec.get("href", "")
        m = self.id_regex.search(href)
        if not m:
            return None
        if "ld" in rec:
            raw = map_jsonld(rec["ld"])
        else:
            raw = parse_card_text(rec.get("text", ""), self.price_label)
        raw["external_id"] = m.group(1)
        raw["url"] = href if href.startswith("http") else self.base_url + href
        raw["_raw"] = rec.get("ld") or rec
        return normalize(raw)

    def _emit_to_queue(self, q: Queue, records: list[dict], seen: set[str]) -> bool:
        """Parses and enqueues each new record. Returns False the moment a
        stop is observed, so the caller can unwind instead of continuing to
        do useless work for a consumer that's already gone."""
        for rec in records:
            href = rec.get("href")
            if not href or href in seen:
                continue
            seen.add(href)
            with self.timer.stage("parse"):
                v = self._to_vehicle(rec)
            if not (v and v.external_id):
                continue
            while True:
                if self._stop.is_set():
                    return False
                try:
                    q.put(("item", v), timeout=0.5)
                    break
                except Full:
                    continue
        return not self._stop.is_set()

    # -- adapter API --------------------------------------------------------
    def fetch_total_count(self) -> int | None:
        return self._total

    def iter_vehicles(self) -> Iterator[NormalizedVehicle]:
        _ensure_subprocess_capable_loop()
        q: Queue = Queue(maxsize=200)
        self._stop = threading.Event()
        watchdog_seconds = max(self.nav_timeout / 1000 + 120, 180)
        t = threading.Thread(target=self._producer_main, args=(q,), daemon=True,
                             name=f"scrape-async-{id(self)}")
        t.start()
        try:
            while True:
                try:
                    kind, payload = q.get(timeout=watchdog_seconds)
                except Empty:
                    raise RuntimeError(
                        f"Async browser adapter produced nothing for {watchdog_seconds:.0f}s "
                        f"— treating the producer as hung (base_url={self.base_url!r})"
                    )
                if kind == "item":
                    yield payload
                elif kind == "error":
                    raise payload
                else:  # "done"
                    return
        finally:
            self._stop.set()
            _drain(q)
            t.join(timeout=30)
            if t.is_alive():
                log.warning("Async browser producer thread for %s outlived a 30s join "
                            "after stop — abandoning it (daemon thread, process exit will "
                            "reclaim it)", self.base_url)

    def _producer_main(self, q: Queue) -> None:
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self._run_async(q))
            q.put(("done", None))
        except _Stopped:
            q.put(("done", None))
        except Exception as exc:  # never let the producer thread die silently
            log.exception("Async browser producer failed for %s", self.base_url)
            try:
                q.put(("error", exc), timeout=5)
            except Full:
                pass
        finally:
            loop.close()

    # -- the actual scrape, running on the producer thread's event loop -----
    async def _run_async(self, q: Queue) -> None:
        from playwright.async_api import async_playwright

        seen: set[str] = set()
        async with async_playwright() as p:
            launch = {"headless": self.headless, "args": chromium_launch_args()}
            proxy = configured_playwright_proxy()
            if proxy:
                launch["proxy"] = proxy
            with self.timer.stage("browser_launch"):
                browser = await p.chromium.launch(**launch)
            pool: _PagePool | None = None
            gate: _BackoffGate | None = None
            get_blocked_count = (lambda: 0)
            try:
                ctx = await browser.new_context(
                    user_agent=("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"),
                    viewport={"width": 1440, "height": 900},
                    locale="en-CA",
                    timezone_id="America/Toronto",
                    extra_http_headers={"Accept-Language": "en-CA,en;q=0.9"},
                )
                await ctx.add_init_script(_STEALTH_JS)
                if self.block_resources:
                    get_blocked_count = await install_blocking_async(ctx)

                pool = _PagePool(ctx, self.fetch_concurrency)
                gate = _BackoffGate(ctx)

                page0 = await pool.acquire()
                await self._load_first_page(page0)

                first_incremental = self.paginate != "url"
                first_records = await self._collect_async(page0, incremental=first_incremental)
                if not self._emit_to_queue(q, first_records, seen):
                    raise _Stopped()
                self.timer.incr("pages")

                if self.paginate == "scroll":
                    await self._run_scroll(page0, seen, q)
                    await pool.release(page0)
                elif self.paginate == "url":
                    est = await self._estimate_total_pages(page0, len(first_records))
                    await pool.release(page0)
                    await self._run_url_parallel(pool, gate, est, seen, q)
                else:
                    await self._run_click(page0, seen, q)
                    await pool.release(page0)
            finally:
                if gate is not None and gate.level:
                    self.timer.incr("backoff_level_reached", gate.level)
                if pool is not None:
                    self.timer.incr("requests_blocked", get_blocked_count())
                    await pool.close_all()
                await browser.close()

    async def _load_first_page(self, page) -> None:
        warmup_ms = int(self.cfg.get("warmup_ms", 4000))
        if self.base_url:
            try:
                with self.timer.stage("warmup"):
                    await page.goto(self.base_url, wait_until="domcontentloaded", timeout=self.nav_timeout)
                    await page.wait_for_load_state("networkidle", timeout=warmup_ms)
            except Exception:
                pass

        first = self.cfg.get("page_url", self.cfg["list_url"]).format(page=1) \
            if self.paginate == "url" else self.cfg["list_url"]

        retries = int(self.cfg.get("load_retries", 3))
        found = False
        for attempt in range(1, retries + 1):
            if self._stop.is_set():
                raise _Stopped()
            try:
                with self.timer.stage("nav"):
                    await page.goto(first, wait_until="domcontentloaded", timeout=self.nav_timeout)
                    await page.wait_for_selector(self.wait_selector, timeout=self.nav_timeout // retries)
                found = True
                break
            except Exception as exc:
                log.warning("Attempt %s/%s: %s — reloading after backoff",
                            attempt, retries, str(exc).splitlines()[0][:120])
                if attempt < retries:
                    with self.timer.stage("wait"):
                        await asyncio.sleep((3000 + attempt * 2000) / 1000)

        if not found:
            shot = self.cfg.get("debug_screenshot", "scrape_debug.png")
            try:
                await page.screenshot(path=shot, full_page=False)
            except Exception:
                shot = "(screenshot failed)"
            title = body = ""
            try:
                title = await page.title()
                body = (await page.text_content("body") or "")[:400].replace("\n", " ")
            except Exception:
                pass
            raise RuntimeError(
                f"'{self.wait_selector}' never appeared after {retries} tries. "
                f"Likely bot/WAF blocking the inventory API. "
                f"url={page.url} | title={title!r} | body_start={body!r} | screenshot={shot}"
            )

        with self.timer.stage("wait"):
            await wait_count_stable_async(page, self.wait_selector, cap_ms=self.settle_ms)

        try:
            if self.total_selector:
                txt = await page.text_content(self.total_selector) or ""
            else:
                txt = await page.text_content("body") or ""
            mt = re.search(r"([\d,]{3,})\s*(?:results|cars|vehicles|listings)", txt, re.I)
            self._total = int(mt.group(1).replace(",", "")) if mt else None
        except Exception:
            self._total = None

    async def _collect_async(self, page, *, incremental: bool = False) -> list[dict]:
        with self.timer.stage("extract"):
            if self.extract == "jsonld":
                nodes = await page.evaluate(_JSONLD_JS)
                return [{"href": (n.get("url") or n.get("@id") or ""), "ld": n} for n in nodes]
            return await page.evaluate(_COLLECT_JS, {
                "anchorSel": self.card_anchor, "cardSel": self.card_selector,
                "incremental": incremental,
            })

    # -- single-page modes (scroll/click) — no concurrency, just async ------
    async def _run_scroll(self, page, seen: set[str], q: Queue) -> None:
        stagnant = 0
        for _ in range(self.scroll_rounds):
            if self._stop.is_set():
                raise _Stopped()
            before = len(seen)  # count as of the end of the previous round's collect
            with self.timer.stage("wait"):
                await page.mouse.wheel(0, 20000)
                grew = await wait_for_growth_async(page, self.card_anchor, before, cap_ms=self.settle_ms)
            records = await self._collect_async(page, incremental=True)
            if not self._emit_to_queue(q, records, seen):
                raise _Stopped()
            self.timer.incr("pages")
            if self._total and len(seen) >= self._total:
                break
            if not grew:
                stagnant += 1
                if stagnant >= 3:
                    break
            else:
                stagnant = 0

    async def _run_click(self, page, seen: set[str], q: Queue) -> None:
        nxt = self.cfg.get("next_selector")
        for _ in range(self.max_pages):
            if self._stop.is_set():
                raise _Stopped()
            btn = await page.query_selector(nxt) if nxt else None
            # is_enabled() alone misses a common "no more results" pattern:
            # the button stays in the DOM, not disabled, but CSS-hidden. A
            # click Playwright can't complete then burns its full actionability
            # timeout (default 30s) retrying against an invisible target.
            if not btn or not await btn.is_enabled() or not await btn.is_visible():
                break
            before = len(seen)
            with self.timer.stage("wait"):
                await btn.click()
                grew = await wait_for_growth_async(page, self.card_anchor, before, cap_ms=self.settle_ms)
            if not grew:
                break
            records = await self._collect_async(page, incremental=True)
            if not self._emit_to_queue(q, records, seen):
                raise _Stopped()
            self.timer.incr("pages")

    # -- url mode: the actual parallel-fetch path ----------------------------
    async def _estimate_total_pages(self, page, first_page_card_count: int) -> int:
        control_max = 0
        try:
            control_max = int(await page.evaluate(_MAX_PAGE_JS))
        except Exception:
            pass
        ceil_estimate = 0
        if self._total and first_page_card_count:
            ceil_estimate = -(-self._total // first_page_card_count)  # ceil division
        est = max(control_max, ceil_estimate)
        if not est:
            return 0  # unknown — caller falls back to the windowed frontier
        return min(self.max_pages, est)

    async def _run_url_parallel(self, pool: _PagePool, gate: _BackoffGate, est_pages: int,
                                seen: set[str], q: Queue) -> None:
        sem = asyncio.Semaphore(self.fetch_concurrency)
        if est_pages:
            await self._fetch_pages(pool, gate, sem, list(range(2, est_pages + 1)), seen, q)
            return

        # Unknown bound: probe in windows of `fetch_concurrency` pages at a
        # time and stop once an entire window yields nothing new — stronger
        # evidence than the old single-empty-page streak, since the whole
        # window ran concurrently rather than one page at a time.
        window = self.fetch_concurrency
        start = 2
        while start <= self.max_pages:
            if self._stop.is_set():
                raise _Stopped()
            end = min(start + window - 1, self.max_pages)
            before = len(seen)
            await self._fetch_pages(pool, gate, sem, list(range(start, end + 1)), seen, q)
            if len(seen) == before:
                break
            start = end + 1

    async def _fetch_pages(self, pool: _PagePool, gate: _BackoffGate, sem: asyncio.Semaphore,
                           page_nums: list[int], seen: set[str], q: Queue) -> None:
        """Fetch every page in `page_nums`, then retry once whichever ones
        failed — by the retry pass the backoff gate has very likely already
        stepped down to serialized fetching, so the retry itself is the
        recovery mechanism, not just a second roll of the dice."""
        failed = await self._fetch_batch(pool, gate, sem, page_nums, seen, q)
        if failed:
            still_failed = await self._fetch_batch(pool, gate, sem, failed, seen, q)
            # A minority failing twice is tolerated (some pages may just be
            # gone); a majority means the site is actively rejecting us and
            # the run must surface that, not silently under-report inventory.
            if len(still_failed) > max(1, len(page_nums) // 2):
                raise RuntimeError(
                    f"{len(still_failed)}/{len(page_nums)} pages failed twice in a row "
                    f"(base_url={self.base_url!r}) — treating the site as unreachable "
                    f"rather than silently under-reporting inventory"
                )

    async def _fetch_batch(self, pool: _PagePool, gate: _BackoffGate, sem: asyncio.Semaphore,
                           page_nums: list[int], seen: set[str], q: Queue) -> list[int]:
        """Fetch every page_num concurrently (bounded by sem/gate). Returns the
        page numbers that failed, for the caller's optional retry pass."""
        tasks = {n: asyncio.create_task(self._fetch_one_page(pool, gate, sem, n, seen, q))
                for n in page_nums}
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)
        failed = []
        for n, r in zip(tasks.keys(), results):
            if isinstance(r, _Stopped):
                raise r
            if isinstance(r, Exception) or r is False:
                failed.append(n)
        return failed

    async def _fetch_one_page(self, pool: _PagePool, gate: _BackoffGate, sem: asyncio.Semaphore,
                              page_num: int, seen: set[str], q: Queue) -> bool:
        """True on success. False means page_num is worth retrying once the
        backoff gate (if any signal fired) has taken effect — a 403/429/WAF
        page's content is never trusted, even if it happened to render."""
        if self._stop.is_set():
            raise _Stopped()
        async with sem:
            held = await gate.acquire_slot()
            try:
                page = await pool.acquire()
                healthy = True
                try:
                    url = self.cfg["page_url"].format(page=page_num)
                    with self.timer.stage("nav"):
                        resp = await page.goto(url, wait_until="domcontentloaded", timeout=self.nav_timeout)
                        if resp is not None and resp.status in (403, 429):
                            await gate.signal(f"http {resp.status}")
                            return False
                        try:
                            await page.wait_for_selector(self.card_anchor, timeout=self.nav_timeout // 2)
                        except Exception:
                            pass
                    with self.timer.stage("wait"):
                        await wait_count_stable_async(page, self.card_anchor, cap_ms=self.settle_ms)
                    records = await self._collect_async(page)
                    self.timer.incr("pages")
                    if not self._emit_to_queue(q, records, seen):
                        raise _Stopped()
                    return True
                except _Stopped:
                    raise
                except Exception as exc:
                    healthy = False
                    reason = gate.classify(exc=exc)
                    if reason:
                        await gate.signal(reason)
                    else:
                        log.warning("Page %s fetch failed: %s", page_num, exc)
                    return False
                finally:
                    if healthy:
                        await pool.release(page)
                    else:
                        fresh = await pool.replace(page)
                        await pool.release(fresh)
            finally:
                gate.release_slot(held)


def _drain(q: Queue) -> None:
    try:
        while True:
            q.get_nowait()
    except Empty:
        pass
