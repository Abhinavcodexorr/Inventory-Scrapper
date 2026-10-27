"""Static checks on a dealer's scrape_config that catch configs which can only
produce wrong or truncated data.

Two classes of defect, both observed live and both invisible on a dashboard
because the run reports success:

* **Structural** — the config cannot do what it declares. B Town, Good Fellows
  and AA Canada declare a `total_path` and `page_size` but their endpoint has no
  `{page}`, so every request returns page 1: 120 "scraped", 12 stored.

* **Identity** — the config addresses another dealer's inventory. Car-On was
  cloned from Tabangi and kept Tabangi's Convertus company id (`cp=3337`) inside
  a URL-encoded query parameter. The hostname guard compared only the outer host
  (genuinely caronauto.ca), passed it, and Car-On then published Tabangi's
  stock under its own name — 542 shared VINs, shown as a healthy 80% success.

Everything here is pure: no network, no database. The cross-dealer comparison
takes the other dealers' configs as an argument so callers decide how to load
them.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any
from urllib.parse import parse_qsl, urlparse

from .urls import EndpointError, validate_endpoint

# Query parameters that only say WHICH PAGE or HOW MANY — they differ between
# two requests to the same dealer, so they must not be part of an identity.
_PAGING_PARAMS = frozenset({
    "page", "pg", "pc", "pn", "pt", "p", "paged", "offset", "limit", "per_page",
    "perpage", "pagesize", "page_size", "size", "startindex", "endindex", "start",
    "count", "results", "dsp_limit", "dsp_page", "dsp_offset", "numberperpage",
})

# Parameter names that identify the dealer inside a shared platform API. Two
# dealers presenting the same value for one of these against the same API host
# and path are asking for the same inventory.
_IDENTITY_PARAMS = frozenset({
    "cp", "dp", "dealer_id", "dealerid", "dealer", "company_id", "companyid",
    "site_id", "siteid", "store_id", "storeid", "account", "account_id",
    "accountid", "org_id", "orgid", "client_id", "clientid", "location_id",
    "locationid", "customer_id", "customerid", "dealercode", "dealer_code",
    "dealership_id", "dealershipid", "rooftop", "rooftop_id",
})

_URL_KEYS = ("endpoint", "api_url", "list_url", "page_url", "base_url")
_MAX_DEPTH = 3


def _host(url: str) -> str:
    netloc = urlparse(url.strip()).netloc.lower().split("@")[-1].split(":")[0]
    return netloc[4:] if netloc.startswith("www.") else netloc


def _is_url(value: str) -> bool:
    return value.startswith(("http://", "https://"))


def _config_urls(cfg: dict[str, Any]) -> list[str]:
    urls = [cfg[k] for k in _URL_KEYS if isinstance(cfg.get(k), str)]
    if isinstance(cfg.get("endpoints"), list):
        urls += [u for u in cfg["endpoints"] if isinstance(u, str)]
    return [u for u in urls if _is_url(u)]


def _feed_urls(cfg: dict[str, Any]) -> list[str]:
    """The json_api endpoint(s) — the requests that return inventory data."""
    urls = [cfg["endpoint"]] if isinstance(cfg.get("endpoint"), str) else []
    if isinstance(cfg.get("endpoints"), list):
        urls += [u for u in cfg["endpoints"] if isinstance(u, str)]
    return urls


def _walk(url: str, depth: int = 0) -> Iterable[str]:
    """Yield `url` and every URL nested inside its query string, decoded.

    `?endpoint=https%3A%2F%2Fvms.example%2Fapi%3Fcp%3D3337` is how WordPress
    proxy plugins pass the real API address — the dealer's id lives in the inner
    URL, which a host comparison on the outer URL never sees."""
    yield url
    if depth >= _MAX_DEPTH:
        return
    for _, value in parse_qsl(urlparse(url).query, keep_blank_values=True):
        if _is_url(value):
            yield from _walk(value, depth + 1)


def _is_paging(name: str, value: str) -> bool:
    return name.lower() in _PAGING_PARAMS or "{page" in value


def identity_keys(cfg: dict[str, Any]) -> set[tuple[str, ...]]:
    """Every fingerprint under which this config addresses inventory.

    ("url", host, path, sorted non-paging params)  — the same request
    ("id",  host, path, param, value)              — the same dealer id on the
                                                     same API, whatever else differs
    """
    keys: set[tuple[str, ...]] = set()
    for top in _config_urls(cfg):
        for url in _walk(top):
            parsed = urlparse(url)
            params = [(k.lower(), v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)
                      if not _is_url(v) and not _is_paging(k, v)]
            host, path = _host(url), parsed.path.rstrip("/")
            if not params:
                # A feed whose dealer lives in its host (cardora.zopsoftware.com)
                # has nothing but paging in the query, yet two configs requesting
                # that exact feed still read one inventory. Only the API endpoint
                # counts: page addresses (list_url, base_url) say nothing alone.
                # A POST body (Typesense collection, search filters) is part of
                # the request, so it is part of the identity.
                if top in _feed_urls(cfg) and str(cfg.get("method", "GET")).upper() != "POST":
                    keys.add(("feed", host, path))
                continue
            keys.add(("url", host, path, "&".join(f"{k}={v}" for k, v in sorted(params))))
            for k, v in params:
                if k in _IDENTITY_PARAMS and v:
                    keys.add(("id", host, path, k, v))
    return keys


def find_identity_conflict(
    cfg: dict[str, Any],
    others: Iterable[tuple[int, str, dict[str, Any]]],
) -> str | None:
    """Return a message when `cfg` addresses inventory another dealer already
    owns, else None. `others` are (dealer_id, name, scrape_config) of every OTHER
    dealer — the caller excludes the dealer being edited."""
    mine = identity_keys(cfg)
    if not mine:
        return None
    for dealer_id, name, other_cfg in others:
        shared = mine & identity_keys(other_cfg or {})
        if not shared:
            continue
        # Prefer the most readable explanation: a named dealer id over a URL.
        ids = sorted(k for k in shared if k[0] == "id")
        if ids:
            _, host, _, param, value = ids[0]
            what = f"{param}={value} on {host}"
        else:
            what = f"the same request to {sorted(shared)[0][1]}"
        return (
            f"This config addresses {what}, which dealer #{dealer_id} ({name}) already "
            f"uses. Scraping it would store that dealer's vehicles under this one — "
            f"a copied template keeps the original dealer's identifiers and must be "
            f"edited, not just re-pointed."
        )
    return None


def structural_problems(adapter_mode: str, cfg: dict[str, Any] | None) -> list[str]:
    """Reasons this config cannot work as written. Empty when it is sound."""
    if not cfg:
        return []
    problems: list[str] = []

    if adapter_mode == "json_api":
        posts = str(cfg.get("method", "GET")).upper() == "POST" and cfg.get("body_template")
        endpoints = [cfg["endpoint"]] if isinstance(cfg.get("endpoint"), str) else []
        if isinstance(cfg.get("endpoints"), list):
            endpoints += [e for e in cfg["endpoints"] if isinstance(e, str)]
        for ep in endpoints:
            try:
                validate_endpoint(ep, base_url=cfg.get("url_base") or None)
            except EndpointError as exc:
                problems.append(str(exc))
        # A detail-mode feed reads its whole inventory from one index response,
        # so a total without {page} is expected there.
        if cfg.get("total_path") and not posts and not cfg.get("detail"):
            for ep in endpoints:
                if "{page}" not in ep and "{page_end}" not in ep:
                    problems.append(
                        f"'total_path' says this feed has many pages, but the endpoint "
                        f"has no {{page}} placeholder, so every request returns page 1 "
                        f"and the rest of the inventory is never read. Add {{page}} to the "
                        f"query (e.g. ...&page={{page}}) or remove 'total_path' if the "
                        f"feed really is a single page: {ep[:110]}"
                    )
                    break

    if adapter_mode == "browser" and isinstance(cfg.get("api_url"), str):
        api = cfg["api_url"]
        multi = cfg.get("api_total_path") or int(cfg.get("api_max_pages") or 0) > 1
        if multi and "{page}" not in api:
            problems.append(
                "'api_url' is meant to be paged (api_total_path / api_max_pages) but has "
                "no {page} placeholder, so every request returns the same page."
            )
    return problems
