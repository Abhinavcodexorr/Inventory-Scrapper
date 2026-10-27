"""Scoring captured JSON traffic for "does this look like a vehicle feed".
Ported from dealer_scraper/discovery.py (pre-refactor).
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit

from ..heuristics import DEFAULT_HEURISTICS, HeuristicsConfig


def _score_item(item: dict, heuristics: HeuristicsConfig = DEFAULT_HEURISTICS) -> int:
    """How many vehicle-ish field names this dict has."""
    if not isinstance(item, dict):
        return 0
    hits = 0
    for k in item.keys():
        norm = re.sub(r"[_\-]", "", str(k)).lower()
        if heuristics.vehicle_signal_re.search(norm):
            hits += 1
    return hits


def _find_vehicle_array(obj, path: str = "", depth: int = 0, max_depth: int = 5,
                         heuristics: HeuristicsConfig = DEFAULT_HEURISTICS) -> dict:
    """Search a parsed JSON body for the array most likely to be the vehicle
    listing: mostly-dict items sharing car-ish field names. Returns
    {"score": float, "path": str|None, "array": list|None}; path is the dotted
    path _dig() would need to reach it ("" means the body IS the array)."""
    best = {"score": 0.0, "path": None, "array": None}
    if depth > max_depth:
        return best
    if isinstance(obj, list):
        dicts = [it for it in obj if isinstance(it, dict)]
        if obj and dicts and len(dicts) / len(obj) >= 0.8 and len(obj) >= 2:
            sample = dicts[:5]
            avg = sum(_score_item(it, heuristics) for it in sample) / len(sample)
            if avg >= 2:
                score = avg * min(len(obj), 50)
                if score > best["score"]:
                    best = {"score": score, "path": path, "array": obj}
    elif isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, (list, dict)):
                sub = _find_vehicle_array(v, f"{path}.{k}" if path else str(k), depth + 1, max_depth, heuristics)
                if sub["score"] > best["score"]:
                    best = sub
    return best


def _page_route(url: str) -> str:
    """Path a captured URL serves, with Next.js data routing undone:
    /_next/data/<build>/cars.json -> /cars."""
    path = urlsplit(url).path
    path = re.sub(r"^/_next/data/[^/]+", "", path)
    path = re.sub(r"(/index)?\.json$", "", path)
    return path.rstrip("/") or "/"


def _best_vehicle_json(hits: list[dict], heuristics: HeuristicsConfig = DEFAULT_HEURISTICS,
                       listing_url: str | None = None) -> dict | None:
    """Across every captured XHR/fetch JSON body, the strongest vehicle-array
    candidate: {"url", "list_path", "array", "root", "score"} or None.

    On a tie, the hit serving listing_url's own route wins. HillzDealer sites
    embed the whole inventory in every page's Next.js data, so the link
    prefetch for /forms/finance.json scores exactly as /cars.json does and,
    being captured first, used to be the endpoint discovery chose.
    """
    want = _page_route(listing_url) if listing_url else None
    best = None
    best_on_route = False
    for hit in hits:
        found = _find_vehicle_array(hit["json"], heuristics=heuristics)
        if not found["array"]:
            continue
        on_route = want is not None and _page_route(hit["url"]) == want
        if (best is None or found["score"] > best["score"]
                or (found["score"] == best["score"] and on_route and not best_on_route)):
            best_on_route = on_route
            best = {
                "url": hit["url"], "list_path": found["path"] or "",
                "array": found["array"], "root": hit["json"], "score": found["score"],
                "headers": hit.get("headers") or {},
                "method": hit.get("method") or "GET",
                "post_body": hit.get("post_body"),
            }
    return best


def _extra_request_headers(headers: dict, heuristics: HeuristicsConfig = DEFAULT_HEURISTICS) -> dict:
    """Headers the real browser sent that look like they matter (auth tokens,
    referer/origin checks, custom api keys) — as opposed to browser boilerplate
    an httpx client neither has nor needs."""
    out = {}
    for k, v in (headers or {}).items():
        kl = k.lower()
        if kl in heuristics.header_denylist or kl.startswith("sec-") or kl.startswith(":"):
            continue
        if isinstance(v, str) and v.strip():
            out[k] = v
    return out
