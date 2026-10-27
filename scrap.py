#!/usr/bin/env python3
"""
benchmark_dealer.py — Per-dealer scrape benchmark harness
=========================================================

Purpose
-------
Measure the REAL numbers our capacity/cost model needs, on YOUR machine and
network (and, ideally, through YOUR proxy). It samples a handful of listing-API
pages politely, measures latency + payload size, then projects the full-dealer
scrape time and data volume WITHOUT actually pulling the whole inventory.

It answers, per dealer:
  * avg latency per listing request (L)
  * avg payload size per page (KB)
  * total pages / total cars
  * projected full-scrape wall time  (at a chosen politeness delay + concurrency)
  * projected data volume (drives proxy $/GB cost)
  * effective cars/sec

Two modes:
  api      — hit the dealer's JSON listing endpoint directly (preferred, fastest)
  browser  — render the SPA with Playwright and time page loads (fallback sites)

------------------------------------------------------------------------------
HOW TO FIND THE API URL (api mode)
------------------------------------------------------------------------------
1. Open the dealer's inventory page in Chrome (e.g. https://www.clutch.ca/buy).
2. DevTools (F12) -> Network tab -> filter "Fetch/XHR".
3. Scroll / change page; watch for a request returning JSON with the car list.
4. Copy that request URL. Note the page & page-size query params.
   Pass the URL with {page} where the page number goes, e.g.:
     "https://api.example.com/v1/vehicles?page={page}&limit=25"
   Use --json-path to point at the array of cars (dotted path), and
   --total-path to point at the total-count field, if present.

------------------------------------------------------------------------------
QUICK START
------------------------------------------------------------------------------
  python -m pip install httpx            # api mode
  python -m pip install playwright && playwright install chromium   # browser mode

  # API mode (recommended):
  python benchmark_dealer.py api \
      --url "https://api.example.com/v1/vehicles?page={page}&limit=25" \
      --json-path "results.vehicles" --total-path "results.total" \
      --sample-pages 6 --delay 1.0 --concurrency 1

  # Through a proxy (measures real proxy latency + counts GB):
  python benchmark_dealer.py api --url "..." --proxy "http://user:pass@host:port"

  # Browser mode (SPA, no clean API):
  python benchmark_dealer.py browser \
      --url "https://www.clutch.ca/buy" \
      --card-selector "[data-testid='vehicle-card']" \
      --sample-pages 4 --delay 1.0

Be polite: keep --sample-pages small (5-8 is plenty to get a stable average),
keep --delay >= 1.0, and run off-peak. This tool never fetches the whole
inventory — it extrapolates from the sample.
"""
from __future__ import annotations
import argparse, json, statistics, sys, time
from dataclasses import dataclass, field
from typing import Any


def dotted(obj: Any, path: str) -> Any:
    if not path:
        return obj
    for key in path.split("."):
        if isinstance(obj, list):
            key = int(key)
        obj = obj[key]
    return obj


def human_time(seconds: float) -> str:
    if seconds < 90:
        return f"{seconds:.0f} s"
    if seconds < 5400:
        return f"{seconds/60:.1f} min"
    return f"{seconds/3600:.2f} h"


def human_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


@dataclass
class Sample:
    latencies: list[float] = field(default_factory=list)
    sizes: list[int] = field(default_factory=list)
    cars_per_page: list[int] = field(default_factory=list)
    total_cars: int | None = None


# --------------------------------------------------------------------------- #
# API mode
# --------------------------------------------------------------------------- #
def run_api(args) -> Sample:
    import httpx

    if "{page}" not in args.url:
        print("WARN: --url has no {page} placeholder; sampling the same page repeatedly.")

    headers = {"User-Agent": args.user_agent, "Accept": "application/json"}
    proxies = args.proxy or None
    s = Sample()

    with httpx.Client(headers=headers, proxy=proxies, timeout=args.timeout, follow_redirects=True) as client:
        for i in range(args.sample_pages):
            page = args.start_page + i
            url = args.url.replace("{page}", str(page))
            t0 = time.perf_counter()
            r = client.get(url)
            dt = time.perf_counter() - t0
            r.raise_for_status()
            body = r.content
            data = r.json()

            cars = dotted(data, args.json_path) if args.json_path else data
            n_cars = len(cars) if isinstance(cars, list) else 0
            if s.total_cars is None and args.total_path:
                try:
                    s.total_cars = int(dotted(data, args.total_path))
                except Exception:
                    s.total_cars = None

            s.latencies.append(dt)
            s.sizes.append(len(body))
            s.cars_per_page.append(n_cars)
            print(f"  page {page:>4}: {dt*1000:7.0f} ms  {human_bytes(len(body)):>9}  {n_cars:>3} cars")
            if i < args.sample_pages - 1:
                time.sleep(args.delay)
    return s


# --------------------------------------------------------------------------- #
# Browser mode (Playwright)
# --------------------------------------------------------------------------- #
def run_browser(args) -> Sample:
    from playwright.sync_api import sync_playwright

    s = Sample()
    with sync_playwright() as p:
        launch_kwargs = {"headless": True}
        if args.proxy:
            # Playwright ignores user:pass inside "server" (-> 407 from the
            # proxy); it needs them as separate keys.
            from urllib.parse import unquote, urlsplit
            u = urlsplit(args.proxy)
            server = f"{u.scheme}://{u.hostname}" + (f":{u.port}" if u.port else "")
            launch_kwargs["proxy"] = {"server": server}
            if u.username is not None:
                launch_kwargs["proxy"]["username"] = unquote(u.username)
            if u.password is not None:
                launch_kwargs["proxy"]["password"] = unquote(u.password)
        browser = p.chromium.launch(**launch_kwargs)
        ctx = browser.new_context(user_agent=args.user_agent)
        page = ctx.new_page()

        transferred = {"bytes": 0}
        # NOTE: content-length undercounts chunked/streamed responses (no header),
        # but is far closer to the real transfer size than the previous bug here,
        # which measured the BYTE LENGTH OF THE HEADER STRING ("1234" -> 4 bytes)
        # instead of the value it names (1234 bytes).
        page.on("response", lambda resp: transferred.__setitem__(
            "bytes", transferred["bytes"] + int(resp.headers.get("content-length") or 0)))

        for i in range(args.sample_pages):
            pnum = args.start_page + i
            url = args.url.replace("{page}", str(pnum)) if "{page}" in args.url else args.url
            transferred["bytes"] = 0
            t0 = time.perf_counter()
            page.goto(url, wait_until="networkidle", timeout=int(args.timeout * 1000))
            if args.card_selector:
                page.wait_for_selector(args.card_selector, timeout=int(args.timeout * 1000))
            dt = time.perf_counter() - t0
            n_cars = page.locator(args.card_selector).count() if args.card_selector else 0

            s.latencies.append(dt)
            s.sizes.append(transferred["bytes"])
            s.cars_per_page.append(n_cars)
            print(f"  render {pnum:>3}: {dt*1000:7.0f} ms  ~{human_bytes(transferred['bytes']):>9}  {n_cars:>3} cards")
            if i < args.sample_pages - 1:
                time.sleep(args.delay)
        browser.close()
    return s


# --------------------------------------------------------------------------- #
# Pipeline mode — run a real dealer's adapter end-to-end (no DB writes) and
# report the same per-stage timing the engine logs on a real sync, so a
# before/after latency comparison doesn't require a full sync to measure.
# --------------------------------------------------------------------------- #
def run_pipeline(args) -> int:
    import hashlib

    from dealer_scraper.adapters.registry import build_adapter
    from dealer_scraper.db import SessionLocal
    from dealer_scraper.models import Dealer
    from dealer_scraper.timing import StageTimer

    with SessionLocal() as session:
        dealer = session.get(Dealer, args.dealer_id)
        if dealer is None:
            print(f"No dealer with id={args.dealer_id}")
            return 1

        scrape_config = dict(dealer.scrape_config or {})
        if args.engine:
            scrape_config["browser_engine"] = args.engine

        timer = StageTimer()
        adapter = build_adapter(dealer.adapter_mode, scrape_config, timer=timer)

        print(f"Pipeline benchmark: dealer #{dealer.id} '{dealer.name}' "
              f"(adapter_mode={dealer.adapter_mode}, engine={args.engine})\n")

        t0 = time.perf_counter()
        signature_rows: list[tuple] = []
        count = 0
        try:
            for v in adapter.iter_vehicles():
                count += 1
                signature_rows.append((v.external_id, str(v.price), v.mileage))
        except Exception as exc:
            print(f"Adapter raised: {type(exc).__name__}: {exc}")
            return 1
        total = time.perf_counter() - t0

        snap = timer.snapshot()
        pages = snap["counters"].get("pages")
        signature = hashlib.sha256(
            "|".join(f"{a}:{b}:{c}" for a, b, c in sorted(signature_rows)).encode()
        ).hexdigest()

        print(timer.report_text(
            header={"Dealer": f"{dealer.name} (#{dealer.id})", "Engine": args.engine},
            total_seconds=total, vehicles=count, pages=pages,
        ))
        print(f"\nContent signature: {signature}")

        if args.out:
            out = {
                "mode": "pipeline", "dealer_id": dealer.id, "dealer_name": dealer.name,
                "adapter_mode": dealer.adapter_mode, "engine": args.engine,
                "total_seconds": round(total, 3), "vehicle_count": count, "pages": pages,
                "content_signature": signature, "stats": snap,
            }
            with open(args.out, "a") as f:
                f.write(json.dumps(out) + "\n")
            print(f"Appended machine-readable result to {args.out}")
        return 0


# --------------------------------------------------------------------------- #
# Projection / report
# --------------------------------------------------------------------------- #
def report(args, s: Sample) -> None:
    if not s.latencies:
        print("No samples collected.")
        return

    avg_lat = statistics.mean(s.latencies)
    p95_lat = max(s.latencies) if len(s.latencies) < 5 else statistics.quantiles(s.latencies, n=20)[-1]
    avg_size = statistics.mean(s.sizes)
    cars_pp = statistics.median([c for c in s.cars_per_page if c]) or args.page_size

    total_cars = s.total_cars or args.assume_cars
    total_pages = max(1, -(-int(total_cars) // int(cars_pp)))  # ceil

    # per-request wall time = latency + politeness delay, divided by concurrency
    per_req = (avg_lat + args.delay) / max(1, args.concurrency)
    full_time = total_pages * per_req
    full_bytes = total_pages * avg_size
    cars_per_sec = total_cars / full_time if full_time else 0

    print("\n" + "=" * 66)
    print(f"  DEALER BENCHMARK RESULT  ({args.mode} mode)")
    print("=" * 66)
    print(f"  Sampled pages............ {len(s.latencies)}")
    print(f"  Avg latency / request.... {avg_lat*1000:.0f} ms   (p95 ~{p95_lat*1000:.0f} ms)")
    print(f"  Avg payload / page....... {human_bytes(avg_size)}")
    print(f"  Cars per page............ {cars_pp:.0f}")
    print(f"  Total cars............... {total_cars}"
          f"{'  (from API total field)' if s.total_cars else '  (assumed via --assume-cars)'}")
    print(f"  Total pages.............. {total_pages}")
    print("  " + "-" * 62)
    print(f"  Politeness delay......... {args.delay:.1f} s   Concurrency: {args.concurrency}")
    print(f"  >> Projected scrape time. {human_time(full_time)}   ({full_time:.0f} s)")
    print(f"  >> Projected data volume. {human_bytes(full_bytes)}  per full scrape")
    print(f"  >> Effective throughput.. {cars_per_sec:.1f} cars/sec")
    print("  " + "-" * 62)
    print(f"  Per-day proxy volume (this dealer, 1 run/day): {human_bytes(full_bytes)}")
    print(f"  Per-month (30 runs)....: {human_bytes(full_bytes*30)}")
    print("=" * 66)

    out = {
        "mode": args.mode, "url": args.url,
        "avg_latency_s": round(avg_lat, 4), "p95_latency_s": round(p95_lat, 4),
        "avg_page_bytes": int(avg_size), "cars_per_page": int(cars_pp),
        "total_cars": int(total_cars), "total_pages": int(total_pages),
        "politeness_delay_s": args.delay, "concurrency": args.concurrency,
        "projected_scrape_seconds": round(full_time, 1),
        "projected_bytes_per_scrape": int(full_bytes),
        "projected_bytes_per_month": int(full_bytes * 30),
        "cars_per_sec": round(cars_per_sec, 2),
    }
    if args.out:
        with open(args.out, "a") as f:
            f.write(json.dumps(out) + "\n")
        print(f"\nAppended machine-readable result to {args.out}")
    print("\nPlug 'projected_scrape_seconds' and 'projected_bytes_per_month' into the")
    print("capacity model in the report to replace the ASSUMED figures with measured ones.")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Per-dealer scrape benchmark harness")
    sub = p.add_subparsers(dest="mode", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--url", required=True, help="Listing URL; use {page} placeholder for pagination.")
    common.add_argument("--sample-pages", type=int, default=6, help="How many pages to sample (keep small, 5-8).")
    common.add_argument("--start-page", type=int, default=1)
    common.add_argument("--page-size", type=int, default=25, help="Fallback cars/page if not detected.")
    common.add_argument("--delay", type=float, default=1.0, help="Politeness delay between requests (s).")
    common.add_argument("--concurrency", type=int, default=1, help="Parallel requests PER dealer (keep 1-3).")
    common.add_argument("--assume-cars", type=int, default=1000, help="Used only if total count can't be read.")
    common.add_argument("--proxy", default=None, help="http://user:pass@host:port  (measures real proxy latency).")
    common.add_argument("--timeout", type=float, default=30.0)
    common.add_argument("--user-agent", default="Mozilla/5.0 (compatible; InventoryBot/1.0)")
    common.add_argument("--out", default="benchmark_results.jsonl", help="Append JSON result here.")

    a = sub.add_parser("api", parents=[common], help="Hit the JSON listing endpoint directly.")
    a.add_argument("--json-path", default="", help="Dotted path to the car array, e.g. 'results.vehicles'.")
    a.add_argument("--total-path", default="", help="Dotted path to the total-count field, e.g. 'results.total'.")

    b = sub.add_parser("browser", parents=[common], help="Render the SPA with Playwright.")
    b.add_argument("--card-selector", default="", help="CSS selector for a vehicle card (to count per page).")

    pl = sub.add_parser(
        "pipeline",
        help="Run a real dealer's adapter end-to-end (DB read-only) and report per-stage timing.",
    )
    pl.add_argument("--dealer-id", type=int, required=True)
    pl.add_argument("--engine", choices=["sync", "async"], default="sync",
                     help="browser_engine override passed to the adapter (async = Phase 2).")
    pl.add_argument("--out", default="benchmark_results.jsonl", help="Append JSON result here (empty to skip).")
    return p


def main() -> int:
    args = build_parser().parse_args()
    if args.mode == "pipeline":
        return run_pipeline(args)
    print(f"Benchmarking ({args.mode} mode): {args.url}\n")
    try:
        s = run_api(args) if args.mode == "api" else run_browser(args)
    except ImportError as e:
        print(f"Missing dependency: {e}. Install it (see header) and retry.")
        return 2
    except Exception as e:
        print(f"Benchmark error: {e}")
        return 1
    report(args, s)
    return 0


if __name__ == "__main__":
    sys.exit(main())