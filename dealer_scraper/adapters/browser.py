"""Browser-mode adapter (Playwright) for JS-rendered / bot-protected dealer sites.

Use this when a site has no clean public JSON API, or is behind a JS bot
challenge (e.g. AWS WAF, as clutch.ca is). A real browser executes the
challenge and carries its token cookie, then we read the rendered grid.

It extracts listing cards by a STABLE anchor (the vehicle detail link, e.g.
`a[href^='/vehicles/']`) rather than CSS classes, because sites like Clutch use
build-hashed class names that change on every deploy.

scrape_config (stored in dealers.scrape_config JSONB) for adapter_mode="browser":
{
  "list_url":        "https://www.clutch.ca/cars",
  "base_url":        "https://www.clutch.ca",
  "wait_selector":   "a[href^='/vehicles/']",   # element that proves the grid rendered
  "card_anchor":     "a[href^='/vehicles/']",    # one per vehicle; its card is an ancestor
  "id_regex":        "/vehicles/(\\d+)",         # pull external_id from the href
  "paginate":        "scroll",                    # scroll | url | click
  "page_url":        "https://www.clutch.ca/cars?page={page}",  # for paginate=url
  "next_selector":   "button[aria-label='Next']",  # for paginate=click
  "scroll_rounds":   40,                           # for paginate=scroll
  "max_pages":       60,                            # safety cap
  "total_selector":  "",                           # optional: element holding "3915 results"
  "headless":        true,                          # set false if WAF needs a visible browser
  "nav_timeout":     45000,
  "settle_ms":       1500,                          # CAP on the settle wait after each page/
                                                     # scroll/click (dealer_scraper/adapters/
                                                     # waits.py) — an already-rendered page
                                                     # returns in ~2 poll ticks, not this full ms
  "block_resources": true                           # abort image/font/media + tracker requests
}

Requires:  pip install playwright  &&  playwright install chromium
"""
from __future__ import annotations

import asyncio
import logging
import re
import sys
import time
from typing import Iterator
from urllib.parse import urljoin

from ..config import settings
from ..normalize import NormalizedVehicle, normalize
from ..timing import StageTimer
from .base import chromium_launch_args, configured_playwright_proxy, require_config
from .blocking import install_blocking, uninstall_blocking
from .waits import count_matching, wait_count_stable, wait_for_growth, wait_for_vehicles


def _safe_count(page, selector: str) -> int:
    """Element count for diagnostics only — never raises."""
    try:
        return int(page.evaluate("(sel) => document.querySelectorAll(sel).length", selector))
    except Exception:
        return -1

log = logging.getLogger("scraper.browser")


def _ensure_subprocess_capable_loop() -> None:
    """Make sure a loop created on this thread can spawn Playwright's Node driver.

    On Windows only the Proactor loop implements subprocess_exec; the Selector one
    raises NotImplementedError. uvicorn installs the *Selector* policy process-wide
    whenever it uses a subprocess (``--reload`` or ``--workers > 1``), so a scrape
    running on the API's thread pool would otherwise die the moment Playwright
    starts its driver. Policies only apply to loops created after this call, so the
    server's already-running loop is untouched.
    """
    if sys.platform != "win32":
        return
    policy = asyncio.get_event_loop_policy()
    if isinstance(policy, asyncio.WindowsProactorEventLoopPolicy):
        return
    log.info("Switching asyncio policy %s -> WindowsProactorEventLoopPolicy for Playwright",
             type(policy).__name__)
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

# JS run in-page: collect one record per vehicle-detail anchor, de-duplicated,
# returning the href and the visible text of its enclosing card.
#
# `incremental`: when true, the seen-set persists on `window` across calls (one
# scrape session never revisits a card already returned), so a same-document
# pagination mode (scroll/click) collects only the NEW cards each round instead
# of re-serializing the whole grid over CDP every time — O(new) instead of
# O(total) per round. `_iter_url` (which navigates, resetting `window` anyway)
# always passes incremental=false.
_COLLECT_JS = """
({anchorSel, cardSel, incremental}) => {
  if (incremental) {
    if (!window.__scrapeSeen) window.__scrapeSeen = new Set();
  }
  const seen = incremental ? window.__scrapeSeen : new Set();
  const out = [];
  for (const a of document.querySelectorAll(anchorSel)) {
    const href = a.getAttribute('href');
    if (!href || seen.has(href)) continue;
    seen.add(href);
    // Prefer the card container named in the config. The climb below is only a
    // fallback: "first ancestor over 40 chars" stops at whatever wrapper happens
    // to be chatty (a photo carousel saying "View All Photos 1 of 31" clears 40
    // on its own), which yields a card with no title, price or mileage in it.
    let card = cardSel ? a.closest(cardSel) : null;
    if (!card) {
      card = a;
      for (let i = 0; i < 6 && card.parentElement; i++) {
        card = card.parentElement;
        if ((card.textContent || '').length > 40) break;
      }
    }
    // Join individual text nodes with spaces so adjacent spans (e.g. model + trim,
    // or 'favorite' + year) don't glue together the way textContent does.
    const w = document.createTreeWalker(card, NodeFilter.SHOW_TEXT);
    const parts = []; let t;
    while ((t = w.nextNode())) { const v = (t.nodeValue || '').trim(); if (v) parts.push(v); }
    // Many dealer platforms publish the vehicle as data-* attributes on the card
    // element for their own analytics layer (Convertus/Achilles, DealerOn and
    // Dealer.com all do). That is strictly better than reading the rendered
    // text: it carries VIN, colour, transmission and odometer, which frequently
    // never appear on the card face at all, and it needs no locale-specific
    // parsing ("Kilometres: 53,862" defeats a number-then-unit regex).
    const attrs = {};
    if (card.dataset) { for (const k in card.dataset) attrs[k] = card.dataset[k]; }
    out.push({ href, text: parts.join(' ').replace(/\\s+/g, ' ').trim(), attrs });
  }
  return out;
}
"""

# Injected before page scripts run: removes the most common automation tells the
# WAF fingerprints (navigator.webdriver, missing plugins/languages, headless chrome).
_STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
Object.defineProperty(navigator, 'languages', { get: () => ['en-CA', 'en'] });
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
window.chrome = window.chrome || { runtime: {} };
const _q = navigator.permissions && navigator.permissions.query;
if (_q) {
  navigator.permissions.query = (p) =>
    p && p.name === 'notifications'
      ? Promise.resolve({ state: Notification.permission })
      : _q(p);
}
"""

def _dig_path(obj, path: str):
    """Follow a dotted path into nested dicts/lists; None if any hop is missing.

    Mirrors json_api's `_dig` so an api_field_map written for one adapter reads
    the same in the other.
    """
    if not path:
        return obj
    cur = obj
    for key in path.split("."):
        try:
            cur = cur[int(key)] if isinstance(cur, list) else cur[key]
        except (KeyError, IndexError, TypeError, ValueError):
            return None
    return cur


_YEAR = re.compile(r"(?<!\d)(19|20)\d{2}(?!\d)")
_MILEAGE = re.compile(r"([\d,]{3,})\s*(?:km|mi(?:les)?)\b", re.I)
_PRICE = re.compile(r"\$\s?([\d,]{3,})")
_LEAD_NOISE = re.compile(r"^(compare|sale|favorite|new|used|certified)\b", re.I)
_SOLD_BADGE = re.compile(r"^\s*sold\b", re.I)

#: Card `data-*` attribute names (camelCased by DOM `dataset`) that carry a
#: vehicle field directly, mapped to our field name. Several spellings per
#: field because the platforms disagree; first non-empty match wins.
#:
#: This is what lets a listing-only scrape populate VIN / colour / transmission
#: — fields otherwise NULL because the client declined detail-page scraping.
_CARD_ATTR_FIELDS: dict[str, tuple[str, ...]] = {
    "vin":            ("vehicleVin", "vin"),
    "year":           ("vehicleYear", "year", "modelYear"),
    "make":           ("vehicleMake", "make"),
    "model":          ("vehicleModel", "model"),
    "trim":           ("vehicleTrim", "trim"),
    "mileage":        ("vehicleOdo", "vehicleOdometer", "odometer", "mileage", "vehicleMileage"),
    "exterior_color": ("vehicleColour", "vehicleColor", "vehicleExteriorColor",
                       "exteriorColor", "colour", "color"),
    "interior_color": ("vehicleInteriorColour", "vehicleInteriorColor", "interiorColor"),
    "transmission":   ("vehicleTransmission", "transmission"),
    "fuel_type":      ("vehicleFueltype", "vehicleFuelType", "fuelType"),
    "body_type":      ("vehicleBodyStyle", "bodyStyle", "vehicleBody"),
    "drivetrain":     ("vehicleDrivetrain", "vehicleDriveTrain", "drivetrain"),
    "stock_number":   ("vehicleStock", "stockNumber", "stock"),
    "price":          ("vehicleInternetPrice", "vehiclePrice", "price"),
    "original_price": ("vehicleMsrp", "msrp", "vehicleRetailPrice"),
}


def parse_card_attrs(attrs: dict) -> dict:
    """Pull known vehicle fields out of a card's data-* attributes.

    Returns only keys that had a non-empty value, so the caller can layer this
    over text parsing without blanking a field the text did find.
    """
    out: dict = {}
    if not attrs:
        return out
    for field, names in _CARD_ATTR_FIELDS.items():
        for name in names:
            value = attrs.get(name)
            if value is None:
                continue
            value = str(value).strip()
            # "" and a literal "0" price are placeholders on several platforms.
            if not value or (field in ("price", "original_price") and value in ("0", "0.00")):
                continue
            out[field] = value
            break
    return out


def parse_card_text(text: str, price_label: str | None = None) -> dict:
    """Best-effort parse of a Clutch-style card string into listing fields.
    Example: 'Compare Sale favorite 2024 Chrysler Pacifica Touring-L • 30,690 km $44,990 $48,690 ...'

    price_label picks WHICH price when a card lists several ("Retail Price
    $54,600 … Sale Price $49,799"): pass e.g. "Sale Price" to take the amount
    following that label. Without it the first amount on the card wins, which on
    such a card is the pre-discount number, not what the vehicle is listed at.
    """
    d: dict = {}
    # A card that opens with a "Sold" badge is the dealer's sold archive, not
    # stock (cardora.ca: 613 of 800 cards). Passed on as the feed-stated status
    # so the engine stores it as sold instead of inflating active inventory.
    if _SOLD_BADGE.match(text):
        d["source_status"] = "Sold"
    price = None
    if price_label:
        price = re.search(re.escape(price_label) + r"\D{0,12}\$\s?([\d,]{3,})", text, re.I)
    if price is None:
        price = _PRICE.search(text)
    if price:
        d["price"] = price.group(1).replace(",", "")
        # A discounted card shows two amounts ("$44,990 $48,690"). The asking
        # price is captured above; keep the highest OTHER amount as the
        # pre-discount figure instead of discarding it. normalize._original_price
        # drops it again unless it really is higher than the asking price.
        amounts = [a.replace(",", "") for a in _PRICE.findall(text)]
        others = [a for a in amounts if a != d["price"]]
        if others:
            d["original_price"] = max(others, key=lambda a: float(a or 0))
    mil = _MILEAGE.search(text)
    if mil:
        d["mileage"] = mil.group(1).replace(",", "")

    ym = _YEAR.search(text)
    if ym:
        d["year"] = ym.group(0)
        # text between the year and the first '•' or 'km' is "Make Model Trim"
        after = text[ym.end():]
        # End the "Make Model Trim" run at the first thing that clearly is not part
        # of the name: a bullet, a mileage figure, a price, or the card's own
        # labels. Without the last two, trim swallows the whole rest of the card
        # ("Plus AWD Stock : 260508A Details Retail Price $54,600 Disclaimer…").
        after = re.split(r"[•|]|\d[\d,]*\s*(?:km|mi(?:les)?)\b|\$|\bstock\b|\bdetails\b",
                         after, flags=re.I)[0].strip(" -–—:")
        tokens = [t for t in after.split() if t]
        if tokens:
            d["make"] = tokens[0]
        if len(tokens) > 1:
            d["model"] = tokens[1]
        if len(tokens) > 2:
            d["trim"] = " ".join(tokens[2:])
    return d


def name_from_link(text: str, href: str) -> dict:
    """Make and model read from the card words that spell the start of the
    car's link slug. approvalgenie.ca prints "Kia Seltos" BEFORE the year, so
    parse_card_text (which reads the name after the year) finds no make; the
    link /used-car-inventory/hNLZn6yX/Kia-Seltos-2-0-S-X-Line/ confirms which
    card words are the name, so nothing else on the card can be mistaken for it."""
    slug = next((seg for seg in reversed(href.split("?")[0].split("/")) if seg), "").lower()
    # A price token ("$1") is never part of the name; keep it as a break so
    # its digits cannot extend "Nissan Kicks" to "Kicks 1" against nissan-kicks-1-6.
    words = [None if "$" in t else re.sub(r"[^\w-]", "", t) or None for t in text.split()]

    def spells(name: list[str]) -> bool:
        joined = "-".join(name).lower()
        return slug == joined or slug.startswith(joined + "-")

    for i, word in enumerate(words):
        if not word or not spells([word]):
            continue
        name = [word]
        for nxt in words[i + 1:]:
            if not nxt or not spells(name + [nxt]):
                break
            name.append(nxt)
        if len(name) >= 2:
            return {"make": name[0], "model": " ".join(name[1:])}
    return {}


# Collect all schema.org Vehicle JSON-LD nodes on the page (clean structured data).
_JSONLD_JS = """
() => {
  const out = [];
  for (const s of document.querySelectorAll('script[type="application/ld+json"]')) {
    let j; try { j = JSON.parse(s.textContent); } catch (e) { continue; }
    const arr = Array.isArray(j) ? j : (j['@graph'] || [j]);
    for (const n of arr) {
      const t = n && n['@type'];
      if (t && /Vehicle|Car/i.test(String(t))) out.push(n);
    }
  }
  return out;
}
"""


def _name_of(v):
    return v.get("name") if isinstance(v, dict) else v


def _last_seg(v):
    if not v:
        return None
    s = str(_name_of(v))
    return s.rsplit("/", 1)[-1] if "/" in s else s


def _num(v):
    return v.get("value") if isinstance(v, dict) else v


def _offer(offers):
    if isinstance(offers, list):
        offers = offers[0] if offers else None
    return offers if isinstance(offers, dict) else {}


def _original_offer_price(offer: dict):
    """Pre-discount price advertised inside a schema.org Offer, if any.

    Vendors express this in several ways; `priceSpecification` may itself be a
    list of specs where the higher one is the list price. normalize._original_price
    does the final sanity check that it actually exceeds the asking price, so this
    only needs to surface a candidate.
    """
    for key in ("listPrice", "highPrice", "msrp"):
        if offer.get(key):
            return offer[key]
    spec = offer.get("priceSpecification")
    if isinstance(spec, list):
        values = [s.get("price") for s in spec if isinstance(s, dict) and s.get("price")]
        return max(values, key=lambda v: float(str(v).replace(",", "") or 0)) if values else None
    if isinstance(spec, dict):
        return spec.get("listPrice") or spec.get("highPrice")
    return None


def _derive_trim(name, year, make, model):
    if not name:
        return None
    s = str(name)
    for tok in (str(year) if year else "", make or "", model or ""):
        if tok:
            s = s.replace(str(tok), " ", 1)
    return " ".join(s.split()).strip() or None


def map_jsonld(n: dict) -> dict:
    """Map a schema.org/Vehicle node to our raw field dict."""
    make = _name_of(n.get("brand")) or _name_of(n.get("manufacturer"))
    model = _name_of(n.get("model"))
    year = n.get("modelDate") or n.get("vehicleModelDate") or n.get("productionDate")
    offer = _offer(n.get("offers"))
    cond = _last_seg(n.get("itemCondition"))
    if cond:
        cond = cond.replace("Condition", "")
    drive = _last_seg(n.get("driveWheelConfiguration"))
    if drive:  # e.g. "4WDWheelDriveConfiguration" / "RearWheelDriveConfiguration" -> "4WD" / "Rear"
        for suffix in ("WheelDriveConfiguration", "DriveWheelConfiguration", "Configuration"):
            drive = drive.replace(suffix, "")
        drive = drive.strip() or None
    return {
        "make": make,
        "model": model,
        "year": year,
        "trim": _derive_trim(n.get("name"), year, make, model),
        "mileage": _num(n.get("mileageFromOdometer")),
        "price": offer.get("price") or (offer.get("priceSpecification") or {}).get("price"),
        "currency": offer.get("priceCurrency"),
        "condition": cond,
        "body_type": _last_seg(n.get("bodyType")),
        "drivetrain": drive,
        "fuel_type": _last_seg(n.get("fuelType")),
        "vin": n.get("vehicleIdentificationNumber"),
        # schema.org publishes colour on the Vehicle node — free to read when a
        # site provides it, and the only way to get colour now that detail pages
        # are out of scope.
        "exterior_color": _name_of(n.get("color")),
        "interior_color": _name_of(n.get("vehicleInteriorColor")),
        "transmission": _last_seg(n.get("vehicleTransmission")),
        # A struck-through "was" price, when the offer carries one.
        "original_price": _original_offer_price(offer),
    }


class BrowserAdapter:
    mode = "browser"
    REQUIRED_CONFIG = ("list_url",)

    def __init__(self, config: dict, *, timer: StageTimer | None = None):
        require_config(self.mode, config, *self.REQUIRED_CONFIG)
        self.cfg = config
        self.timer = timer or StageTimer()
        self.base_url: str = config.get("base_url", "")
        self.card_anchor: str = config.get("card_anchor", "a[href*='/vehicles/']")
        # Optional: the element that IS one listing card (e.g. ".hit"). Naming it
        # beats guessing by ancestor-text-length; see _COLLECT_JS.
        self.card_selector: str = config.get("card_selector", "")
        # Which price to take when a card shows several (e.g. "Sale Price").
        self.price_label: str = config.get("price_label", "")
        self.wait_selector: str = config.get("wait_selector", self.card_anchor)
        self.id_regex = re.compile(config.get("id_regex", r"/(?:vehicles|cars|inventory)/([\w-]+)"))
        self.paginate: str = config.get("paginate", "scroll")
        self.max_pages: int = int(config.get("max_pages", 60))
        self.scroll_rounds: int = int(config.get("scroll_rounds", 40))
        self.headless: bool = bool(config.get("headless", True))
        self.nav_timeout: int = int(config.get("nav_timeout", 45000))
        # A cap on how long the intelligent waits below may block, NOT a fixed
        # sleep duration — a settled page returns in a couple of poll ticks
        # (dealer_scraper/adapters/waits.py), not the full settle_ms.
        self.settle_ms: int = int(config.get("settle_ms", 1500))
        # How long to keep trying a pagination click. Playwright's 30s
        # actionability default is far too long for this: when a "load more"
        # control removes itself at the end of the list, every one of those
        # seconds is spent retrying a click that can never land, and the
        # resulting TimeoutError failed the entire run.
        self.click_timeout_ms: int = int(config.get("click_timeout_ms", 5000))
        self.total_selector: str = config.get("total_selector", "")
        # How many cards a full page of this listing holds (the `pc=N` / page-size
        # parameter in list_url). 0 = unknown, which keeps the old timing-only
        # behaviour. See _wait_for_expected.
        self.expect_per_page: int = int(config.get("expect_per_page", 0))
        self.expect_wait_ms: int = int(config.get("expect_wait_ms", self.nav_timeout // 2))
        # How many times a page that rendered NOTHING is reloaded before it is
        # accepted as empty. See _iter_url — an empty page is usually a slow
        # one, and believing it first time truncated runs silently.
        self.page_retries: int = max(1, int(config.get("page_retries", 2)))
        # --- in-page API mode -------------------------------------------------
        # Some sites render a grid the scraper struggles with but feed it from
        # an endpoint the PAGE is allowed to call. tabangimotors.com is one:
        # ajax-vehicles.php answers 200 to the page's own fetch and 403 to the
        # server, and its grid renders ~30s after load, missing the window
        # entirely on maybe a third of loads. Reading the DOM got 332 of 495
        # with pages 6/11/12 blank and duplicate pg results on top.
        #
        # When `api_url` is set, the adapter still navigates once — that is what
        # establishes the session the endpoint requires — then pages through the
        # JSON from inside the page context. No rendering, exact paging, and a
        # real total for the removal gate.
        self.api_url: str = str(config.get("api_url") or "")
        self.api_page_size: int = int(config.get("api_page_size", 100))
        self.api_list_path: str = str(config.get("api_list_path") or "")
        self.api_total_path: str = str(config.get("api_total_path") or "")
        self.api_field_map: dict = config.get("api_field_map") or {}
        self.api_max_pages: int = int(config.get("api_max_pages", 200))
        self.use_proxy: bool = bool(config.get("use_proxy", True))
        self.extract: str = config.get("extract", "dom")  # "jsonld" (schema.org) | "dom" (card text)
        self.block_resources: bool = bool(config.get("block_resources", settings.resource_blocking_default))
        self._total: int | None = None

    # -- helpers ----------------------------------------------------------
    def _to_vehicle(self, rec: dict) -> NormalizedVehicle | None:
        href = rec.get("href", "")
        m = self.id_regex.search(href)
        if not m:
            return None
        if "ld" in rec:                       # structured schema.org data (preferred)
            raw = map_jsonld(rec["ld"])
        else:                                  # fallback: parse the visible card text
            raw = parse_card_text(rec.get("text", ""), self.price_label)
            if not raw.get("make"):
                raw.update(name_from_link(rec.get("text", ""), href))
            # Card data-* attributes are structured, so they beat text parsing
            # wherever both produced something — but only where the attribute
            # actually has a value, so a platform that publishes two of these
            # doesn't blank the eight the text did find.
            for key, value in parse_card_attrs(rec.get("attrs") or {}).items():
                if value not in (None, ""):
                    raw[key] = value
        raw["external_id"] = m.group(1)
        raw["url"] = href if href.startswith("http") else self.base_url + href
        raw["_raw"] = rec.get("ld") or rec
        return normalize(raw)

    def _collect(self, page, *, incremental: bool = False) -> list[dict]:
        with self.timer.stage("extract"):
            if self.extract == "jsonld":
                nodes = page.evaluate(_JSONLD_JS)
                return [{"href": (n.get("url") or n.get("@id") or ""), "ld": n} for n in nodes]
            return page.evaluate(_COLLECT_JS, {
                "anchorSel": self.card_anchor, "cardSel": self.card_selector,
                "incremental": incremental,
            })

    # -- adapter API ------------------------------------------------------
    def fetch_total_count(self) -> int | None:
        return self._total  # populated during iter_vehicles if total_selector is set

    def iter_vehicles(self) -> Iterator[NormalizedVehicle]:
        from playwright.sync_api import sync_playwright  # lazy: only needed for browser mode

        _ensure_subprocess_capable_loop()
        seen: set[str] = set()
        with sync_playwright() as p:
            launch = {"headless": self.headless, "args": chromium_launch_args()}
            # Opt-out per dealer — see json_api's use_proxy. A site reachable
            # from the droplet should not inherit the proxy's downtime.
            proxy = configured_playwright_proxy() if self.use_proxy else None
            if proxy:
                launch["proxy"] = proxy
            with self.timer.stage("browser_launch"):
                browser = p.chromium.launch(**launch)
            ctx = browser.new_context(
                user_agent=("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"),
                viewport={"width": 1440, "height": 900},
                locale="en-CA",
                timezone_id="America/Toronto",
                extra_http_headers={"Accept-Language": "en-CA,en;q=0.9"},
            )
            ctx.add_init_script(_STEALTH_JS)  # hide automation fingerprints from the WAF
            get_blocked_count = (lambda: 0)
            if self.block_resources:
                get_blocked_count = install_blocking(ctx)
            page = ctx.new_page()
            try:
                # Warm-up: hit the homepage first so AWS WAF issues its token cookie
                # before we ask for the (protected) inventory API. wait_for_load_state
                # returns as soon as the network actually goes idle instead of always
                # sleeping the full warmup_ms — that cap only bites on a page that
                # never settles (e.g. polling widgets), which is rare on a homepage.
                warmup_ms = int(self.cfg.get("warmup_ms", 4000))
                if self.base_url:
                    try:
                        with self.timer.stage("warmup"):
                            page.goto(self.base_url, wait_until="domcontentloaded", timeout=self.nav_timeout)
                            page.wait_for_load_state("networkidle", timeout=warmup_ms)
                    except Exception:
                        pass

                first = self._page_url(1) if self.paginate == "url" else self.cfg["list_url"]

                # Retry: the vehicles API sometimes fails on the first render behind WAF.
                # A browser loading zero images/trackers is itself a bot signal on some
                # sites, so once resource blocking has had a full pass of attempts to
                # itself be the problem, the last attempt retries with blocking off.
                retries = int(self.cfg.get("load_retries", 3))
                found = False
                last_exc: Exception | None = None
                for attempt in range(1, retries + 1):
                    try:
                        # goto is INSIDE the retry: a bot-protected site can abort the
                        # navigation itself (net::ERR_ABORTED), and that must cost one
                        # attempt, not the whole scrape.
                        with self.timer.stage("nav"):
                            page.goto(first, wait_until="domcontentloaded", timeout=self.nav_timeout)
                            # api mode reads the feed, not the grid, so a late
                            # (or never) rendered grid must not fail the run.
                            if not self.api_url:
                                page.wait_for_selector(
                                    self.wait_selector, timeout=self.nav_timeout // retries)
                        found = True
                        break
                    except Exception as exc:
                        last_exc = exc
                        log.warning("Attempt %s/%s: %s — reloading after backoff",
                                    attempt, retries, str(exc).splitlines()[0][:120])
                        if attempt == retries - 1 and self.block_resources:
                            log.info("Retrying final attempt with resource blocking disabled")
                            blocked_so_far = get_blocked_count()
                            uninstall_blocking(ctx)
                            get_blocked_count = (lambda n=blocked_so_far: n)
                        if attempt < retries:  # no point sleeping after the LAST failed try
                            with self.timer.stage("wait"):
                                page.wait_for_timeout(3000 + attempt * 2000)  # backoff

                if not found:
                    shot = self.cfg.get("debug_screenshot", "scrape_debug.png")
                    try:
                        page.screenshot(path=shot, full_page=False)
                    except Exception:
                        shot = "(screenshot failed)"
                    title = body = ""
                    try:
                        title = page.title()
                        body = (page.text_content("body") or "")[:400].replace("\n", " ")
                    except Exception:
                        pass
                    # Distinguish "the page never loaded" from "the page loaded
                    # but the grid did not". Both used to raise the selector
                    # message blaming bot protection, so a Decodo 502 was
                    # reported as a WAF block on a site that was never reached
                    # — and cost hours of looking at the wrong thing.
                    if str(page.url).startswith("chrome-error") or not title:
                        raise RuntimeError(
                            f"Navigation to the listing failed — the page never loaded, so "
                            f"nothing could be read from it. This is a network/proxy problem, "
                            f"not the site's markup. "
                            f"last_error={type(last_exc).__name__ if last_exc else 'none'}: "
                            f"{str(last_exc).splitlines()[0][:160] if last_exc else ''} | "
                            f"url={page.url} | proxy={'on' if self.use_proxy else 'off'}"
                        )
                    raise RuntimeError(
                        f"'{self.wait_selector}' never appeared after {retries} tries, though "
                        f"the page itself loaded. Likely bot/WAF blocking the inventory API, "
                        f"or a grid that renders slower than the wait allows. "
                        f"url={page.url} | title={title!r} | body_start={body!r} | screenshot={shot}"
                    )
                # In api mode the grid is never read, so whether it painted is
                # irrelevant — waiting for it (and failing when it is late)
                # would reintroduce the exact flakiness this mode avoids. The
                # navigation above already did its job: it established the
                # session the endpoint requires.
                vehicles_ready = 1 if self.api_url else 0
                if not self.api_url:
                    with self.timer.stage("wait"):
                        # Wait for links that match id_regex — i.e. real vehicles.
                        # A bare anchor-count wait is satisfied by the site's own nav
                        # ("Inventory" links live on the listing path), so it settles
                        # instantly on a page with zero vehicles rendered.
                        vehicles_ready = wait_for_vehicles(
                            page, self.card_anchor, self.id_regex.pattern,
                            cap_ms=max(self.settle_ms, self.nav_timeout // 3),
                        )
                        # Page 1 is measured the same way as every later page: a
                        # count that merely stopped moving is not a finished grid.
                        self._wait_for_expected(page, "Page 1")
                if vehicles_ready <= 0:
                    # Never return "0 vehicles, no error". Explain what was seen
                    # so the failure is diagnosable from the run record alone.
                    anchors = _safe_count(page, self.card_anchor)
                    shot = self.cfg.get("debug_screenshot", "scrape_debug.png")
                    try:
                        page.screenshot(path=shot, full_page=False)
                    except Exception:
                        shot = "(screenshot failed)"
                    raise RuntimeError(
                        f"No vehicle links found on the listing page. "
                        f"{anchors} element(s) matched card_anchor "
                        f"'{self.card_anchor}', but none matched id_regex "
                        f"'{self.id_regex.pattern}' — so those are probably navigation "
                        f"links, not vehicles. Either the grid never rendered (client-side "
                        f"listing needing more time / blocked request) or id_regex does not "
                        f"match this site's detail URLs. url={page.url} | screenshot={shot}"
                    )

                try:
                    if self.total_selector:
                        txt = page.text_content(self.total_selector) or ""
                    else:
                        txt = page.text_content("body") or ""
                    mt = re.search(r"([\d,]{3,})\s*(?:results|cars|vehicles|listings)", txt, re.I)
                    self._total = int(mt.group(1).replace(",", "")) if mt else None
                except Exception:
                    self._total = None

                if self.api_url:
                    yield from self._iter_page_api(page, seen)
                elif self.paginate == "scroll":
                    yield from self._iter_scroll(page, seen)
                elif self.paginate == "url":
                    yield from self._iter_url(page, seen)
                else:
                    yield from self._iter_click(page, seen)
            finally:
                self.timer.incr("requests_blocked", get_blocked_count())
                browser.close()

    def _emit(self, records, seen) -> Iterator[NormalizedVehicle]:
        for rec in records:
            href = rec.get("href")
            if not href or href in seen:
                continue
            seen.add(href)
            with self.timer.stage("parse"):
                v = self._to_vehicle(rec)
            if v and v.external_id:
                yield v

    def _iter_scroll(self, page, seen) -> Iterator[NormalizedVehicle]:
        stagnant = 0
        hit_round_cap = True
        for _ in range(self.scroll_rounds):
            yield from self._emit(self._collect(page, incremental=True), seen)
            # Snapshot AFTER this round's collect, not before it — the count
            # that matters is "how many cards existed right before we scroll",
            # not "before we last collected", or a scroll that only reveals
            # cards this round's own collect already drained would read as
            # growth one round late.
            before = len(seen)
            with self.timer.stage("wait"):
                page.mouse.wheel(0, 20000)
                # A timeout here (no new card within settle_ms) IS the signal —
                # not an error to retry, and not worth sleeping past.
                grew = wait_for_growth(page, self.card_anchor, before, cap_ms=self.settle_ms)
            self.timer.incr("pages")
            if not grew:
                stagnant += 1
                if stagnant >= 3:  # nothing new after several scrolls -> end
                    hit_round_cap = False
                    break
            else:
                stagnant = 0
        if hit_round_cap:
            # Only reached here by exhausting scroll_rounds, never by confirmed
            # stagnation — one more incremental collect in case the last
            # round's growth hadn't been drained yet. (A stagnation break just
            # collected inside the loop; repeating it would return nothing.)
            yield from self._emit(self._collect(page, incremental=True), seen)

    def _wait_for_expected(self, page, label: str) -> None:
        """Block until the page holds `expect_per_page` cards, or give up.

        `wait_count_stable` returns as soon as a count holds for ~400ms, which
        on a progressively-rendered grid fires mid-paint. tabangimotors.com
        serves 50 cards per page and this collected 74 of 517 across the whole
        run, because each page was measured while it was still drawing.

        When the listing URL fixes the page size (`pc=50`), the honest wait is
        "until 50 cards exist". Falling short is logged, not raised: the final
        page legitimately holds fewer, and the run's own completeness check is
        what decides whether the total is trustworthy.
        """
        if not self.expect_per_page:
            return
        selector = self.card_selector or self.card_anchor
        deadline = time.monotonic() + (self.expect_wait_ms / 1000)
        count = 0
        while time.monotonic() < deadline:
            try:
                count = page.eval_on_selector_all(selector, "els => els.length")
            except Exception:
                count = 0
            if count >= self.expect_per_page:
                return
            page.wait_for_timeout(250)
        log.info("%s rendered %s/%s cards before the %sms cap — collecting what is there",
                 label, count, self.expect_per_page, self.expect_wait_ms)

    _API_FETCH_JS = """
    async (url) => {
      const r = await fetch(url, {
        credentials: 'include',
        headers: { 'accept': 'application/json, text/plain, */*',
                   'x-requested-with': 'XMLHttpRequest' },
      });
      const text = await r.text();
      try { return { status: r.status, body: JSON.parse(text) }; }
      catch (e) { return { status: r.status, body: null, raw: text.slice(0, 300) }; }
    }
    """

    def _iter_page_api(self, page, seen) -> Iterator[NormalizedVehicle]:
        """Page through the site's own JSON feed from inside the browser.

        The navigation that already happened in iter_vehicles is load-bearing:
        it is what gives this fetch the cookies and origin the endpoint checks.
        Calling the same URL from the server gets 403.
        """
        empty_streak = 0
        for pg in range(1, self.api_max_pages + 1):
            url = self.api_url.format(page=pg, page_size=self.api_page_size)
            with self.timer.stage("nav"):
                try:
                    res = page.evaluate(self._API_FETCH_JS, url)
                except Exception as exc:
                    log.warning("Inventory API page %s could not be fetched (%s)",
                                pg, type(exc).__name__)
                    break

            status = (res or {}).get("status")
            body = (res or {}).get("body")
            if status != 200 or body is None:
                # Never treat a non-200 as "end of listing" — that is how a
                # blocked run quietly reports a shrinking dealer.
                if seen:
                    log.warning("Inventory API page %s returned HTTP %s after %s vehicles",
                                pg, status, len(seen))
                    break
                raise RuntimeError(
                    f"Inventory API returned HTTP {status} on the first page "
                    f"({url[:160]}) — body={str((res or {}).get('raw'))[:200]!r}")

            if pg == 1 and self.api_total_path:
                total = _dig_path(body, self.api_total_path)
                try:
                    self._total = int(total) if total is not None else None
                except (TypeError, ValueError):
                    self._total = None

            items = _dig_path(body, self.api_list_path) or []
            if not isinstance(items, list) or not items:
                empty_streak += 1
                if empty_streak >= 2:
                    break
                continue
            empty_streak = 0
            self.timer.incr("pages")

            before = len(seen)
            for item in items:
                v = self._api_item_to_vehicle(item)
                if v is None or not v.external_id or v.external_id in seen:
                    continue
                seen.add(v.external_id)
                yield v
            log.info("Inventory API page %s: %s items, %s new (total so far %s)",
                     pg, len(items), len(seen) - before, len(seen))

            # A short page is the last page.
            if len(items) < self.api_page_size:
                break

    def _api_item_to_vehicle(self, item: dict) -> NormalizedVehicle | None:
        raw: dict = {}
        for our_field, source_key in self.api_field_map.items():
            raw[our_field] = _dig_path(item, source_key)
        if raw.get("url") and self.base_url and not str(raw["url"]).startswith("http"):
            raw["url"] = self.base_url.rstrip("/") + str(raw["url"])
        raw["_raw"] = item
        try:
            return normalize(raw)
        except Exception:
            return None

    def _page_url(self, pg: int) -> str:
        """`page_url` for page `pg`, made absolute. Configs written by hand often
        give just the path ("/used-car-inventory/?page={page}"), which Chromium
        refuses as an invalid URL on every retry (approvalgenie.ca)."""
        url = self.cfg.get("page_url", self.cfg["list_url"]).format(page=pg)
        return urljoin(self.cfg.get("list_url") or self.base_url or "", url)

    def _iter_url(self, page, seen) -> Iterator[NormalizedVehicle]:
        empty_streak = 0
        for pg in range(1, self.max_pages + 1):
            before = len(seen)
            if pg > 1:
                exhausted = False
                # A page that renders nothing is retried before it is believed.
                # tabangimotors.com paints its grid ~30s after domcontentloaded
                # and occasionally misses that window entirely; two slow pages
                # in a row used to trip the empty_streak break and end an
                # otherwise healthy run at 165 of 517. An empty page is far
                # more often a slow one than the end of the listing — the end
                # is what empty_streak is for, and it still applies after the
                # retries are spent.
                for attempt in range(1, self.page_retries + 1):
                    with self.timer.stage("nav"):
                        try:
                            page.goto(self._page_url(pg),
                                      wait_until="domcontentloaded", timeout=self.nav_timeout)
                        except Exception as exc:
                            # `max_pages` is a safety cap, not a page count — it is
                            # deliberately set above the real number, so a healthy
                            # run ALWAYS walks off the end of the listing. Sites
                            # answer a page past the end in different ways: an
                            # empty grid (handled by empty_streak below), a 404, or
                            # a 5xx. The last two make goto raise, and letting that
                            # propagate discards an otherwise complete scrape —
                            # observed on clutch.ca, which failed at page 105 of a
                            # ~96-page listing having already collected 3,263
                            # vehicles, and reported the whole nightly run failed.
                            #
                            # Only treat it as the end when we have actually
                            # collected something. Failing on the first navigation,
                            # or with nothing gathered, is a real failure and must
                            # stay loud.
                            if seen:
                                log.info("Page %s did not load after %s vehicles (%s) — "
                                         "treating the listing as exhausted",
                                         pg, len(seen), type(exc).__name__)
                                exhausted = True
                                break
                            raise
                    try:  # wait for this page's grid to render before scraping it
                        page.wait_for_selector(self.card_anchor, timeout=self.nav_timeout // 2)
                    except Exception:
                        pass
                    with self.timer.stage("wait"):
                        self._wait_for_expected(page, f"Page {pg}")
                        wait_count_stable(page, self.card_anchor, cap_ms=self.settle_ms)
                    # Each page is a fresh document (goto navigated away) — no
                    # incremental in-page seen-set to reuse, always a full collect.
                    yield from self._emit(self._collect(page), seen)
                    if len(seen) > before:
                        break                       # got vehicles, move on
                    if attempt < self.page_retries:
                        log.info("Page %s rendered nothing on attempt %s/%s — reloading",
                                 pg, attempt, self.page_retries)
                if exhausted:
                    break
            else:
                yield from self._emit(self._collect(page), seen)
            self.timer.incr("pages")
            if len(seen) == before:          # no new cars on this page
                empty_streak += 1
                if empty_streak >= 2:        # two empty pages in a row -> past the end
                    break
            else:
                empty_streak = 0

    def _iter_click(self, page, seen) -> Iterator[NormalizedVehicle]:
        from playwright.sync_api import Error as PlaywrightError  # lazy, like the rest

        nxt = self.cfg.get("next_selector")
        for _ in range(self.max_pages):
            yield from self._emit(self._collect(page, incremental=True), seen)
            self.timer.incr("pages")
            btn = page.query_selector(nxt) if nxt else None
            # is_enabled() alone misses a common "no more results" pattern:
            # the button stays in the DOM, not disabled, but CSS-hidden. A
            # click Playwright can't complete then burns its full actionability
            # timeout (default 30s) retrying against an invisible target.
            if not btn or not btn.is_enabled() or not btn.is_visible():
                break
            before = len(seen)  # snapshot right before the click, not before this round's collect
            with self.timer.stage("wait"):
                try:
                    # The visibility check above and this click are two separate
                    # moments, and the gap is exactly when a "load more" control
                    # removes itself: scrolling it into view loads the last batch,
                    # the control disappears, and the click never lands. That is
                    # the end of the list, not a broken scraper — but with
                    # Playwright's 30s actionability default it surfaced as a
                    # TimeoutError that failed the whole run and discarded a
                    # complete scrape (observed on grcars.ca at 250 vehicles).
                    btn.click(timeout=self.click_timeout_ms)
                except PlaywrightError as exc:
                    log.info("Pagination control stopped responding after %s vehicles "
                             "(%s) — treating the list as exhausted",
                             len(seen), type(exc).__name__)
                    break
                grew = wait_for_growth(page, self.card_anchor, before, cap_ms=self.settle_ms)
            if not grew:  # click produced nothing new -> treat "next" as exhausted
                break
