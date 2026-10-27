"""Batch-test discovery against many dealer sites.

    python -m dealer_scraper.discovery https://a.ca https://b.com ...
    python -m dealer_scraper.discovery --show-config https://a.ca
    python -m dealer_scraper.discovery --file sites.txt

Prints one row per site so you can see at a glance which sites the engine
supports and which need chargeable custom work.
"""
from __future__ import annotations

import argparse
import json
import sys

from . import discover


def _main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="dealer_scraper.discovery")
    ap.add_argument("urls", nargs="*", help="Dealer website URLs")
    ap.add_argument("--file", help="Text file with one URL per line")
    ap.add_argument("--show-config", action="store_true", help="Print the derived scrape_config")
    ap.add_argument("--headed", action="store_true", help="Show the browser (helps with bot walls)")
    args = ap.parse_args(argv)

    urls = list(args.urls)
    if args.file:
        with open(args.file) as fh:
            urls += [ln.strip() for ln in fh if ln.strip() and not ln.startswith("#")]
    if not urls:
        ap.error("give at least one URL, or --file")

    rows = []
    for url in urls:
        print(f"\n--- discovering {url} ...", flush=True)
        r = discover(url, headless=not args.headed)
        rows.append(r)
        flag = "CUSTOM WORK" if r.requires_custom_work else "supported"
        print(f"    verdict={r.verdict:<13} [{flag}]  vehicles={r.vehicles_found:<5} "
              f"extract={r.extract or '-':<7} paginate={r.pagination or '-'}")
        print(f"    {r.summary}")
        for note in r.notes:
            print(f"      · {note}")
        if args.show_config and r.scrape_config:
            print(json.dumps(r.scrape_config, indent=2))

    print("\n" + "=" * 78)
    print(f"{'SITE':<42} {'VERDICT':<14} {'CARS':>6}  BILLABLE?")
    print("-" * 78)
    for r in rows:
        print(f"{r.base_url[:40]:<42} {r.verdict:<14} {r.vehicles_found:>6}  "
              f"{'YES — quote custom scraper' if r.requires_custom_work else 'no'}")
    billable = sum(1 for r in rows if r.requires_custom_work)
    print("-" * 78)
    print(f"{len(rows)} site(s) checked · {len(rows) - billable} supported · "
          f"{billable} need custom work")
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
