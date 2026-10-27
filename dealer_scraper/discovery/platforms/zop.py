"""Zop dealer sites: find the dealer's inventory feed when the page never calls it.

Every Zop dealer has a JSON feed at https://<slug>.zopsoftware.com/api/website/inventory
(10 vehicles a page, `?page=N`, a `pager.total`, and each vehicle's own `status`:
Instock / Sold / Unpublished). Zop's own app template (cardora.ca) calls that feed
from the browser, so traffic sniffing finds it. Its WordPress plugin template
(newcastleauto.ca) renders cards server-side and searches through Typesense
instead, so the feed never appears in the page's traffic and discovery reported
"not supported" for a platform already onboarded.

The slug is not always the dealer's domain, so candidates come from the page's
own HTML first. A feed is accepted only when its vehicles' page_url values point
at the dealer's own site: the slug is a guess, and a wrong guess must never hand
one dealer another dealer's inventory.
"""
from __future__ import annotations

import logging
import re
from urllib.parse import urlsplit

import httpx

from ..net.safe_http import UnsafeUrlError, safe_client, validate_public_url

log = logging.getLogger("scraper.discovery.zop")

MARKER_RE = re.compile(r"zopsoftware\.com|zopdealer", re.I)
_SLUG_IN_HTML_RE = re.compile(r"https?://([a-z0-9-]+)\.zopsoftware\.com/api/", re.I)
FEED_URL = "https://{slug}.zopsoftware.com/api/website/inventory"
_LIST_PATH = "inventory"
#: Share of a page's vehicles whose page_url must sit on the dealer's own host.
_MIN_OWN_HOST_SHARE = 0.8


def _bare_host(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def is_zop_site(html: str) -> bool:
    return bool(html and MARKER_RE.search(html))


def feed_slugs(base_url: str, html: str) -> list[str]:
    """Slugs to try, most trustworthy first: any feed URL the page names, then
    the dealer domain's first label (newcastleauto.ca -> newcastleauto)."""
    slugs: list[str] = []
    for slug in _SLUG_IN_HTML_RE.findall(html or ""):
        if slug.lower() not in slugs:
            slugs.append(slug.lower())
    label = _bare_host(base_url).split(".")[0]
    if label and label not in slugs:
        slugs.append(label)
    return slugs


def _belongs_to(items: list, dealer_host: str) -> bool:
    urls = [it.get("page_url") for it in items if isinstance(it, dict) and it.get("page_url")]
    if not urls:
        return False
    own = sum(1 for u in urls if _bare_host(u) == dealer_host or _bare_host(u).endswith("." + dealer_host))
    return own / len(urls) >= _MIN_OWN_HOST_SHARE


def probe_feed(base_url: str, html: str, client: httpx.Client | None = None) -> list[dict]:
    """Fetch pages 1 and 2 of this dealer's Zop feed, shaped like discovery's
    captured JSON hits ({"url", "json", "headers", "method", "post_body"}) so the
    normal json_api path scores it, detects `?page=` paging and maps its fields.
    Returns [] when no feed is found or none belongs to this dealer."""
    dealer_host = _bare_host(base_url)
    own_client = client is None
    client = client or safe_client(timeout=20.0, headers={"Accept": "application/json"})
    try:
        for slug in feed_slugs(base_url, html):
            feed = FEED_URL.format(slug=slug)
            hits: list[dict] = []
            for page in (1, 2):
                url = f"{feed}?page={page}"
                try:
                    validate_public_url(url)
                    resp = client.get(url)
                    data = resp.json() if resp.status_code == 200 else None
                except (httpx.HTTPError, UnsafeUrlError, ValueError) as exc:
                    log.debug("Zop feed %s unusable: %s", url, exc)
                    data = None
                items = data.get(_LIST_PATH) if isinstance(data, dict) else None
                if not isinstance(items, list) or not items:
                    break
                if page == 1 and not _belongs_to(items, dealer_host):
                    log.info("Zop feed %s does not list %s's vehicles — skipped", feed, dealer_host)
                    break
                hits.append({"url": url, "json": data, "headers": {}, "method": "GET",
                             "post_body": None})
            if hits:
                return hits
        return []
    finally:
        if own_client:
            client.close()
