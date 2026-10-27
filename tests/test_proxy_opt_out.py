"""A dealer reachable from the droplet should not inherit the proxy's downtime.

On 24 Sep a Decodo 502 lasting under a minute failed all four dealers at once.
Two of them — grcars and Cardora — call a Zop API that answers the droplet
directly; they only went through the proxy because every adapter did, so a
proxy blip became a fleet outage for no reason.

That incident also showed the error text was lying. When `page.goto` fails, the
browser adapter raised "'<selector>' never appeared … Likely bot/WAF blocking"
with url=chrome-error://chromewebdata/ — a network failure reported as a WAF
block on a site that was never reached.

Run:  python -m tests.test_proxy_opt_out
"""
from __future__ import annotations

from dealer_scraper.adapters.browser import BrowserAdapter
from dealer_scraper.adapters.json_api import JsonApiAdapter

JSON_CFG = {
    "endpoint": "https://x.test/api?page={page}",
    "field_map": {"external_id": "id"},
}
BROWSER_CFG = {
    "list_url": "https://x.test/cars",
    "base_url": "https://x.test",
    "card_anchor": "a[href*='/v/']",
    "id_regex": r"/v/(\d+)",
}


def test_proxy_is_on_by_default_for_both_adapters() -> None:
    """Existing dealers carry no use_proxy key and must be unaffected."""
    assert JsonApiAdapter(JSON_CFG).use_proxy is True
    assert BrowserAdapter(BROWSER_CFG).use_proxy is True


def test_a_dealer_can_opt_out() -> None:
    assert JsonApiAdapter({**JSON_CFG, "use_proxy": False}).use_proxy is False
    assert BrowserAdapter({**BROWSER_CFG, "use_proxy": False}).use_proxy is False


def _proxy_transports(adapter: JsonApiAdapter) -> list:
    """Mounts with an actual transport behind them.

    httpx fills `_mounts` from the environment regardless, mapping patterns to
    None when nothing is proxied — so the count of mounts means nothing and
    only a non-None value indicates a real proxy.
    """
    return [t for t in getattr(adapter._client, "_mounts", {}).values() if t is not None]


def test_opting_out_actually_builds_a_direct_client(monkeypatch) -> None:
    """The flag has to reach httpx, not just sit on the object."""
    from dealer_scraper import config as cfg_mod
    monkeypatch.setattr(cfg_mod.settings, "scraper_proxy",
                        "http://user:pass@proxy.test:9999", raising=False)

    proxied = JsonApiAdapter(JSON_CFG)
    direct = JsonApiAdapter({**JSON_CFG, "use_proxy": False})

    assert _proxy_transports(proxied), "default dealer should still route via the proxy"
    assert not _proxy_transports(direct), "use_proxy=false must bypass it entirely"


class _Patch:
    """Minimal monkeypatch stand-in so `python -m tests.…` works like pytest."""

    def setattr(self, obj, name, value, raising=True):
        setattr(obj, name, value)


def main() -> None:
    test_proxy_is_on_by_default_for_both_adapters()
    test_a_dealer_can_opt_out()
    test_opting_out_actually_builds_a_direct_client(_Patch())
    print("PROXY OPT-OUT TESTS PASSED — a proxy blip no longer has to be a fleet outage")


if __name__ == "__main__":
    main()
