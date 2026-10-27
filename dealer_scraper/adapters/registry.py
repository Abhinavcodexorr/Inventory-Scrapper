"""Map a dealer's adapter_mode to an adapter instance built from its config."""
from __future__ import annotations

from urllib.parse import urlparse

from ..config import settings
from ..config_lint import structural_problems
from .base import ConfigError, require_config
from .json_api import JsonApiAdapter
from .browser import BrowserAdapter
from .browser_async import AsyncBrowserAdapter
from .stub import StubAdapter

# Register new site TYPES here. (browser = Playwright, for JS-rendered / bot-protected sites.)
#
# NOTE on providers (Clutch, DealerOn, Dealer Inspire, Dealer.com, ...): a provider
# is NOT a new adapter. Dealers.website_provider is a label; what actually drives
# scraping is adapter_mode + scrape_config. Every provider that exposes a JSON
# inventory endpoint reuses "json_api" with a different endpoint/field map, and
# every JS-rendered / bot-protected one reuses "browser" with different selectors.
# Only a genuinely new transport (e.g. GraphQL, sitemap XML) warrants a new class here.
#
# "browser" is special: it resolves to one of TWO classes depending on
# scrape_config["browser_engine"] (default settings.browser_engine_default) —
# AsyncBrowserAdapter (parallel page fetching) or the original BrowserAdapter
# (single-page, kept as an instant per-dealer rollback). Both share the exact
# same REQUIRED_CONFIG and scrape_config vocabulary, so callers that only need
# "does this mode/config combination make sense" (validate_scrape_config) can
# use either one interchangeably.
_ADAPTERS = {
    "json_api": JsonApiAdapter,
    "browser": BrowserAdapter,
    "stub": StubAdapter,
}


def _adapter_class(adapter_mode: str, scrape_config: dict | None = None):
    if adapter_mode == "browser":
        engine = (scrape_config or {}).get("browser_engine", settings.browser_engine_default)
        return AsyncBrowserAdapter if engine == "async" else BrowserAdapter
    try:
        return _ADAPTERS[adapter_mode]
    except KeyError:
        raise ValueError(f"Unknown adapter_mode '{adapter_mode}'. Known: {list(_ADAPTERS)}")


# Config keys that carry a URL pointing at the dealer's own website. If any of
# these points somewhere else, the scrape would file another site's inventory
# under this dealer.
#
# NOTE on json_api's "endpoint": deliberately NOT guarded here. Most real
# dealer sites source their inventory JSON from a third-party syndication
# platform (DealerOn, HomeNet, Dealer.com, ...) hosted on ITS OWN domain, not
# the dealer's — that is the normal, correct shape for this adapter, not a
# copy-paste mistake, and blocking it would reject the common case outright.
# "url_base" (the dealer's own site, used to absolutise a relative VDP url
# in the API response) is guarded instead: a genuinely copied provider
# template would carry the ORIGINAL dealer's url_base along with it, and that
# mismatch is still worth catching.
_URL_CONFIG_KEYS = ("list_url", "page_url", "base_url", "url_base", "sitemap_url")


def _host_of(url: str | None) -> str | None:
    """Bare hostname, lowercased, without credentials, port or a leading 'www.'."""
    if not url or not isinstance(url, str):
        return None
    netloc = urlparse(url.strip()).netloc.lower().split("@")[-1].split(":")[0]
    if not netloc:
        return None
    return netloc[4:] if netloc.startswith("www.") else netloc


def _same_site(host: str, dealer_host: str) -> bool:
    """True when both hostnames belong to the same site.

    Subdomains are accepted in either direction — inventory.example.com and
    example.com are the same dealer; clutch.ca and cardora.ca are not.
    """
    return (
        host == dealer_host
        or host.endswith("." + dealer_host)
        or dealer_host.endswith("." + host)
    )


def validate_scrape_config(
    adapter_mode: str,
    scrape_config: dict | None,
    dealer_base_url: str | None = None,
) -> None:
    """Raise ValueError/ConfigError if this pair could never scrape.

    Cheap and side-effect free (constructs nothing, opens no client), so the API
    can call it on dealer create/update and reject a config at onboarding rather
    than queueing a sync that is guaranteed to fail.

    When `dealer_base_url` is supplied, every URL in the config must point at that
    same site. This is the guard against copying another dealer's template (e.g.
    reusing a provider config) and silently scraping THEIR inventory into THIS
    dealer's record — corruption that is invisible until someone reads the data.
    """
    cls = _adapter_class(adapter_mode, scrape_config)
    require_config(adapter_mode, scrape_config, *getattr(cls, "REQUIRED_CONFIG", ()))

    if problems := structural_problems(adapter_mode, scrape_config):
        raise ValueError(problems[0])

    dealer_host = _host_of(dealer_base_url)
    if not dealer_host or not scrape_config:
        return
    for key in _URL_CONFIG_KEYS:
        host = _host_of(scrape_config.get(key))
        if host and not _same_site(host, dealer_host):
            raise ValueError(
                f"scrape_config.{key} points at '{host}' but this dealer's website is "
                f"'{dealer_host}'. Scraping it would store {host}'s vehicles under this "
                f"dealer. Update the config URLs to {dealer_host} — a copied provider "
                f"template keeps the original dealer's URLs and must be edited."
            )


def build_adapter(adapter_mode: str, scrape_config: dict, *, timer=None):
    """`timer` is a dealer_scraper.timing.StageTimer the adapter can record
    nav/wait/extract/parse durations into. Adapters that don't accept it
    (StubAdapter, any third-party adapter) fall back silently — instrumentation
    is additive, never a hard requirement to run."""
    cls = _adapter_class(adapter_mode, scrape_config)
    if timer is None:
        return cls(scrape_config)
    try:
        return cls(scrape_config, timer=timer)
    except TypeError:
        return cls(scrape_config)
