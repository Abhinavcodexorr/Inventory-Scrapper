"""Does a proxy actually unblock a protected dealer site?

Answers one question before any money is spent on AWS: for a site that blocks us
today (clutch.ca returns 403), is the problem the IP address or the browser?

    export SCRAPER_PROXY='http://user:pass@host:port'
    python scripts/proxy_check.py

Just the proxy, no dealer site involved — does the gateway accept our
credentials, and does traffic really leave from a different IP?

    python scripts/proxy_check.py --auth-only

Credentials may also come from SCRAPER_PROXY_USERNAME / SCRAPER_PROXY_PASSWORD
(no percent-encoding needed). The password is never printed.

It runs the same four checks with and without the proxy, because "it works with
the proxy" only means something next to "it failed without it":

  1. plain HTTP to the site
  2. plain HTTP to the site's inventory API   (how clutch.ca is configured)
  3. headless browser                          (how a server scrapes)
  4. headed browser                            (what worked locally)

Why check 3 and 4 separately: on clutch.ca a HEADED browser succeeded and a
HEADLESS one was blocked from the SAME address. That means the site is
fingerprinting the browser, not only scoring the IP — and a proxy fixes only the
IP half. If headless-with-proxy still fails, a proxy alone will not make this
site work on a server; it needs a headed browser under a virtual display.

Exit code is 0 if the proxy makes the site readable, 1 if it does not, so this
can gate a deployment decision.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dealer_scraper.proxy import (  # noqa: E402
    ProxyConfigError, ProxySettings, configured_proxy, is_proxy_auth_failure, resolve_proxy,
)

# Targets default to the site we know is blocked.
DEFAULT_SITE = "https://www.clutch.ca/cars"
DEFAULT_API = (
    "https://api.clutch.ca/v1/vehicles/locations/"
    "56f159d4-49db-4a61-b2d8-d8784f10a184?page=0&pageSize=32"
)
DEFAULT_VEHICLE_SELECTOR = "a[href*='/vehicles/']"

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

OK, FAIL, SKIP = "PASS", "FAIL", "skip"
DEFAULT_ECHO = "https://api.ipify.org?format=json"
_AUTH_HELP = (
    "HTTP 407 — the proxy rejected authentication (this is the proxy, not the site).\n"
    "    Check, in the provider's dashboard: the exact username (including any\n"
    "    session/country suffix syntax) and password; whether the account uses IP\n"
    "    allowlisting instead of (or as well as) user:pass and this machine's IP is\n"
    "    on it; that the plan is active with traffic left; and that the port matches\n"
    "    the authentication method. Special characters in the password must be\n"
    "    percent-encoded in SCRAPER_PROXY, or put it in SCRAPER_PROXY_PASSWORD."
)


def _httpx_kwargs(proxy: ProxySettings | None) -> dict:
    # trust_env=False: "without proxy" must really mean without, not whatever
    # HTTP(S)_PROXY the shell happens to export.
    return {"proxy": proxy.httpx_proxy() if proxy else None, "trust_env": False}


def check_proxy_auth(proxy: ProxySettings, echo_url: str) -> int:
    """Authenticate against the proxy alone and compare exit IPs."""
    import httpx

    def exit_ip(p: ProxySettings | None) -> tuple[str | None, str]:
        try:
            with httpx.Client(timeout=30, **_httpx_kwargs(p)) as c:
                r = c.get(echo_url)
        except httpx.ProxyError as exc:
            if is_proxy_auth_failure(exc):
                return None, _AUTH_HELP
            return None, f"proxy error: {proxy.redact(str(exc))[:160]}"
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            return None, (f"could not reach {'the proxy gateway' if p else 'the echo service'} "
                          f"({type(exc).__name__}) — check host/port and outbound firewall")
        except httpx.HTTPError as exc:
            return None, f"{type(exc).__name__}: {proxy.redact(str(exc))[:160]}"
        if r.status_code == 407:
            return None, _AUTH_HELP
        if r.status_code != 200:
            return None, f"echo service answered HTTP {r.status_code}"
        try:
            return str(r.json().get("ip")), "ok"
        except Exception:
            return r.text.strip()[:64], "ok"

    direct, _ = exit_ip(None)
    proxied, detail = exit_ip(proxy)
    print(f"  direct exit IP:   {direct or 'unknown'}")
    if proxied is None:
        print(f"  [ FAIL ] through proxy: {detail}")
        return 1
    print(f"  proxied exit IP:  {proxied}")
    if direct and proxied == direct:
        print("  [ FAIL ] the proxied request left from this machine's own IP — the proxy is not in use")
        return 1
    print("  [  ok  ] proxy accepted the credentials and traffic leaves from a different IP")
    return 0


def _fmt(label: str, verdict: str, detail: str) -> str:
    mark = {OK: "  ok  ", FAIL: " FAIL ", SKIP: " skip "}[verdict]
    return f"  [{mark}] {label:<34} {detail}"


def check_http(url: str, proxy: ProxySettings | None) -> tuple[str, str]:
    """Plain GET. A challenge page is a failure even when the status is 200."""
    import httpx

    try:
        with httpx.Client(timeout=30, follow_redirects=True, **_httpx_kwargs(proxy),
                           headers={"User-Agent": UA, "Accept": "*/*"}) as c:
            r = c.get(url)
    except Exception as exc:
        if is_proxy_auth_failure(exc):
            return FAIL, "HTTP 407 — proxy rejected authentication (run --auth-only)"
        return FAIL, f"{type(exc).__name__}: {_scrub(proxy, str(exc))[:60]}"
    if r.status_code == 407:
        return FAIL, "HTTP 407 — proxy rejected authentication (run --auth-only)"

    ctype = (r.headers.get("content-type") or "").lower()
    if r.status_code in (401, 403, 429):
        return FAIL, f"HTTP {r.status_code} — blocked"
    # AWS WAF answers a challenge with 202 + HTML rather than an error status.
    if r.status_code == 202 and "html" in ctype:
        return FAIL, "HTTP 202 + HTML — WAF challenge"
    if r.status_code >= 400:
        return FAIL, f"HTTP {r.status_code}"
    return OK, f"HTTP {r.status_code}, {len(r.content):,} bytes"


def check_api(url: str, proxy: ProxySettings | None) -> tuple[str, str]:
    """The inventory API must return JSON with vehicles in it, not a challenge."""
    import httpx

    try:
        with httpx.Client(timeout=30, follow_redirects=True, **_httpx_kwargs(proxy),
                           headers={"User-Agent": UA, "Accept": "application/json"}) as c:
            r = c.get(url)
    except Exception as exc:
        if is_proxy_auth_failure(exc):
            return FAIL, "HTTP 407 — proxy rejected authentication (run --auth-only)"
        return FAIL, f"{type(exc).__name__}: {_scrub(proxy, str(exc))[:60]}"
    if r.status_code == 407:
        return FAIL, "HTTP 407 — proxy rejected authentication (run --auth-only)"

    if r.status_code != 200:
        return FAIL, f"HTTP {r.status_code} — blocked"
    try:
        data = r.json()
    except Exception:
        return FAIL, f"HTTP 200 but not JSON ({(r.headers.get('content-type') or '?')[:30]})"
    vehicles = data.get("vehicles") if isinstance(data, dict) else None
    if isinstance(vehicles, list) and vehicles:
        total = data.get("totalCount")
        return OK, f"{len(vehicles)} vehicles returned" + (f", totalCount={total}" if total else "")
    return FAIL, "JSON returned but no vehicles in it"


def _scrub(proxy: ProxySettings | None, text: str) -> str:
    return proxy.redact(text) if proxy else text


def check_browser(url: str, proxy: ProxySettings | None, headless: bool,
                   selector: str) -> tuple[str, str]:
    """Render the listing and count real vehicle links."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return SKIP, "playwright not installed"

    launch: dict = {"headless": headless,
                    "args": ["--disable-blink-features=AutomationControlled"]}
    if proxy:
        # Playwright ignores credentials embedded in "server"; passing the raw
        # SCRAPER_PROXY URL here made every browser check a 407.
        launch["proxy"] = proxy.playwright()

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(**launch)
            try:
                page = browser.new_context(
                    user_agent=UA, viewport={"width": 1440, "height": 900}).new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                try:
                    page.wait_for_selector(selector, timeout=25000)
                except Exception:
                    pass
                page.wait_for_timeout(4000)
                count = page.eval_on_selector_all(selector, "els => els.length")
                body = (page.text_content("body") or "").lower()
            finally:
                browser.close()
    except Exception as exc:
        return FAIL, f"{type(exc).__name__}: {_scrub(proxy, str(exc))[:60]}"

    if count > 0:
        return OK, f"{count} vehicle links rendered"
    for phrase in ("error when trying to get", "access denied", "are you a human",
                   "verify you are human", "unusual traffic"):
        if phrase in body:
            return FAIL, f"0 vehicles — page says '{phrase}'"
    return FAIL, "0 vehicles rendered"


def run_suite(label: str, proxy: ProxySettings | None, site: str, api: str,
               selector: str, skip_headed: bool) -> dict[str, str]:
    print(f"\n{label}")
    print("-" * 72)
    results: dict[str, str] = {}
    for name, fn in (
        ("HTTP → listing page", lambda: check_http(site, proxy)),
        ("HTTP → inventory API", lambda: check_api(api, proxy)),
        ("Browser (headless)", lambda: check_browser(site, proxy, True, selector)),
    ):
        verdict, detail = fn()
        results[name] = verdict
        print(_fmt(name, verdict, detail))
        time.sleep(2)  # be polite to the target between checks

    name = "Browser (headed)"
    if skip_headed:
        results[name] = SKIP
        print(_fmt(name, SKIP, "--skip-headed"))
    else:
        verdict, detail = check_browser(site, proxy, False, selector)
        results[name] = verdict
        print(_fmt(name, verdict, detail))
    return results


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--site", default=DEFAULT_SITE, help="listing page to test")
    ap.add_argument("--api", default=DEFAULT_API, help="inventory API endpoint to test")
    ap.add_argument("--selector", default=DEFAULT_VEHICLE_SELECTOR,
                    help="CSS selector for vehicle detail links")
    ap.add_argument("--proxy", default=None,
                    help="proxy URL (defaults to SCRAPER_PROXY from the environment / .env)")
    ap.add_argument("--auth-only", action="store_true",
                    help="only test that the proxy accepts our credentials; no dealer site")
    ap.add_argument("--echo-url", default=DEFAULT_ECHO,
                    help="IP-echo endpoint used by --auth-only")
    ap.add_argument("--skip-baseline", action="store_true",
                    help="skip the no-proxy run (faster, but the comparison is the point)")
    ap.add_argument("--skip-headed", action="store_true",
                    help="skip the headed browser check (needed on a headless server)")
    args = ap.parse_args()

    try:
        proxy = resolve_proxy(args.proxy) if args.proxy else configured_proxy()
    except ProxyConfigError as exc:
        print(f"Proxy setting is invalid: {exc}")
        return 2
    if proxy is None:
        print("No proxy given. Set SCRAPER_PROXY or pass --proxy.\n"
              "  export SCRAPER_PROXY='http://user:pass@host:port'")
        return 2

    print(f"Proxy:  {proxy.redacted}"
          f"  (username {'set' if proxy.username else 'not set'}, "
          f"password {'set' if proxy.password else 'not set'})")
    if args.auth_only:
        return check_proxy_auth(proxy, args.echo_url)
    print(f"Target: {args.site}")

    baseline: dict[str, str] = {}
    if not args.skip_baseline:
        baseline = run_suite("WITHOUT PROXY (baseline)", None, args.site, args.api,
                              args.selector, args.skip_headed)

    proxied = run_suite("WITH PROXY", proxy, args.site, args.api,
                         args.selector, args.skip_headed)

    print("\n" + "=" * 72)
    print("VERDICT")
    print("=" * 72)

    api_ok = proxied.get("HTTP → inventory API") == OK
    headless_ok = proxied.get("Browser (headless)") == OK
    headed_ok = proxied.get("Browser (headed)") == OK

    if api_ok or headless_ok:
        print("  The proxy makes this site readable the way a server would scrape it.")
        print("  -> Safe to deploy on AWS with this proxy.")
        if baseline and (baseline.get("HTTP → inventory API") == OK
                          or baseline.get("Browser (headless)") == OK):
            print("  NOTE: it also worked WITHOUT the proxy, so today's block may be")
            print("        intermittent. Re-run a few times before concluding.")
        return 0

    if headed_ok:
        print("  Headed works, headless does not — even through the proxy.")
        print("  The site is fingerprinting the BROWSER, not just the IP address,")
        print("  so a proxy alone will not make it work on a server.")
        print("  -> Needs a headed browser under a virtual display (Xvfb) on AWS,")
        print("     in addition to the proxy. Budget for that before deploying.")
        return 1

    print("  Blocked through the proxy on every path.")
    print("  -> This proxy does not solve it. Try a different provider or a")
    print("     residential (not datacenter) pool with sticky sessions before")
    print("     assuming the site cannot be scraped.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
