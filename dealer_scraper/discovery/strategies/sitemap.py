"""Sitemap/robots.txt exploration: discover candidate listing/detail URLs from
a site's own robots.txt + sitemap.xml, without needing a browser at all —
cheap (NETWORK cost class), so it can run before or alongside the
browser-driven exploration and catch sites where the inventory listing isn't
reachable from on-page nav links at all.

The detail-page URL shape is found by reusing
analyzers.dom_repetition.find_best_listing_cluster on the sitemap's own URLs
— the exact same open-ended clustering used for on-page links, since a
sitemap is just another source of candidate paths.

Multiple sitemaps are fetched in parallel using a thread pool to minimize
latency.
"""
from __future__ import annotations

import gzip
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

import httpx
from defusedxml import ElementTree  # untrusted remote XML — guards entity-expansion attacks

from ..analyzers.dom_repetition import LinkCluster, LinkRecord, find_best_listing_cluster
from ..heuristics import DEFAULT_HEURISTICS, HeuristicsConfig
from ..net.safe_http import read_capped, safe_client

log = logging.getLogger("scraper.discovery.sitemap")

_UA = "InventoryBot/1.0 (+contact@brainspack.com) discovery-sitemap"
_SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
_MAX_CHILD_SITEMAPS = 20
# An untrusted sitemap response is capped regardless of what Content-Length
# claims — chunked/gzip responses commonly omit it, and ElementTree.fromstring
# on a multi-GB body is a memory event on its own even before parsing it.
_MAX_SITEMAP_BYTES = 5_000_000


def _local_tag(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _make_client(client: httpx.Client | None, timeout: float) -> tuple[httpx.Client, bool]:
    """Returns (client, owns_it). Tests inject their own client (usually with
    a MockTransport) so no real network call is needed; production callers
    get an SSRF-hardened client (validates every hop, incl. redirects, and
    routes through SCRAPER_PROXY like the browser phase already does)."""
    if client is not None:
        return client, False
    return safe_client(timeout=timeout, headers={"User-Agent": _UA}), True


def fetch_robots_txt(base_url: str, *, timeout: float = 10.0,
                      client: httpx.Client | None = None) -> dict:
    """Returns {"sitemaps": [url,...], "disallow": [path,...], "fetched": bool}.
    Only `User-agent: *` rules are honored (discovery has no registered UA
    token of its own). Never raises."""
    result: dict = {"sitemaps": [], "disallow": [], "fetched": False}
    url = urljoin(base_url.rstrip("/") + "/", "robots.txt")
    c, owns = _make_client(client, timeout)
    try:
        resp = c.get(url)
        if resp.status_code != 200:
            return result
        result["fetched"] = True
        applies = False
        for raw_line in resp.text.splitlines():
            line = raw_line.split("#", 1)[0].strip()
            if not line or ":" not in line:
                continue
            key, _, value = line.partition(":")
            key, value = key.strip().lower(), value.strip()
            if key == "user-agent":
                applies = value == "*"
            elif key == "sitemap" and value:
                result["sitemaps"].append(value)
            elif key == "disallow" and applies and value:
                result["disallow"].append(value)
    except Exception as exc:
        log.debug("robots.txt fetch failed for %s: %s", base_url, exc)
    finally:
        if owns:
            c.close()
    return result


def _extract_locs(root: ElementTree.Element, child_tag: str) -> list[str]:
    locs = [el.text.strip() for el in root.findall(f".//sm:{child_tag}/sm:loc", _SITEMAP_NS)
            if el.text and el.text.strip()]
    if locs:
        return locs
    # Namespace-less fallback — some sitemaps omit the xmlns declaration.
    return [el.text.strip() for el in root.iter()
            if _local_tag(el.tag) == "loc" and el.text and el.text.strip()]


def fetch_sitemap_urls(
    sitemap_url: str, *, timeout: float = 10.0, max_depth: int = 2, max_urls: int = 5000,
    client: httpx.Client | None = None, _depth: int = 0,
) -> list[str]:
    """Recursively walk a sitemap (following <sitemap> index entries up to
    `max_depth`) and return every <loc> URL found, capped at `max_urls`.
    Transparently gzip-decompresses .xml.gz sitemaps. Never raises — a
    fetch/parse failure just returns whatever was collected so far."""
    if _depth > max_depth:
        return []
    c, owns = _make_client(client, timeout)
    urls: list[str] = []
    try:
        # Streamed rather than c.get(...): a size cap enforced AFTER the full
        # body is already buffered in memory is not a cap. read_capped()
        # aborts mid-read the moment an untrusted sitemap exceeds the limit,
        # regardless of what (or whether) Content-Length claims.
        with c.stream("GET", sitemap_url, headers={"Accept-Encoding": "gzip"}) as resp:
            if resp.status_code != 200:
                return urls
            body = read_capped(resp, _MAX_SITEMAP_BYTES)
        if sitemap_url.endswith(".gz"):
            try:
                body = gzip.decompress(body)
            except Exception:
                pass
        root = ElementTree.fromstring(body)
    except Exception as exc:
        log.debug("sitemap fetch/parse failed for %s: %s", sitemap_url, exc)
        return urls
    finally:
        if owns:
            c.close()

    if _local_tag(root.tag) == "sitemapindex":
        for child_url in _extract_locs(root, "sitemap")[:_MAX_CHILD_SITEMAPS]:
            if len(urls) >= max_urls:
                break
            urls.extend(fetch_sitemap_urls(child_url, timeout=timeout, max_depth=max_depth,
                                            max_urls=max_urls - len(urls), client=client, _depth=_depth + 1))
    else:
        # Sliced before extending, not after: a 50k-URL leaf sitemap is no
        # longer fully materialized in memory just to be cut down to size.
        remaining = max_urls - len(urls)
        if remaining > 0:
            urls.extend(_extract_locs(root, "url")[:remaining])
    return urls[:max_urls]


@dataclass
class SitemapDiscovery:
    sitemaps_found: list[str] = field(default_factory=list)
    urls_seen: int = 0
    best_cluster: LinkCluster | None = None
    detail_sample_urls: list[str] = field(default_factory=list)


def discover_via_sitemap(
    base_url: str, heuristics: HeuristicsConfig = DEFAULT_HEURISTICS, *,
    timeout: float = 10.0, client: httpx.Client | None = None,
) -> SitemapDiscovery:
    """robots.txt -> sitemap(s) -> open-ended URL-shape clustering. Runs
    entirely over httpx (NETWORK cost class) — no browser needed, so this can
    be tried even for sites the on-page nav-link crawl never reaches the
    listing from.

    Fetches multiple sitemaps in parallel to minimize latency."""
    c, owns = _make_client(client, timeout)
    try:
        robots = fetch_robots_txt(base_url, timeout=timeout, client=c)
        sitemap_urls = list(robots["sitemaps"])
        if not sitemap_urls:
            sitemap_urls = [urljoin(base_url.rstrip("/") + "/", "sitemap.xml")]

        host = (urlparse(base_url).netloc or "").lower()
        all_urls: list[str] = []

        def _fetch_one_sitemap(sm_url: str) -> list[str]:
            """Fetch a single sitemap and return URLs from same host."""
            urls = []
            for u in fetch_sitemap_urls(sm_url, timeout=timeout,
                                         max_urls=heuristics.link_shape_max_links, client=c):
                netloc = (urlparse(u).netloc or "").lower()
                if not netloc or netloc == host:
                    urls.append(u)
            return urls

        # Fetch multiple sitemaps in parallel, bounded to 5 concurrent
        with ThreadPoolExecutor(max_workers=min(5, len(sitemap_urls[:5]))) as executor:
            futures = [executor.submit(_fetch_one_sitemap, sm_url) for sm_url in sitemap_urls[:5]]
            for future in futures:
                try:
                    urls = future.result(timeout=timeout + 5.0)
                    all_urls.extend(urls)
                    if len(all_urls) >= heuristics.link_shape_max_links:
                        all_urls = all_urls[:heuristics.link_shape_max_links]
                        break
                except Exception as e:
                    log.debug("Parallel sitemap fetch failed: %s", e)
    finally:
        if owns:
            c.close()

    records = [LinkRecord(path=urlparse(u).path) for u in all_urls]
    best = find_best_listing_cluster(records, heuristics)
    sample = [u for u in all_urls if best and urlparse(u).path in best.paths][:8] if best else []

    return SitemapDiscovery(sitemaps_found=sitemap_urls, urls_seen=len(all_urls),
                             best_cluster=best, detail_sample_urls=sample)
