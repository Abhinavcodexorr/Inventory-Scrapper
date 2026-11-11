"""Scrape a dealer on THIS machine and push the cars to the server.

For sites the server cannot reach (for example mostwantedcars.ca only answers
from a Canadian address). Run it where the site opens:

    API_KEY=...  .venv/bin/python scripts/push_scrape.py \\
        --server http://13.205.212.181:8080 --dealer-id 529 \\
        --mode json_api --config path/to/config.json

    --dry-run   scrape and print the count, send nothing

The API key is read from the API_KEY environment variable, never from the
command line, so it stays out of shell history.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from dealer_scraper.adapters.registry import build_adapter  # noqa: E402

_SKIP = {"raw_payload"}  # large, and not needed to identify or price a car


def vehicle_to_row(v) -> dict:
    row = {}
    for f in dataclasses.fields(v):
        if f.name in _SKIP:
            continue
        val = getattr(v, f.name)
        row[f.name] = str(val) if isinstance(val, Decimal) else val
    return row


def scrape(mode: str, config: dict) -> tuple[list[dict], int | None]:
    adapter = build_adapter(mode, config)
    total = adapter.fetch_total_count()
    rows = [vehicle_to_row(v) for v in adapter.iter_vehicles()]
    return rows, total or getattr(adapter, "_total", None)


def push(server: str, dealer_id: int, api_key: str, rows: list[dict], total: int | None) -> dict:
    url = f"{server.rstrip('/')}/api/dealers/{dealer_id}/ingest"
    resp = httpx.post(
        url, headers={"X-API-Key": api_key},
        json={"vehicles": rows, "reported_total": total}, timeout=300,
    )
    if resp.status_code != 200:
        raise SystemExit(f"Server refused the push: HTTP {resp.status_code} {resp.text[:300]}")
    return resp.json()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--server", required=True, help="e.g. http://13.205.212.181:8080")
    ap.add_argument("--dealer-id", type=int, required=True, help="the dealer's id ON THE SERVER")
    ap.add_argument("--config", required=True, help="scrape config JSON file")
    ap.add_argument("--mode", default="json_api", help="adapter mode (default json_api)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    config = json.loads(Path(args.config).read_text())
    rows, total = scrape(args.mode, config)
    print(f"Scraped {len(rows)} cars (site reports {total}).")
    if not rows:
        print("Nothing scraped; not sending.")
        return 1
    if args.dry_run:
        return 0

    api_key = os.environ.get("API_KEY", "")
    if not api_key:
        print("Set the API_KEY environment variable first.")
        return 2
    result = push(args.server, args.dealer_id, api_key, rows, total)
    print("Server result:", json.dumps(result))
    return 0 if result.get("status") in ("completed",) else 1


if __name__ == "__main__":
    raise SystemExit(main())
