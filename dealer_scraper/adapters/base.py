"""Adapter contract. Every dealer is scraped through one of these.

The engine is dealer-agnostic: it only calls fetch_total_count() and iter_vehicles().
Adding an API-mode dealer needs NO new code — just a dealers row with scrape_config.
New site *types* (e.g. a GraphQL or browser adapter) are new subclasses.
"""
from __future__ import annotations

import os
from typing import Iterator, Protocol, runtime_checkable

from ..normalize import NormalizedVehicle
from ..proxy import ProxyConfigError, configured_proxy, resolve_proxy

# Args every Chromium launch gets. The two --disable-* flags hide automation
# fingerprints a WAF looks for.
_BASE_CHROMIUM_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-features=IsolateOrigins,site-per-process",
]


def playwright_proxy(proxy_url: str | None) -> dict | None:
    """Turn a proxy URL into Playwright's proxy dict, splitting out credentials.

    Residential proxy providers hand out a single URL with the credentials
    inline — `http://user:pass@gate.provider.com:7777`. Playwright does NOT
    read credentials from the server URL: it wants `username` and `password`
    as separate keys, and silently proxies unauthenticated without them, so
    every request comes back as a provider auth error that looks exactly like
    the target site blocking you. Splitting them here avoids diagnosing the
    wrong problem under time pressure.

    Returns None when no proxy is configured, so callers can pass it straight
    through.
    """
    try:
        proxy = resolve_proxy(proxy_url)
    except ProxyConfigError:
        return None
    return proxy.playwright() if proxy else None


def configured_playwright_proxy() -> dict | None:
    """Playwright proxy dict for the application proxy (SCRAPER_PROXY plus the
    optional SCRAPER_PROXY_USERNAME/PASSWORD). Unlike playwright_proxy(), a
    configured-but-unparseable proxy raises ProxyConfigError instead of
    returning None — silently scraping without the proxy would bill nothing and
    then fail on every protected site for a reason nobody would look for."""
    proxy = configured_proxy()
    return proxy.playwright() if proxy else None


def chromium_launch_args() -> list[str]:
    """Chromium args for this process, adding --no-sandbox only when root.

    Chromium refuses to start as root without --no-sandbox ("Running as root
    without --no-sandbox is not supported"), which is how a scrape fails on a
    server where the worker was deployed under the root account. Adding the
    flag unconditionally would drop the sandbox for every developer running
    locally as a normal user, so it is added only when it is actually needed.

    Running the worker as a non-root user remains the better arrangement — this
    keeps a root deployment working rather than endorsing one.
    """
    args = list(_BASE_CHROMIUM_ARGS)
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        args.append("--no-sandbox")
    return args


class ConfigError(ValueError):
    """A dealer's scrape_config is missing something its adapter needs.

    Raised while BUILDING the adapter, so an unusable config is rejected at
    onboarding with an actionable message instead of surfacing as a KeyError
    halfway through a background scrape.
    """


def require_config(mode: str, config: dict | None, *keys: str | tuple[str, ...]) -> None:
    """Each key is required; a TUPLE of keys means "at least one of these"."""
    cfg = config or {}
    missing: list[str] = []
    for key in keys:
        if isinstance(key, tuple):
            if not any(cfg.get(k) for k in key):
                missing.append(" or ".join(key))
        elif not cfg.get(key):
            missing.append(key)
    if missing:
        raise ConfigError(
            f"scrape_config for adapter_mode='{mode}' is missing required "
            f"{'keys' if len(missing) > 1 else 'key'}: {', '.join(missing)}"
        )


@runtime_checkable
class DealerAdapter(Protocol):
    mode: str
    # Keys the adapter cannot run without; the registry validates against these.
    REQUIRED_CONFIG: tuple[str, ...]

    def fetch_total_count(self) -> int | None:
        """The site's reported inventory count (for the removal safety gate). None if unknown."""
        ...

    def iter_vehicles(self) -> Iterator[NormalizedVehicle]:
        """Yield every vehicle in the dealer's inventory, handling pagination + politeness."""
        ...
