"""Analytics beacons must not be paid for at residential rates.

Decodo's top-targets for one Clutch run:

    clutch.ca                     48.81 MB   25 requests
    browser-intake-datadoghq.com   4.07 MB   96 requests   <-- telemetry
    cdn.rudderlabs.com             1.11 MB
    js.hs-analytics.net          793.85 KB
    bat.bing.com                 589.64 KB

Datadog sent more requests than the dealer's own site. The noise blocklist
already named several of these vendors, but the check sat below
_NEVER_BLOCK_TYPES and only ever applied to `script`, while RUM beacons are
xhr/fetch — so they were exempt by construction.

Run:  python -m tests.test_blocking_telemetry
"""
from __future__ import annotations

from dealer_scraper.adapters.blocking import install_blocking


class FakeRequest:
    def __init__(self, url: str, resource_type: str) -> None:
        self.url = url
        self.resource_type = resource_type


class FakeRoute:
    def __init__(self, url: str, resource_type: str) -> None:
        self.request = FakeRequest(url, resource_type)
        self.outcome: str | None = None

    def abort(self):
        self.outcome = "abort"

    def continue_(self):
        self.outcome = "continue"


class FakeContext:
    def __init__(self) -> None:
        self.handler = None

    def route(self, _pattern, handler):
        self.handler = handler


def _decide(url: str, rtype: str, *, block_images: bool = True) -> str:
    ctx = FakeContext()
    install_blocking(ctx, block_images=block_images)
    route = FakeRoute(url, rtype)
    ctx.handler(route)
    return route.outcome


def test_datadog_rum_beacons_are_blocked_even_though_they_are_xhr() -> None:
    """The exact request that cost 4 MB over 96 calls."""
    assert _decide("https://browser-intake-datadoghq.com/api/v2/rum", "xhr") == "abort"
    assert _decide("https://browser-intake-datadoghq.com/api/v2/rum", "fetch") == "abort"


def test_the_other_vendors_from_that_run() -> None:
    for url in (
        "https://cdn.rudderlabs.com/v1.1/rudder-analytics.min.js",
        "https://clutchqcl.dataplane.rudderstack.com/v1/track",
        "https://js.hs-analytics.net/analytics/1.js",
        "https://js.hs-banner.com/banner.js",
        "https://bat.bing.com/action/0",
    ):
        assert _decide(url, "script") == "abort", url
        assert _decide(url, "xhr") == "abort", url


def test_the_dealers_own_inventory_xhr_is_never_touched() -> None:
    """The whole reason xhr was exempt. It must stay exempt for real hosts."""
    assert _decide("https://www.clutch.ca/api/vehicles?page=2", "xhr") == "continue"
    assert _decide(
        "https://www.tabangimotors.com/wp-content/plugins/convertus-vms/"
        "include/php/ajax-vehicles.php?endpoint=x", "fetch") == "continue"
    assert _decide("https://cardora.zopsoftware.com/api/website/inventory", "xhr") == "continue"


def test_navigation_is_never_blocked() -> None:
    """Aborting the document loses the page — true even for a noise host."""
    assert _decide("https://www.clutch.ca/cars", "document") == "continue"
    assert _decide("https://bat.bing.com/anything", "document") == "continue"


def test_the_waf_challenge_host_still_loads() -> None:
    """clutch.ca sits behind AWS WAF; blocking its token endpoint would fail
    every run to save 5 MB."""
    assert _decide(
        "https://b8a9d40eb5a6.0fe78084.ca-central-1.token.awswaf.com/x", "xhr") == "continue"


def test_images_still_blocked_and_still_optional() -> None:
    assert _decide("https://www.clutch.ca/car.jpg", "image") == "abort"
    assert _decide("https://www.clutch.ca/car.jpg", "image", block_images=False) == "continue"


def main() -> None:
    test_datadog_rum_beacons_are_blocked_even_though_they_are_xhr()
    test_the_other_vendors_from_that_run()
    test_the_dealers_own_inventory_xhr_is_never_touched()
    test_navigation_is_never_blocked()
    test_the_waf_challenge_host_still_loads()
    test_images_still_blocked_and_still_optional()
    print("TELEMETRY BLOCKING TESTS PASSED — beacons no longer billed as residential traffic")


if __name__ == "__main__":
    main()
