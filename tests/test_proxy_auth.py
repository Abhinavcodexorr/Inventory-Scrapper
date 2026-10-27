"""A 407 is the proxy refusing us, not the dealer's site — and it is final.

The scraper reported `ProxyError: 407 Proxy Authentication Required` after
retrying it four times per page with exponential backoff (httpx raises a 407 on
an https CONNECT tunnel as ProxyError, a TransportError, which the retry policy
treated as a flaky network). These tests pin:

  * credentials with reserved characters reach the proxy byte-for-byte,
    whether percent-encoded in SCRAPER_PROXY or set separately;
  * a 407 becomes ProxyAuthError, is attempted exactly once, and names the
    proxy — not the dealer — as the problem;
  * the password never appears in an exception, a log line or a repr;
  * discovery's SSRF guard still runs when a proxy is configured.

All offline: a local fake proxy on 127.0.0.1 stands in for the provider.

Run:  python -m pytest tests/test_proxy_auth.py
"""
from __future__ import annotations

import base64
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

import dealer_scraper.config as cfg_mod
from dealer_scraper.adapters.base import configured_playwright_proxy
from dealer_scraper.adapters.json_api import JsonApiAdapter
from dealer_scraper.proxy import (
    ProxyAuthError, ProxyConfigError, is_proxy_auth_failure, redact_url, resolve_proxy,
)

PASSWORD = "p@ss:w/rd#1%?"          # every character that breaks a naive URL
ENCODED = "p%40ss%3Aw%2Frd%231%25%3F"


# ----------------------------------------------------------------- parsing
def test_percent_encoded_credentials_are_decoded() -> None:
    p = resolve_proxy(f"http://user-country-ca-session-x1:{ENCODED}@gate.example.com:7000")
    assert p.username == "user-country-ca-session-x1"
    assert p.password == PASSWORD
    assert p.server == "http://gate.example.com:7000"
    assert p.httpx_proxy().auth == ("user-country-ca-session-x1", PASSWORD)
    assert p.playwright() == {"server": "http://gate.example.com:7000",
                              "username": "user-country-ca-session-x1", "password": PASSWORD}


def test_separate_credentials_need_no_encoding_and_win() -> None:
    p = resolve_proxy("http://old:old@gate.example.com:7000", "user", PASSWORD)
    assert (p.username, p.password) == ("user", PASSWORD)


@pytest.mark.parametrize("url", [
    "http://user:pa/ss@gate.example.com:7000",
    "http://user:pa#ss@gate.example.com:7000",
    "http://user:pa?ss@gate.example.com:7000",
])
def test_unencoded_reserved_characters_are_a_config_error(url: str) -> None:
    with pytest.raises(ProxyConfigError) as exc:
        resolve_proxy(url)
    assert "pa" + url.split(":pa", 1)[1][:3] not in str(exc.value)
    assert "SCRAPER_PROXY_PASSWORD" in str(exc.value)


@pytest.mark.parametrize("url, needle", [
    ("user:pw@gate.example.com:7000", "no scheme"),
    ("ftp://gate.example.com:21", "not supported"),
    ("http://:7000", "hostname"),
])
def test_malformed_proxy_urls_are_rejected(url: str, needle: str) -> None:
    with pytest.raises(ProxyConfigError, match=needle):
        resolve_proxy(url)


def test_unset_proxy_is_none() -> None:
    assert resolve_proxy(None) is None and resolve_proxy("  ") is None


def test_redaction_never_shows_the_password() -> None:
    p = resolve_proxy(f"http://user:{ENCODED}@gate.example.com:7000")
    for text in (p.redacted, repr(p), redact_url(f"http://user:{ENCODED}@gate.example.com:7000"),
                 p.redact(f"failed via http://user:{ENCODED}@gate and {PASSWORD}")):
        assert PASSWORD not in text and ENCODED not in text, text
    assert p.redacted == "http://***:***@gate.example.com:7000"


def test_configured_playwright_proxy_uses_separate_credentials(monkeypatch) -> None:
    from pydantic import SecretStr

    monkeypatch.setattr(cfg_mod.settings, "scraper_proxy", "http://gate.example.com:7000")
    monkeypatch.setattr(cfg_mod.settings, "scraper_proxy_username", "user")
    monkeypatch.setattr(cfg_mod.settings, "scraper_proxy_password", SecretStr(PASSWORD))
    assert configured_playwright_proxy() == {
        "server": "http://gate.example.com:7000", "username": "user", "password": PASSWORD}


# ------------------------------------------------- a fake proxy on loopback
class _FakeProxy(BaseHTTPRequestHandler):
    """Plain-http forward proxy: records Proxy-Authorization, answers with
    either a JSON feed or 407 depending on `expected`."""

    expected: str | None = None
    seen: list = []

    def do_GET(self) -> None:  # noqa: N802
        header = self.headers.get("Proxy-Authorization")
        creds = base64.b64decode(header.split(" ", 1)[1]).decode() if header else None
        type(self).seen.append((self.path, creds))
        if creds != type(self).expected:
            self.send_response(407)
            self.send_header("Proxy-Authenticate", 'Basic realm="proxy"')
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        body = json.dumps({"vehicles": [{"stock": "A1", "make": "Ram"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:
        pass


@pytest.fixture
def fake_proxy():
    _FakeProxy.seen = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeProxy)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_address[1]
    server.shutdown()


CFG = {"endpoint": "http://dealer.example/feed.json", "list_path": "vehicles",
       "field_map": {"external_id": "stock", "make": "make"}, "delay_seconds": 0}


def test_reserved_character_credentials_reach_the_proxy_intact(fake_proxy, monkeypatch) -> None:
    _FakeProxy.expected = f"user:{PASSWORD}"
    monkeypatch.setattr(cfg_mod.settings, "scraper_proxy",
                        f"http://user:{ENCODED}@127.0.0.1:{fake_proxy}")
    got = list(JsonApiAdapter(CFG).iter_vehicles())
    assert [v.external_id for v in got] == ["A1"]
    assert _FakeProxy.seen == [("http://dealer.example/feed.json", f"user:{PASSWORD}")]


def test_407_is_proxy_auth_error_attempted_once_and_redacted(fake_proxy, monkeypatch, caplog) -> None:
    _FakeProxy.expected = "user:the-right-password"
    monkeypatch.setattr(cfg_mod.settings, "scraper_proxy",
                        f"http://user:{ENCODED}@127.0.0.1:{fake_proxy}")
    caplog.set_level(logging.DEBUG)
    with pytest.raises(ProxyAuthError) as exc:
        list(JsonApiAdapter(CFG).iter_vehicles())
    assert len(_FakeProxy.seen) == 1, "a rejected login must not be retried"
    msg = str(exc.value)
    assert "407" in msg and "proxy" in msg.lower() and "not the dealer" in msg
    for text in (msg, caplog.text):
        assert PASSWORD not in text and ENCODED not in text


def test_https_tunnel_407_is_not_retried(monkeypatch) -> None:
    """https targets: httpx raises the 407 as ProxyError from the CONNECT."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url)
        raise httpx.ProxyError("407 Proxy Authentication Required", request=request)

    a = JsonApiAdapter({**CFG, "endpoint": "https://dealer.example/feed.json", "use_proxy": False})
    a._client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(ProxyAuthError):
        list(a.iter_vehicles())
    assert len(calls) == 1


def test_target_site_errors_are_not_proxy_errors(monkeypatch) -> None:
    from tenacity import wait_none

    monkeypatch.setattr(JsonApiAdapter._request.retry, "wait", wait_none())
    for status, attempts in ((404, 1), (403, 4), (429, 4), (500, 4)):
        calls = []

        def handler(request, status=status):
            calls.append(1)
            return httpx.Response(status, json={"error": "nope"})

        a = JsonApiAdapter({**CFG, "use_proxy": False})
        a._client = httpx.Client(transport=httpx.MockTransport(handler))
        with pytest.raises(httpx.HTTPStatusError) as exc:
            a._get(a.endpoints[0], 1)
        assert exc.value.response.status_code == status
        assert not isinstance(exc.value, ProxyAuthError)
        assert len(calls) == attempts, f"HTTP {status}: {len(calls)} attempts"


def test_transient_proxy_failures_retry_a_bounded_number_of_times(monkeypatch) -> None:
    from tenacity import wait_none

    monkeypatch.setattr(JsonApiAdapter._request.retry, "wait", wait_none())
    calls = []

    def handler(request):
        calls.append(1)
        raise httpx.ProxyError("502 Bad Gateway", request=request)

    a = JsonApiAdapter({**CFG, "use_proxy": False})
    a._client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(httpx.ProxyError):
        a._get(a.endpoints[0], 1)
    assert len(calls) == 4


def test_is_proxy_auth_failure_only_matches_407() -> None:
    req = httpx.Request("GET", "https://x.example")
    assert is_proxy_auth_failure(httpx.ProxyError("407 Proxy Authentication Required", request=req))
    assert not is_proxy_auth_failure(httpx.ProxyError("502 Bad Gateway", request=req))
    assert not is_proxy_auth_failure(httpx.ConnectError("refused", request=req))


# ------------------------------------------------------- discovery client
def test_safe_client_keeps_ssrf_guard_when_proxied(monkeypatch) -> None:
    """Client(proxy=...) used to mount a plain proxy transport over
    SafeTransport, so every discovery fetch skipped SSRF validation."""
    from dealer_scraper.discovery.net.safe_http import SafeTransport, UnsafeUrlError, safe_client

    monkeypatch.setattr(cfg_mod.settings, "scraper_proxy", "http://u:p@proxy.example:9999")
    with safe_client() as client:
        assert isinstance(client._transport_for_url(httpx.URL("http://169.254.169.254/")),
                          SafeTransport)
        with pytest.raises(UnsafeUrlError):
            client.get("http://169.254.169.254/latest/meta-data/")


def main() -> None:
    """`python -m tests.test_proxy_auth` — several tests take pytest fixtures
    (parametrize, monkeypatch, caplog), so each named test runs through pytest."""
    import sys

    import pytest as _pytest

    tests = (
        test_percent_encoded_credentials_are_decoded,
        test_separate_credentials_need_no_encoding_and_win,
        test_unencoded_reserved_characters_are_a_config_error,
        test_malformed_proxy_urls_are_rejected,
        test_unset_proxy_is_none,
        test_redaction_never_shows_the_password,
        test_configured_playwright_proxy_uses_separate_credentials,
        test_reserved_character_credentials_reach_the_proxy_intact,
        test_407_is_proxy_auth_error_attempted_once_and_redacted,
        test_https_tunnel_407_is_not_retried,
        test_target_site_errors_are_not_proxy_errors,
        test_transient_proxy_failures_retry_a_bounded_number_of_times,
        test_is_proxy_auth_failure_only_matches_407,
        test_safe_client_keeps_ssrf_guard_when_proxied,
    )
    sys.exit(_pytest.main([__file__, "-q", "-p", "no:cacheprovider",
                           "-k", " or ".join(t.__name__ for t in tests)]))


if __name__ == "__main__":
    main()
