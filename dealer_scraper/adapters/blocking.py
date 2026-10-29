"""Playwright network-resource blocking shared by the scrape adapter and (from
Phase 4 on) discovery's browser phase.

Only ever blocks bytes that extraction doesn't need: images/fonts/media, plus
known third-party analytics/tracker/ads scripts. XHR/fetch/document/websocket
requests are NEVER touched — those carry the JSON payloads and rendered markup
the adapters actually read. Bandwidth, not CPU, is the dominant per-scrape
running cost against a residential/datacenter proxy (HANDOFF/06), so this is
the single highest-leverage network optimization available.
"""
from __future__ import annotations

import re
import time
from typing import Callable
from urllib.parse import urlparse

# Resource *types* Chromium classifies requests into. Extraction only ever
# reads element URL attributes (img.src, srcset, ...), never the bytes, so
# these are safe to abort outright when block_images is on.
_BLOCKED_TYPES = frozenset({"image", "media", "font"})

# Request types that always carry data an adapter reads — never abort these
# regardless of host or config, even for a "noise" domain.
_NEVER_BLOCK_TYPES = frozenset({"xhr", "fetch", "document", "websocket"})

# Common third-party noise hostnames. Matched against the request's netloc
# (not the whole URL), so a query-string coincidence can't cause a false hit.
# Deliberately conservative — a miss here just costs bandwidth, not accuracy;
# a false positive could break a site that loads real content through one of
# these domains, so scripts are the only extra type filtered by host at all.
#: The separator class allows a HYPHEN as well as a dot. Analytics vendors fold
#: the product into the hostname itself — `browser-intake-datadoghq.com` is one
#: label, so a `(^|\.)` prefix never matched it and Datadog was exempt from a
#: blocklist that named it. It cost 4 MB over 96 requests on a single run.
_NOISE_RE = re.compile(
    r"(^|[.\-])(google-analytics\.com|googletagmanager\.com|doubleclick\.net|"
    r"facebook\.(?:com|net)|hotjar\.com|segment\.(?:com|io)|mixpanel\.com|"
    r"fullstory\.com|intercom\.io|zendesk\.com|criteo\.com|clarity\.ms|"
    r"amplitude\.com|snapchat\.com|tiktok\.com|pinterest\.com|linkedin\.com|"
    r"twitter\.com|x\.com|newrelic\.com|sentry\.io|bugsnag\.com|"
    r"cloudflareinsights\.com|onetrust\.com|cookielaw\.org|"
    # Observed consuming paid residential bandwidth on a single Clutch run
    # (Decodo top-targets): Datadog RUM 4.07 MB over 96 requests, plus
    # RudderStack, HubSpot and Bing UET.
    r"datadoghq\.com|datadoghq\.eu|ddog-gov\.com|"
    r"rudderlabs\.com|rudderstack\.com|"
    r"hs-analytics\.net|hs-banner\.com|hs-scripts\.com|hsforms\.net|hubspot\.com|"
    r"bing\.com|clarity\.microsoft\.com|"
    r"optimizely\.com|quantserve\.com|scorecardresearch\.com)$",
    re.I,
)


# The site's own scripts/stylesheets are fetched through the route instead of
# being left to the browser. sm360-hosted dealers (btownautosales.ca and ~60
# more) answer 429 when Chromium requests their ~20 scripts in parallel; the
# inventory script is among the refused ones, so the listing renders "No
# Results". Fetched through the route they arrive spaced out and are served —
# back to back on one IP: 3 refusals and 0 vehicles vs none and 12.
_PACED_TYPES = frozenset({"script", "stylesheet"})
_RETRY_DELAYS = (1.5, 3.0, 6.0)


def site_host_of(url: str) -> str:
    """Bare host (no port, no leading www.) used to recognise a site's own files."""
    host = urlparse(url).netloc.lower().split(":")[0]
    return host[4:] if host.startswith("www.") else host


def continue_paced(route, site_host: str | None) -> None:
    """Continue a request, fetching the site's own scripts/stylesheets through
    the route and retrying a 429 with back-off. Redirects are not followed
    here, so the browser re-requests each hop through the same route (and
    whatever guard sits on it). Falls back to a plain continue on any error."""
    req = route.request
    if not site_host or req.resource_type not in _PACED_TYPES or site_host_of(req.url) != site_host:
        return route.continue_()
    try:
        resp = route.fetch(max_redirects=0)
        for delay in _RETRY_DELAYS:
            if resp.status != 429:
                break
            time.sleep(delay)
            resp = route.fetch(max_redirects=0)
        route.fulfill(response=resp)
    except Exception:
        try:
            route.continue_()
        except Exception:
            pass


def install_blocking(context, *, block_images: bool = True,
                     site_url: str | None = None) -> Callable[[], int]:
    """Route every request in `context` through a filter.

    Returns a zero-arg getter for how many requests were aborted, meant to
    feed StageTimer.incr("requests_blocked") once iteration finishes.
    """
    blocked = {"n": 0}
    site_host = site_host_of(site_url) if site_url else None

    def handler(route):
        req = route.request
        rtype = req.resource_type

        # Navigation always proceeds — aborting it loses the page itself.
        if rtype == "document":
            return route.continue_()

        # Known analytics/telemetry hosts are dropped whatever the request TYPE.
        #
        # This check used to sit below the never-block list and apply only to
        # `script`, so RUM beacons — which are xhr/fetch — were exempt. Decodo's
        # top-targets for one Clutch run showed browser-intake-datadoghq.com at
        # 96 requests and 4 MB, more requests than clutch.ca itself, plus
        # RudderStack, HubSpot and Bing. That is residential bandwidth, billed
        # per GB, spent delivering someone else's telemetry.
        #
        # Safe because the list is a closed set of analytics vendors: no dealer
        # serves inventory from bat.bing.com. Anything not on the list keeps the
        # old protection.
        host = urlparse(req.url).netloc.lower()
        if _NOISE_RE.search(host):
            blocked["n"] += 1
            return route.abort()

        if rtype in _NEVER_BLOCK_TYPES:
            return route.continue_()
        if block_images and rtype in _BLOCKED_TYPES:
            blocked["n"] += 1
            return route.abort()
        continue_paced(route, site_host)

    context.route("**/*", handler)
    return lambda: blocked["n"]


def uninstall_blocking(context) -> None:
    """Undo install_blocking — used as a WAF-recovery fallback: a browser that
    never loads a single image or third-party script is itself a bot signal on
    some sites, so a load-retry loop can retry once with blocking off."""
    try:
        context.unroute("**/*")
    except Exception:
        pass


# -- async-Playwright mirrors (dealer_scraper/adapters/browser_async.py) --
# Same filter, same constants — only the route handler and the two calls that
# touch the page need `await` under the async API.

async def install_blocking_async(context, *, block_images: bool = True) -> Callable[[], int]:
    blocked = {"n": 0}

    async def handler(route):
        req = route.request
        rtype = req.resource_type
        if rtype in _NEVER_BLOCK_TYPES:
            return await route.continue_()
        if block_images and rtype in _BLOCKED_TYPES:
            blocked["n"] += 1
            return await route.abort()
        if rtype == "script":
            host = urlparse(req.url).netloc.lower()
            if _NOISE_RE.search(host):
                blocked["n"] += 1
                return await route.abort()
        await route.continue_()

    await context.route("**/*", handler)
    return lambda: blocked["n"]


async def uninstall_blocking_async(context) -> None:
    try:
        await context.unroute("**/*")
    except Exception:
        pass
