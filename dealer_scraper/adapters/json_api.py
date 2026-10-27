"""Config-driven JSON-API adapter.

Covers the common case: a dealer SPA backed by a paginated JSON endpoint
(clutch.ca works this way). Everything site-specific lives in the dealer's
scrape_config, so onboarding an API-mode dealer is DATA, not code.

Expected scrape_config shape (stored in dealers.scrape_config JSONB):
{
  "endpoint":    "https://api.example.com/v1/vehicles?page={page}&limit={page_size}",
  "page_size":   25,
  "start_page":  1,
  "page_step":   1,                    # optional; how much the page value grows per request.
                                        # Use page_size here (and start_page=0) for offset-style
                                        # APIs, e.g. "...?offset={page}&limit={page_size}".
  "list_path":   "results.vehicles",   # dotted path to the array of cars
  "total_path":  "results.total",      # dotted path to total count (optional)
  "field_map": {                        # our field  <-  source key (dotted paths allowed)
      "external_id": "id",
      "vin":         "vin",
      "make":        "make",
      "model":       "model",
      "year":        "year",
      "trim":        "trim",
      "mileage":     "mileage_km",
      "price":       "price",
      "url":         "vdp_url"
  },
  "headers":       {"Accept": "application/json"},
  "max_pages":     null,                 # safety cap; null = until empty/total reached

  # Optional. Some sites paginate a search-style POST endpoint instead of GET
  # query params (e.g. {"startIndex": 1, "endIndex": 24, ...} in the body).
  # "method" defaults to GET; set it to POST and give a "body" template — any
  # string value equal to "{page}", "{page_size}" or "{page_end}" (page +
  # page_size - 1, for inclusive start/end-index windows) is substituted with
  # the real (typed, not stringified) value each request:
  "method": "POST",
  "body": {
      "sortCode": "recents", "startIndex": "{page}", "endIndex": "{page_end}",
      "searchKey": "", "options": {"update": false, "platform": "ECOMM"}
  },

  # Optional. Some sites split inventory across several feeds (e.g. DealerOn
  # serves new and used from the same API under different page ids). List them
  # and they are scraped in order into ONE dealer's inventory; "endpoint" above
  # is the single-feed shorthand for the same thing.
  "endpoints": [
      "https://.../srp/vehicles/17485/1243176?pn={page_size}&pt={page}",
      "https://.../srp/vehicles/17485/1243170?pn={page_size}&pt={page}"
  ],
  # Optional. Constants for fields the payload does not carry at all. Applied
  # only where the mapping produced nothing, so real data always wins.
  "defaults":  {"currency": "USD"},
  # Optional. Absolutises a root-relative url value ("/inventory/vehicle/..").
  # Also the base a root-relative "endpoint" is resolved against.
  "url_base":  "https://www.example.com",

  # Optional. Next.js sites serve page data at /_next/data/<buildId>/..., and
  # the buildId changes on every deploy of the dealer's site — a hardcoded one
  # turns into a 404 overnight. Write "{build_id}" in the endpoint instead and
  # name a page whose __NEXT_DATA__ carries the current id; it is read once
  # per run before the first request:
  "next_build_id_url": "https://dealer.example/cars",

  # Optional. For feeds whose list response carries only SOME vehicles in full
  # plus an index of EVERY vehicle (HillzDealer's server-rendered /cars: 10
  # full records + {id, slug} for all of them). Vehicles in the list are used
  # as-is; every index entry not among them is fetched from "url", a template
  # filled from the index entry's own fields (and {build_id}). The index entry
  # is attached to each item as "_index", so field_map can read it
  # ("url": "_index.slug"). Detail URLs must stay on the template's host.
  "detail": {
      "index_path": "pageProps.vehiclesData.0.fullIds",
      "key":        "id",              # field shared by index entries and list items
      "url":        "https://dealer.example/_next/data/{build_id}{slug}.json",
      "item_path":  "pageProps.data"   # where the vehicle sits in a detail response
  }
}
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from string import Formatter
from typing import Any, Iterator
from urllib.parse import urlsplit

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from ..config import settings
from ..normalize import NormalizedVehicle, normalize
from ..proxy import ProxyAuthError, configured_proxy, is_proxy_auth_failure
from ..timing import StageTimer
from ..urls import EndpointError, TEMPLATE_FIELDS, validate_endpoint, validate_request_url
from .base import ConfigError, require_config
from .response_inspector import (
    ListPathError, ResponseFormatError, classify_html, dig, html_title, looks_like_json,
    resolve_list,
)

log = logging.getLogger("scraper.json_api")

__all__ = ["JsonApiAdapter", "BlockedByTarget", "ListPathError", "ProxyAuthError",
           "ResponseFormatError"]


class BlockedByTarget(RuntimeError):
    """The site answered with a bot-protection challenge rather than data.

    Distinct from a transport error so the retry ladder does not hammer a site
    that has just told us to stop, and so the run's error message names the real
    problem instead of a JSON parse failure.
    """


#: Request headers that are only meaningful for the single browser request they
#: were captured from. Persisting them makes every scrape replay one stale id.
_VOLATILE_REQUEST_HEADERS = (
    "traceparent", "tracestate", "x-datadog-trace-id", "x-datadog-parent-id",
    "x-datadog-origin", "x-datadog-sampling-priority", "b3", "x-request-id",
    "x-amzn-trace-id",
)

#: Target-site statuses worth another attempt. A 404/401/400 says the request
#: itself is wrong — repeating it only adds delay before the same answer.
_RETRY_STATUSES = frozenset({403, 408, 425, 429, 500, 502, 503, 504})

#: Fields whose absence is counted per run, so a field_map that stopped matching
#: shows up in the run's counters instead of as quietly blank columns.
_ESSENTIAL_FIELDS = ("external_id", "vin", "year", "make", "model", "price")

_BUILD_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,100}$")
_NEXT_DATA_RE = re.compile(r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', re.S)


def _dig(obj: Any, path: str) -> Any:
    """Follow a dotted path; supports list indices. Returns None if any hop is missing."""
    return dig(obj, path)


def _retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response is not None and exc.response.status_code in _RETRY_STATUSES
    # ProxyAuthError is never raised as a TransportError, so a 407 is never retried.
    return isinstance(exc, httpx.TransportError)


class _Pacer:
    """Spaces request STARTS by `delay` seconds, globally across however many
    worker threads are firing concurrently. The aggregate request RATE is
    exactly what a single `delay_seconds` config always promised (politeness
    unchanged); wall time improves because each request's own network latency
    now overlaps with the next request's spacing wait instead of stacking on
    top of it, the way a plain `time.sleep(delay)` between sequential
    requests did.
    """

    def __init__(self, delay: float):
        self.delay = delay
        self._lock = threading.Lock()
        self._next_allowed_at = 0.0

    def wait_turn(self) -> None:
        if not self.delay:
            return
        with self._lock:
            now = time.monotonic()
            start_at = max(now, self._next_allowed_at)
            self._next_allowed_at = start_at + self.delay
        sleep_for = start_at - time.monotonic()
        if sleep_for > 0:
            time.sleep(sleep_for)


class _RateGate:
    """Downgrades to fully-serialized fetching (+ 4x pacer spacing) the first
    time a 403/429 survives tenacity's own retries — never upgrades back for
    the rest of this endpoint. A second such signal after that means the
    site is still rejecting us even one at a time, so the run fails visibly
    rather than silently returning a partial inventory as complete.
    """

    def __init__(self, pacer: _Pacer):
        self._pacer = pacer
        self._level_lock = threading.Lock()
        self.serial_lock = threading.Lock()
        self.level = 0

    def signal(self, reason: str) -> None:
        with self._level_lock:
            if self.level >= 2:
                raise RuntimeError(f"json_api rate-limit backoff exhausted ({reason})")
            self.level += 1
            self._pacer.delay *= 4
            log.warning("json_api backoff level -> %s, pacer delay now %.2fs (%s)",
                       self.level, self._pacer.delay, reason)

    def guarded_call(self, fn, *args):
        if self.level >= 1:
            with self.serial_lock:
                return fn(*args)
        return fn(*args)


class JsonApiAdapter:
    mode = "json_api"
    REQUIRED_CONFIG = (("endpoint", "endpoints"), "field_map")

    def __init__(self, config: dict, *, timer: StageTimer | None = None):
        require_config(self.mode, config, *self.REQUIRED_CONFIG)
        self.cfg = config
        self.timer = timer or StageTimer()
        self.page_size: int = int(config.get("page_size", 25))
        self.start_page: int = int(config.get("start_page", 1))
        # Clamped to >=1: a 0 or negative step would never advance the cursor.
        self.page_step: int = max(1, int(config.get("page_step", 1)))
        # "" means the response body IS the array of vehicles.
        self.method: str = str(config.get("method", "GET")).upper()
        self.body_template: dict | None = config.get("body")
        self.list_path: str = config.get("list_path", "")
        self.total_path: str | None = config.get("total_path")
        self.field_map: dict[str, str] = config["field_map"]
        self.defaults: dict[str, Any] = config.get("defaults") or {}
        self.url_base: str = str(config.get("url_base") or "").rstrip("/")
        # One feed or many; a single "endpoint" is just a one-element list.
        # Validated HERE, so an empty, relative or placeholder endpoint is a
        # ConfigError at build time — never an UnsupportedProtocol mid-scrape.
        try:
            self.endpoints: list[str] = [
                validate_endpoint(ep, key="endpoint", base_url=self.url_base or None)
                for ep in (config.get("endpoints") or [config.get("endpoint")])
            ]
        except EndpointError as exc:
            raise ConfigError(str(exc)) from None
        self.build_id_url: str | None = config.get("next_build_id_url")
        self._build_id: str | None = None
        self._build_id_lock = threading.Lock()
        self.detail: dict | None = self._validate_detail(config.get("detail"))
        uses_build_id = any("{build_id}" in ep for ep in self.endpoints) or (
            self.detail is not None and "{build_id}" in self.detail["url"])
        if uses_build_id:
            if not self.build_id_url:
                raise ConfigError(
                    "scrape_config uses {build_id} but has no next_build_id_url to read "
                    "the current Next.js build id from (e.g. the dealer's /cars page).")
            try:
                self.build_id_url = validate_endpoint(
                    self.build_id_url, key="next_build_id_url", allowed_fields=frozenset())
            except EndpointError as exc:
                raise ConfigError(str(exc)) from None
        self.max_pages: int | None = config.get("max_pages")
        self.delay: float = float(config.get("delay_seconds", settings.default_delay_seconds))
        self.fetch_concurrency: int = max(1, int(
            config.get("fetch_concurrency", settings.per_dealer_fetch_concurrency)))
        self._pacer = _Pacer(self.delay)
        headers = {"User-Agent": settings.scraper_user_agent}
        headers.update(config.get("headers", {}))
        # Discovery captures request headers off a live browser session, which
        # sweeps up per-request tracing ids (traceparent, x-datadog-trace-id).
        # Replaying one recorded id on every request forever is both meaningless
        # and a distinctive fingerprint, so they are dropped.
        for volatile in _VOLATILE_REQUEST_HEADERS:
            headers.pop(volatile, None)
            headers.pop(volatile.title(), None)
        # Not every dealer needs the residential proxy, and routing one through
        # it anyway buys nothing but a shared point of failure: a Decodo 502
        # took out grcars and Cardora together, though both answer from the
        # droplet directly. Proxy is opt-out per dealer, default on so existing
        # dealers are unchanged.
        self.use_proxy: bool = bool(config.get("use_proxy", True))
        # Parsed by dealer_scraper/proxy.py; credentials go to httpx explicitly
        # (not re-parsed from a URL) and never appear in an error message.
        self._proxy = configured_proxy() if self.use_proxy else None
        self._client = httpx.Client(
            headers=headers,
            timeout=settings.http_timeout,
            follow_redirects=True,
            proxy=self._proxy.httpx_proxy() if self._proxy else None,
            # proxy=None alone is not enough: httpx still picks up HTTP_PROXY /
            # HTTPS_PROXY from the environment, so a dealer that opted out would
            # quietly keep using whatever the host exports. Opting out means out.
            trust_env=self.use_proxy,
        )
        # First page of each feed, cached by fetch_total_count() so the engine's
        # "how many should there be?" probe is not re-fetched by iter_vehicles().
        self._first_page: dict[str, Any] = {}
        # Identities already yielded this run: a page that repeats them is a
        # feed ignoring its page parameter, not more inventory.
        self._seen: set[tuple] = set()
        self._seen_lock = threading.Lock()

    # ------------------------------------------------------------ config
    @staticmethod
    def _validate_detail(detail: Any) -> dict | None:
        if detail is None:
            return None
        if not isinstance(detail, dict):
            raise ConfigError("scrape_config.detail must be an object.")
        missing = [k for k in ("index_path", "key", "url") if not detail.get(k)]
        if missing:
            raise ConfigError(f"scrape_config.detail is missing: {', '.join(missing)}")
        template = str(detail["url"])
        # The host must be literal: a template like "{url}" or "https://{host}/.."
        # would let a value inside the dealer's own response choose where we send
        # the next request.
        if not re.match(r"^https?://[^/{}]+/", template):
            raise ConfigError(
                "scrape_config.detail.url must start with a literal https://host/ — "
                "only the path may be filled from index fields.")
        try:
            fields = {f for _, f, _, _ in Formatter().parse(template) if f is not None}
            sample = template.format_map({f: "x" for f in fields})
            validate_endpoint(sample, key="detail.url", allowed_fields=frozenset())
        except (EndpointError, ValueError) as exc:
            raise ConfigError(f"scrape_config.detail.url is invalid: {exc}") from None
        return {"index_path": str(detail["index_path"]), "key": str(detail["key"]),
                "url": template, "item_path": str(detail.get("item_path") or ""),
                "list_key": str(detail.get("list_key") or detail["key"])}

    # ------------------------------------------------------------ requests
    def _url(self, endpoint: str, page: int) -> str:
        fields = {"page": page, "page_size": self.page_size, "page_end": page + self.page_size - 1}
        if "{build_id}" in endpoint:
            fields["build_id"] = self._resolve_build_id()
        return validate_request_url(endpoint.format(**fields))

    def _render_body(self, page: int) -> Any:
        """Substitute "{page}" / "{page_size}" / "{page_end}" leaf placeholders
        in the configured body template with their real, typed values (an int,
        not a stringified one — the source APIs send numbers, not strings)."""
        values = {"{page}": page, "{page_size}": self.page_size,
                  "{page_end}": page + self.page_size - 1}

        def render(node: Any) -> Any:
            if isinstance(node, dict):
                return {k: render(v) for k, v in node.items()}
            if isinstance(node, list):
                return [render(v) for v in node]
            if isinstance(node, str) and node in values:
                return values[node]
            return node

        return render(self.body_template or {})

    def _absolutise(self, value: Any) -> Any:
        if self.url_base and isinstance(value, str) and value.startswith("/"):
            return self.url_base + value
        return value

    def _proxy_auth_error(self) -> ProxyAuthError:
        where = self._proxy.redacted if self._proxy else "the environment's HTTP(S)_PROXY"
        return ProxyAuthError(
            f"The proxy ({where}) refused the request with HTTP 407 Proxy Authentication "
            f"Required — the proxy rejected our credentials, not the dealer's site. Check "
            f"the username/password (and any session/country syntax in the username) "
            f"against the provider's dashboard, whether the account requires this "
            f"server's IP on an allowlist, and whether the plan still has traffic. "
            f"Test the proxy alone with: python scripts/proxy_check.py --auth-only. "
            f"Not retried: repeating a rejected login cannot succeed."
        )

    @retry(
        reraise=True,
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=2, max=20),
        retry=retry_if_exception(_retryable),
    )
    def _request(self, url: str, label: Any, *, body: Any = None, expect: str = "json") -> Any:
        with self.timer.stage("wait"):
            self._pacer.wait_turn()
        with self.timer.stage("nav"):
            try:
                if body is not None:
                    resp = self._client.post(url, json=body)
                elif expect == "html":
                    resp = self._client.get(url, headers={"Accept": "text/html,*/*"})
                else:
                    resp = self._client.get(url)
            except httpx.ProxyError as exc:
                # https targets: the 407 arrives on the CONNECT tunnel as a
                # ProxyError (a TransportError), which used to be retried 4x.
                if is_proxy_auth_failure(exc):
                    raise self._proxy_auth_error() from None
                raise
            if resp.status_code == 407:  # plain-http targets: the 407 is the response
                raise self._proxy_auth_error()
            resp.raise_for_status()
            if expect == "html":
                return resp.text
            return self._parse_json(resp, label)

    def _parse_json(self, resp: httpx.Response, label: Any) -> Any:
        content_type = (resp.headers.get("content-type") or "").lower()
        if "json" in content_type or looks_like_json(resp.content):
            try:
                return resp.json()
            except ValueError:
                raise ResponseFormatError(
                    f"The dealer's API answered request {label} with {content_type or 'no content-type'} "
                    f"(HTTP {resp.status_code}) but the body is not valid JSON "
                    f"({len(resp.content)} bytes) — truncated or not a data response."
                ) from None
        text = resp.text
        kind = classify_html(text)
        title = html_title(text)
        if kind == "challenge" or resp.status_code == 202:
            # A bot-protection challenge is served as HTML with a perfectly
            # ordinary status (AWS WAF uses 202). Left alone, resp.json()
            # raises JSONDecodeError from deep inside the parser and the run
            # reports that instead of "we were blocked", which is unreadable
            # for an operator and hides the one fact that matters.
            raise BlockedByTarget(
                f"The dealer's API answered request {label} with "
                f"{content_type or 'no content-type'} instead of JSON "
                f"(HTTP {resp.status_code}{', page title: ' + repr(title) if title else ''}) — "
                f"this is a bot-protection challenge, not a data response. Slow the scrape "
                f"down (delay_seconds / fetch_concurrency) or route this dealer through a "
                f"residential proxy."
            )
        if kind == "login":
            raise ResponseFormatError(
                f"The endpoint answered request {label} with a login page "
                f"({title!r}, HTTP {resp.status_code}) — this feed needs an authenticated "
                f"session and cannot be scraped as configured."
            )
        raise ResponseFormatError(
            f"The endpoint answered request {label} with {content_type or 'no content-type'} "
            f"(HTTP {resp.status_code}{', page title: ' + repr(title) if title else ''}) instead "
            f"of JSON — the endpoint is probably wrong (an HTML page, not the data feed it serves)."
        )

    def _get(self, endpoint: str, page: int) -> Any:
        url = self._url(endpoint, page)
        if self.method == "POST":
            return self._request(url, page, body=self._render_body(page))
        return self._request(url, page)

    def _resolve_build_id(self) -> str:
        """Current Next.js buildId, read once per run from next_build_id_url."""
        with self._build_id_lock:
            if self._build_id is None:
                html = self._request(self.build_id_url, "next_build_id_url", expect="html")
                build_id = None
                if m := _NEXT_DATA_RE.search(html):
                    try:
                        build_id = json.loads(m.group(1)).get("buildId")
                    except (ValueError, AttributeError):
                        build_id = None
                if not build_id and (m := re.search(r'"buildId"\s*:\s*"([^"]+)"', html)):
                    build_id = m.group(1)
                if not build_id or not _BUILD_ID_RE.match(str(build_id)):
                    raise ResponseFormatError(
                        f"next_build_id_url ({self.build_id_url}) did not contain a Next.js "
                        f"buildId in __NEXT_DATA__ — the site may no longer be a Next.js "
                        f"pages-router site, or the URL is not one of its pages.")
                self._build_id = str(build_id)
                log.info("Resolved Next.js buildId %s from %s", self._build_id, self.build_id_url)
            return self._build_id

    def _page_value(self, k: int) -> int:
        """The k-th page value AFTER the first page (k=0 is the first page
        itself); handles both paginate-by-page-number and offset-style APIs
        (page_step == page_size, start_page == 0)."""
        return self.start_page + k * self.page_step

    def _first(self, endpoint: str) -> Any:
        if endpoint not in self._first_page:
            self._first_page[endpoint] = self._get(endpoint, self.start_page)
        return self._first_page[endpoint]

    def fetch_total_count(self) -> int | None:
        """Sum of every feed's reported total — the denominator the engine's
        removal safety gate compares the scraped count against."""
        if not self.total_path:
            return None
        total = 0
        for endpoint in self.endpoints:
            val = _dig(self._first(endpoint), self.total_path)
            try:
                total += int(val)
            except (TypeError, ValueError):
                return None  # one unreadable feed makes the sum meaningless
        return total

    # ------------------------------------------------------------ mapping
    def _map_item(self, item: dict) -> NormalizedVehicle:
        mapped: dict[str, Any] = {}
        for our_field, source_key in self.field_map.items():
            mapped[our_field] = _dig(item, source_key)
        for field, value in self.defaults.items():
            if mapped.get(field) in (None, ""):
                mapped[field] = value
        if self.url_base:
            mapped["url"] = self._absolutise(mapped.get("url"))
        mapped["_raw"] = item
        # Optional fields may be absent on any given listing; that is never an
        # error. Absences of the fields a listing is useless without are counted,
        # so a field_map that matched one dealer and not another is visible in
        # the run's counters instead of as blank columns.
        for field in _ESSENTIAL_FIELDS:
            if field in self.field_map and mapped.get(field) in (None, ""):
                self.timer.incr(f"missing_{field}")
        return normalize(mapped)

    @staticmethod
    def _identity(v: NormalizedVehicle) -> tuple | None:
        if not (v.external_id or v.vin or v.url):
            return None
        return (v.external_id, v.vin, v.url)

    def _emit(self, item: Any) -> NormalizedVehicle | None:
        """Map one item; None for a non-object or an identity already yielded."""
        if not isinstance(item, dict):
            self.timer.incr("items_not_objects")
            return None
        with self.timer.stage("parse"):
            v = self._map_item(item)
        ident = self._identity(v)
        if ident is None:
            self.timer.incr("vehicles_without_identity")
        else:
            with self._seen_lock:
                if ident in self._seen:
                    self.timer.incr("duplicates_skipped")
                    return None
                self._seen.add(ident)
        self.timer.incr("vehicles_mapped")
        return v

    def _yield_items(self, data: Any, items: list | None = None) -> Iterator[NormalizedVehicle]:
        """Yields the NEW vehicles in a page; returns how many there were."""
        new = 0
        for item in (items if items is not None else (_dig(data, self.list_path) or [])):
            v = self._emit(item)
            if v is not None:
                new += 1
                yield v
        return new

    # ------------------------------------------------------------ pagination
    def _fetch_page_worker(self, endpoint: str, page: int, gate: _RateGate) -> Any:
        """Runs on a worker thread. A 403/429 that survives tenacity's own
        retries signals the gate (serializing + slowing every subsequent
        fetch on this endpoint) and gets ONE retry of its own, now under
        that reduced pace — never silently dropped."""
        try:
            return gate.guarded_call(self._get, endpoint, page)
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code if exc.response is not None else None
            if status in (403, 429):
                gate.signal(f"http {status}")
                return gate.guarded_call(self._get, endpoint, page)
            raise

    def _fetch_known(self, endpoint: str, n_pages: int, gate: _RateGate) -> Iterator[NormalizedVehicle]:
        """Total page count is known upfront (a `total_path` was configured) —
        fan every remaining page out to the thread pool at once and yield
        vehicles as pages complete, in whatever order that happens to be
        (the engine dedups by identity, not by arrival order)."""
        page_vals = [self._page_value(k) for k in range(1, n_pages)]
        if not page_vals:
            return
        with ThreadPoolExecutor(max_workers=self.fetch_concurrency) as ex:
            futures = {ex.submit(self._fetch_page_worker, endpoint, p, gate): p for p in page_vals}
            errors: list[Exception] = []
            for fut in as_completed(futures):
                try:
                    data = fut.result()
                except (ProxyAuthError, ConfigError):
                    raise  # every other page would fail identically
                except Exception as exc:
                    errors.append(exc)
                    continue
                self.timer.incr("pages")
                yield from self._yield_items(data)
        # A minority of pages failing is tolerated (one flaky nav shouldn't
        # fail the whole scrape); a majority means the site is actively
        # rejecting us and the run must surface that, not under-report.
        if errors and len(errors) > len(page_vals) / 2:
            raise errors[0]

    def _fetch_unknown(self, endpoint: str, pages_done: int, gate: _RateGate) -> Iterator[NormalizedVehicle]:
        """No total known — probe in windows of `fetch_concurrency` pages,
        but PROCESS them in page order and stop at the first empty/short
        page, preserving the exact stop semantics the old strictly-sequential
        loop had. Pages fetched-but-never-processed within a window that
        stopped early are counted, never silently dropped from the report."""
        window = self.fetch_concurrency
        k = 1  # first page (k=0) was already fetched by the caller
        with ThreadPoolExecutor(max_workers=self.fetch_concurrency) as ex:
            while True:
                if self.max_pages and pages_done >= self.max_pages:
                    return
                batch = window
                if self.max_pages:
                    batch = min(batch, self.max_pages - pages_done)
                page_vals = [self._page_value(k + i) for i in range(batch)]
                futures = [ex.submit(self._fetch_page_worker, endpoint, p, gate) for p in page_vals]
                stop_idx = None
                for i, fut in enumerate(futures):
                    data = fut.result()  # propagates on failure — never mistaken for "end of data"
                    items = _dig(data, self.list_path) or []
                    if not items:
                        stop_idx = i
                        break
                    self.timer.incr("pages")
                    pages_done += 1
                    new = yield from self._yield_items(data, items)
                    if new == 0:
                        # Every vehicle on this page was already yielded: the
                        # feed is ignoring its page parameter (or wrapped
                        # around). More pages would only repeat it.
                        log.warning("Page %s of %s repeated earlier vehicles only — "
                                    "stopping pagination", page_vals[i], endpoint[:120])
                        self.timer.incr("repeated_pages")
                        stop_idx = i
                        break
                    if len(items) < self.page_size:
                        stop_idx = i
                        break
                if stop_idx is not None:
                    wasted = len(page_vals) - stop_idx - 1
                    if wasted:
                        # Concurrently in-flight when the true end was found;
                        # fetched for nothing, logged rather than hidden.
                        self.timer.incr("speculative_pages_wasted", wasted)
                    return
                k += batch

    # ------------------------------------------------------------ detail fan-out
    def _detail_url(self, entry: dict) -> str:
        template = self.detail["url"]
        fields = {f for _, f, _, _ in Formatter().parse(template) if f is not None}
        values: dict[str, Any] = {}
        for f in fields:
            if f == "build_id":
                values[f] = self._resolve_build_id()
            elif f in TEMPLATE_FIELDS:
                raise ConfigError(f"detail.url cannot use {{{f}}}")
            else:
                value = entry.get(f)
                if value is None or isinstance(value, (dict, list)):
                    raise ResponseFormatError(f"index entry has no usable '{f}' for detail.url: "
                                              f"{json.dumps(entry)[:160]}")
                values[f] = value
        url = validate_request_url(template.format_map(values))
        if urlsplit(url).netloc != urlsplit(template).netloc:
            raise ResponseFormatError(f"detail URL left the configured host: {url[:120]}")
        return url

    def _fetch_detail(self, entry: dict) -> dict | None:
        url = self._detail_url(entry)
        try:
            data = self._request(url, f"detail {entry.get(self.detail['key'])}")
        except httpx.HTTPStatusError as exc:
            if exc.response is not None and exc.response.status_code in (404, 410):
                # Sold between the index and this request — not an error.
                self.timer.incr("detail_gone")
                return None
            raise
        item = _dig(data, self.detail["item_path"])
        if not isinstance(item, dict):
            raise ListPathError(
                f"detail.item_path '{self.detail['item_path']}' is not an object in the "
                f"response for {url[:120]}")
        return item

    def _iter_details(self, first_data: Any, list_items: list) -> Iterator[NormalizedVehicle]:
        d = self.detail
        index = resolve_list(first_data, d["index_path"], where="(detail.index_path)")
        entries = [e for e in index if isinstance(e, dict) and e.get(d["key"]) is not None]
        by_key = {str(e[d["key"]]): e for e in entries}

        listed: set[str] = set()
        for item in list_items:
            if not isinstance(item, dict):
                continue
            key = _dig(item, d["list_key"])
            if key is not None:
                listed.add(str(key))
                if str(key) in by_key:
                    item = {**item, "_index": by_key[str(key)]}
            if (v := self._emit(item)) is not None:
                yield v
        self.timer.incr("pages")

        todo = [e for k, e in by_key.items() if k not in listed]
        log.info("Detail fan-out: %s listed in full, %s more in the index of %s",
                 len(listed), len(todo), len(by_key))
        if not todo:
            return
        errors: list[Exception] = []
        with ThreadPoolExecutor(max_workers=self.fetch_concurrency) as ex:
            futures = {ex.submit(self._fetch_detail, e): e for e in todo}
            for fut in as_completed(futures):
                try:
                    item = fut.result()
                except (ProxyAuthError, ConfigError):
                    raise
                except Exception as exc:
                    errors.append(exc)
                    continue
                self.timer.incr("detail_pages")
                if item is not None:
                    if (v := self._emit({**item, "_index": futures[fut]})) is not None:
                        yield v
        self.timer.incr("detail_errors", len(errors))
        if errors and len(errors) > len(todo) / 2:
            raise errors[0]

    # ------------------------------------------------------------ driver
    def _iter_endpoint(self, endpoint: str) -> Iterator[NormalizedVehicle]:
        first_data = self._first_page.pop(endpoint, None)
        if first_data is None:
            first_data = self._get(endpoint, self.start_page)
        # A list_path that does not exist in the response is a config error
        # (with candidate paths), not an empty inventory. A path that exists and
        # holds [] is a genuinely empty lot.
        first_items = resolve_list(first_data, self.list_path, where=f"from {endpoint[:120]}")
        if self.detail:
            yield from self._iter_details(first_data, first_items)
            return
        if not first_items:
            return
        self.timer.incr("pages")
        yield from self._yield_items(first_data, first_items)
        if len(first_items) < self.page_size:
            return  # everything fit on one page

        # A GET endpoint with no {page} placeholder cannot paginate: every
        # request returns the identical payload, so "stop when a page comes
        # back empty" never fires and the loop only ends on the task timeout.
        # cccmotors.ca was configured this way — a 199-vehicle feed emitted
        # 1,072,212 rows over 90 minutes, holding the single worker slot and
        # billing residential proxy traffic the whole time.
        #
        # POST feeds are exempt: they page through the body template, so the
        # URL is legitimately constant.
        if "{page}" not in endpoint and not (self.method == "POST" and self.body_template):
            log.info(
                "Endpoint has no {page} placeholder — treating it as a single-page "
                "feed (%s vehicles). Add {page} to paginate: %s",
                len(first_items), endpoint[:120],
            )
            return

        total: int | None = None
        if self.total_path:
            try:
                total = int(_dig(first_data, self.total_path))
            except (TypeError, ValueError):
                total = None

        gate = _RateGate(self._pacer)
        if total:
            n_pages = -(-total // self.page_size)  # ceil division
            if self.max_pages:
                n_pages = min(n_pages, self.max_pages)
            yield from self._fetch_known(endpoint, n_pages, gate)
        else:
            yield from self._fetch_unknown(endpoint, pages_done=1, gate=gate)

    def iter_vehicles(self) -> Iterator[NormalizedVehicle]:
        try:
            for endpoint in self.endpoints:
                yield from self._iter_endpoint(endpoint)
        finally:
            self._client.close()
            counters = self.timer.snapshot()["counters"]
            log.info(
                "json_api mapped %s vehicle(s); %s",
                counters.get("vehicles_mapped", 0),
                ", ".join(f"{k}={v}" for k, v in sorted(counters.items())
                          if k.startswith(("missing_", "duplicates", "vehicles_without", "detail_")))
                or "no missing essential fields",
            )
