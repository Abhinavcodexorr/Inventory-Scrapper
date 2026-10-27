"""A proxy URL must reach Playwright with its credentials split out.

Residential providers issue one URL with the credentials inline:

    http://user-abc-country-ca:s3cr3t@gate.provider.com:7000

Playwright does not read credentials from the server URL — it wants `username`
and `password` as separate keys. Passing the whole URL as `server` connects
*unauthenticated*, and the provider then returns an auth error on every
request. That surfaces as the target site appearing to block you, which is the
same symptom the proxy was bought to fix, so the wrong thing gets diagnosed.

Run:  python -m tests.test_proxy_config
"""
from __future__ import annotations

from dealer_scraper.adapters.base import chromium_launch_args, playwright_proxy


def test_credentials_are_split_out_of_the_url() -> None:
    got = playwright_proxy("http://user-abc-country-ca:s3cr3t@gate.provider.com:7000")
    assert got == {
        "server": "http://gate.provider.com:7000",
        "username": "user-abc-country-ca",
        "password": "s3cr3t",
    }, got
    # The credentials must NOT remain in the server URL.
    assert "@" not in got["server"]


def test_proxy_without_credentials_needs_no_auth_keys() -> None:
    assert playwright_proxy("http://10.0.0.5:8888") == {"server": "http://10.0.0.5:8888"}


def test_percent_encoded_password_is_decoded() -> None:
    """Providers percent-encode passwords containing @ or : — Playwright wants
    the decoded value, or authentication silently fails."""
    got = playwright_proxy("http://user:p%40ss%3Aword@gate.provider.com:7777")
    assert got["password"] == "p@ss:word", got


def test_scheme_and_port_are_preserved() -> None:
    assert playwright_proxy("socks5://u:p@host:1080")["server"] == "socks5://host:1080"
    assert playwright_proxy("http://host.example")["server"] == "http://host.example"


def test_unset_proxy_returns_none() -> None:
    """Callers pass the result straight through, so 'no proxy' must be None
    rather than an empty dict Playwright would try to honour."""
    assert playwright_proxy(None) is None
    assert playwright_proxy("") is None


def test_garbage_proxy_does_not_raise() -> None:
    """A malformed value must not take down a scrape at browser launch."""
    assert playwright_proxy("not a url") is None


def test_sandbox_flag_only_when_root() -> None:
    """Guarded here too: the proxy and root fixes ship together, and losing the
    sandbox for non-root users would be a silent security regression."""
    import os
    import unittest.mock as mock

    with mock.patch.object(os, "geteuid", lambda: 0):
        assert "--no-sandbox" in chromium_launch_args()
    with mock.patch.object(os, "geteuid", lambda: 1000):
        assert "--no-sandbox" not in chromium_launch_args()


def main() -> None:
    test_credentials_are_split_out_of_the_url()
    test_proxy_without_credentials_needs_no_auth_keys()
    test_percent_encoded_password_is_decoded()
    test_scheme_and_port_are_preserved()
    test_unset_proxy_returns_none()
    test_garbage_proxy_does_not_raise()
    test_sandbox_flag_only_when_root()
    print("PROXY CONFIG TESTS PASSED — credentials reach Playwright, sandbox kept for non-root")


if __name__ == "__main__":
    main()
