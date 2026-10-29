"""Discovery coordinator: orchestrates detection-only runs and returns scrape_config.

Discovery discovers — it finds inventory pages, detects inventory type, and
generates scraper configuration. It never scrapes vehicles, walks pagination,
or runs the production adapter. Those responsibilities belong to the scraper.
"""
from __future__ import annotations

import json
import logging
import re
import time
from collections import Counter
from dataclasses import dataclass, field as dc_field
from typing import Any, Callable
from urllib.parse import urlparse

from ..adapters.base import chromium_launch_args, configured_playwright_proxy
from ..adapters.blocking import _BLOCKED_TYPES, _NOISE_RE, continue_paced, site_host_of
from ..adapters.browser import _ensure_subprocess_capable_loop
from ..config import settings
from .analyzers.dom_repetition import LinkRecord, find_best_listing_cluster
from .analyzers.visual_structure import BoundingBox, analyze_grid
from .browser.antibot import AntiBotVerdict, classify as classify_antibot
from .browser.cards import detect_card_selector
from .cms.fingerprints import detect_cms_platform, get_most_likely_platform
from .browser.scroll import reveal_lazy_cards
from .browser.session import capture_card_boxes, capture_links
from .browser.waits import wait_for_stable
from .context import Candidate, CandidateKind, DiscoveryBudgetExceeded, DiscoveryCancelled, DiscoveryContext
from .decision import DecisionEngine
from .learning import apply_priors, read_priors, should_write_pattern, write_pattern
from .diagnostics import CandidateReport, DecisionReport, DiscoveryReport, SignalContributionOut, build_phases_from_stage_timings
from .evidence import Evidence, EvidenceKind
from .heuristics import DEFAULT_HEURISTICS, HeuristicsConfig
from .net.fieldmap import _build_field_map, _find_total_path, _needs_url_base
from .net.safe_http import UnsafeUrlError, validate_public_url
from .net.pagination_probe import (
    _dedup_dicts,
    _detect_body_pagination,
    _detect_pagination,
    _endpoint_key,
    _probe_body_pagination_via_http,
    _probe_pagination_via_http,
    _template_body,
    _template_endpoint,
)
from .net.scorer import _best_vehicle_json, _extra_request_headers
from .platforms import hillz, zop
from .strategies.exploration import PROBE_JS, _bare_host, _candidate_urls
from .strategies.pagination import (
    _build_detail_id_regex,
    _detect_dom_pagination,
    count_jsonld_vehicles,
)
from .strategies.sitemap import discover_via_sitemap

log = logging.getLogger("scraper.discovery")


def _report_throttled(ctx: DiscoveryContext, result) -> bool:
    """If the site refused its own scripts/data calls during the run, say so
    instead of blaming the layout, and return True. A page whose inventory
    script came back 429 renders an empty grid, which reads exactly like a
    layout the engine does not understand."""
    throttled = ctx.snapshots.get("throttled") or []
    if not throttled:
        return False
    statuses = sorted({s for s, _ in throttled})
    examples = ", ".join(dict.fromkeys(u for _, u in throttled[:3]))
    reason = (f"site refused {len(throttled)} of its own script/data request(s) "
              f"(HTTP {'/'.join(map(str, statuses))}), e.g. {examples}")
    ctx.evidence.add(Evidence(
        EvidenceKind.ANTIBOT_SIGNAL, "subresource_throttle", "", weight=1.0,
        payload={"unresolved": True, "count": len(throttled), "statuses": statuses},
        note="The site rate-limited the scripts that render its inventory."))
    ctx.report.antibot_events.append("rate_limit")
    ctx.report.add_note(
        f"The {reason}. Its inventory is rendered by those scripts, so the listing "
        f"showed no vehicles. Retry discovery later, or route this site through "
        f"SCRAPER_PROXY.")
    result.summary = (
        "The site is rate-limiting automated access: it refused the scripts that "
        "display its vehicles, so no inventory could be read. Retry discovery "
        "later, or enable a residential proxy for this site.")
    ctx.report.decision = DecisionReport(chosen_key=None, rationale=reason)
    return True


def _current_page_block(page, ctx: DiscoveryContext):
    """Classify whatever the page shows right now as blocked or not, using the
    last main-document response the page recorded. Never raises."""
    doc = ctx.snapshots.get("last_document") or {}
    url = getattr(page, "url", "") or ""
    # A navigation the site dropped outright (connection reset mid-run) leaves
    # Chrome's own error page and no response at all — the site has stopped
    # answering us, which is a block, not a layout we failed to read.
    if doc.get("failed") or url.startswith("chrome-error://"):
        return AntiBotVerdict(blocked=True, reasons=["navigation failed: site stopped responding"])
    # Only trust a status recorded for the page actually shown; otherwise it is
    # a stale 200 from an earlier, healthy load.
    if doc.get("url") and url and doc["url"].rstrip("/") != url.rstrip("/"):
        doc = {}
    try:
        body = page.text_content("body") or ""
    except Exception:
        body = ""
    try:
        title = page.title() or ""
    except Exception:
        title = ""
    return classify_antibot(status_code=doc.get("status"), headers=doc.get("headers"),
                            body_text=f"{title}\n{body}", heuristics=ctx.heuristics)


@dataclass
class DiscoveryResult:
    base_url: str
    ok: bool = False
    confidence: str = "failed"          # high | medium | low | failed
    # Operational verdict the admin UI acts on:
    #   ready         - engine handles this site as-is, safe to enable
    #   needs_review  - config derived but weak evidence; verify first run
    #   unsupported   - our engine cannot scrape this site without custom work
    verdict: str = "unsupported"
    requires_custom_work: bool = True
    summary: str = ""                   # one plain-English line for the admin
    scrape_config: dict = dc_field(default_factory=dict)
    adapter_mode: str = "browser"
    vehicles_found: int = 0
    reported_total: int | None = None
    # CMS/platform the site was fingerprinted as, when recognised. Carried through
    # to the dealer row so same-platform dealers can reuse a proven config.
    detected_platform: str | None = None
    listing_url: str | None = None
    pagination: str | None = None
    extract: str | None = None
    notes: list[str] = dc_field(default_factory=list)
    run_id: int | None = None
    report: DiscoveryReport | None = None

    def as_dict(self) -> dict:
        return {
            "base_url": self.base_url, "ok": self.ok, "confidence": self.confidence,
            "verdict": self.verdict, "requires_custom_work": self.requires_custom_work,
            "summary": self.summary,
            "adapter_mode": self.adapter_mode, "scrape_config": self.scrape_config,
            "vehicles_found": self.vehicles_found, "reported_total": self.reported_total,
            "detected_platform": self.detected_platform,
            "listing_url": self.listing_url, "pagination": self.pagination,
            "extract": self.extract, "notes": self.notes,
        }


class DiscoveryCoordinator:
    def __init__(
        self,
        base_url: str,
        heuristics: HeuristicsConfig | None = None,
        *,
        headless: bool = True,
        budget_seconds: float | None = None,
        max_candidates: int = 6,
        progress: Callable[[str, int, int], None] | None = None,
        check_cancel: Callable[[], None] | None = None,
        db: Any | None = None,
        run_id: int | None = None,
    ) -> None:
        self.base_url_in = base_url
        self.heuristics = heuristics or DEFAULT_HEURISTICS
        self.headless = headless
        self.budget_seconds = budget_seconds or self.heuristics.default_budget_seconds
        self.max_candidates = max_candidates
        self.progress = progress or (lambda phase, done, total: None)
        self.check_cancel = check_cancel or (lambda: None)
        self.decision = DecisionEngine(self.heuristics)
        self.db = db  # SQLAlchemy session for learning
        self.run_id = run_id  # Discovery run ID for pattern writes

    def run(self) -> DiscoveryResult:
        """Open the dealer's site and derive a scrape_config. Never raises."""
        _ensure_subprocess_capable_loop()

        base_url = self.base_url_in.strip().rstrip("/")
        if not urlparse(base_url).scheme:
            base_url = "https://" + base_url
        host = _bare_host(base_url)

        ctx = DiscoveryContext(
            base_url=base_url, host=host, heuristics=self.heuristics,
            deadline=time.monotonic() + self.budget_seconds,
            progress=self.progress, check_cancel=self.check_cancel,
        )
        result = DiscoveryResult(base_url=base_url)

        # SSRF validation at API boundary (schemas.py) prevents unsafe URLs
        # from creating a run row. The Playwright route guard below catches
        # any requests that somehow bypass that check.

        # Read learned patterns from prior runs to accelerate discovery
        log.info("Discovery starting", extra={"run_id": self.run_id, "base_url": base_url, "host": host})

        if self.db is not None:
            try:
                priors = read_priors(self.db, host)
                if priors:
                    apply_priors(ctx, priors)
                    log.debug("Applied %d learned priors", len(priors), extra={"run_id": self.run_id, "host": host})
            except Exception as exc:
                log.debug("Learning prior read failed", exc_info=True, extra={"run_id": self.run_id, "host": host})

        # Cheap (no browser) — robots.txt + sitemap.xml, purely corroborating
        try:
            with ctx.timer.stage("sitemap"):
                self._run_sitemap_phase(ctx)
        except Exception:
            log.debug("Sitemap phase failed", exc_info=True, extra={"run_id": self.run_id, "base_url": base_url})

        try:
            self._run_browser_phase(ctx, result)
        except DiscoveryCancelled:
            result.summary = "Discovery was cancelled."
            ctx.report.add_note("Discovery cancelled by request.")
            ctx.report.decision = DecisionReport(chosen_key=None, rationale="cancelled")
            log.info("Discovery cancelled", extra={"run_id": self.run_id, "base_url": base_url})
        except DiscoveryBudgetExceeded as exc:
            result.summary = (
                "Discovery budget exhausted before a complete configuration could be derived — "
                "manual review recommended."
            )
            ctx.report.add_note(str(exc))
            ctx.report.decision = DecisionReport(chosen_key=None, rationale="budget_exhausted")
            log.info("Discovery budget exhausted", extra={"run_id": self.run_id, "base_url": base_url})
        except Exception as exc:
            log.exception("Discovery failed", extra={"run_id": self.run_id, "base_url": base_url})
            result.summary = (
                "Discovery could not complete for this site — it needs manual review "
                "and most likely a custom scraper build."
            )
            ctx.report.add_note(f"Discovery error: {type(exc).__name__}: {exc}")

        ctx.report.duration_seconds = round(max(0.0, self.budget_seconds - ctx.remaining_seconds()), 2)
        ctx.report.budget_seconds = self.budget_seconds
        ctx.report.stage_timings = ctx.timer.snapshot()
        ctx.report.phases = build_phases_from_stage_timings(ctx.report.stage_timings)
        ctx.report.evidence_counts = dict(Counter(e.kind.value for e in ctx.evidence.all()))
        result.notes = ctx.report.to_notes()
        result.report = ctx.report

        log.info(ctx.timer.report_text(
            header={"discovery": host, "run_id": self.run_id or "-"},
            total_seconds=ctx.report.duration_seconds,
        ))

        # Write learned patterns from successful discovery if database is available
        if self.db is not None and result.ok and result.scrape_config:
            try:
                self._write_learned_patterns(ctx, result)
            except Exception as exc:
                log.debug("Learning pattern write failed for %s: %s", host, exc)

        self.progress("done", 4, 4)

        log.info("Discovery completed", extra={
            "run_id": self.run_id, "base_url": base_url,
            "verdict": result.verdict, "ok": result.ok,
            "adapter_mode": result.adapter_mode, "vehicles_found": result.vehicles_found,
            "duration_sec": ctx.report.duration_seconds, "budget_sec": self.budget_seconds,
            "candidates": len(ctx.candidates)
        })
        return result

    # ------------------------------------------------------------ sitemap flow
    def _run_sitemap_phase(self, ctx: DiscoveryContext) -> None:
        """robots.txt + sitemap.xml — no browser needed. Purely corroborating:
        the detail-URL count found here becomes a total_count_agreement signal
        on whichever candidate the browser flow picks, it never decides the
        outcome by itself (sitemaps are frequently stale)."""
        sitemap = discover_via_sitemap(ctx.base_url, ctx.heuristics, timeout=10.0)
        ctx.sitemap = sitemap
        if not sitemap.sitemaps_found:
            return
        ctx.evidence.add(Evidence(
            EvidenceKind.SITEMAP_URL, "sitemap", "", weight=1.0,
            payload={"sitemaps": sitemap.sitemaps_found, "urls_seen": sitemap.urls_seen}))
        if sitemap.best_cluster is not None:
            ctx.report.add_note(
                f"Sitemap listed {sitemap.best_cluster.count} URL(s) matching pattern "
                f"{sitemap.best_cluster.shape} — treated as corroborating evidence, not "
                f"a source of vehicle data by itself.")

    # ------------------------------------------------------------ browser flow
    def _run_browser_phase(self, ctx: DiscoveryContext, result: DiscoveryResult) -> None:
        from playwright.sync_api import sync_playwright  # lazy: heavy import

        h = ctx.heuristics
        base_url, host = ctx.base_url, ctx.host
        ctx.report.rendering_mode = "browser"

        json_hits: list[dict] = []  # {"url","json","headers","method","post_body"}, every XHR/fetch JSON response seen

        def _on_response(response) -> None:
            try:
                if len(json_hits) >= h.max_json_hits:
                    return
                req = response.request
                if req.resource_type not in ("xhr", "fetch"):
                    return
                if not (200 <= response.status < 300):
                    return
                headers = response.headers or {}
                ctype = headers.get("content-type", "")
                if "json" not in ctype.lower():
                    return
                url = response.url
                if h.noise_host_re.search(urlparse(url).netloc):
                    return
                content_length = headers.get("content-length")
                if content_length and int(content_length) > h.max_json_body_bytes:
                    return
                body = response.json()
                request_headers = dict(req.headers or {})
                method = req.method
                post_body = None
                if method == "POST":
                    try:
                        raw = req.post_data
                        post_body = json.loads(raw) if raw else None
                    except Exception:
                        post_body = None
            except Exception:
                return
            json_hits.append({"url": url, "json": body, "headers": request_headers,
                               "method": method, "post_body": post_body})

        # Same launch options as the scrape adapters — discovery must reach a
        # site under exactly the conditions the scraper will, or it verifies a
        # page the scraper can't actually get (and vice versa: a proxy that
        # only the scraper uses means discovery is refused on a site that works).
        launch = {"headless": self.headless, "args": chromium_launch_args()}
        proxy = configured_playwright_proxy()
        if proxy:
            launch["proxy"] = proxy

        with sync_playwright() as p:
            browser = p.chromium.launch(**launch)
            try:
                browser_ctx = browser.new_context(
                    user_agent=("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"),
                    viewport={"width": 1440, "height": 900},
                )
                browser_ctx.add_init_script(
                    "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")

                def _guard_request(route):
                    # One choke point for the whole context: covers every
                    # page.goto() below, every subresource the page itself
                    # loads, and every client-side redirect — not just the
                    # initial navigation a per-call-site check would catch.
                    try:
                        validate_public_url(route.request.url)
                    except UnsafeUrlError:
                        route.abort()
                        return
                    # Same filter the scrape adapter applies by default, so
                    # discovery sees the site as the sync will — and sends a
                    # fraction of the requests. btownautosales.ca (sm360) loads
                    # ~90 files per page and answers 429 to its own inventory
                    # script after a few candidate pages, leaving "No Results".
                    req = route.request
                    if settings.resource_blocking_default and (
                            req.resource_type in _BLOCKED_TYPES
                            or (req.resource_type != "document"
                                and _NOISE_RE.search(urlparse(req.url).netloc.lower()))):
                        route.abort()
                    else:
                        continue_paced(route, site_host)

                site_host = site_host_of(base_url)
                browser_ctx.route("**/*", _guard_request)
                page = browser_ctx.new_page()
                page.on("response", _on_response)

                # Status/headers of the document currently loaded in the page.
                # The selector check at the end needs them to tell "the link
                # shape was wrong" from "the site started refusing us part-way
                # through" — a CloudFront 403 page carries no vendor name in its
                # body, only the status says it is a block.
                def _on_document(response) -> None:
                    try:
                        if (response.request.resource_type == "document"
                                and response.frame == page.main_frame):
                            ctx.snapshots["last_document"] = {
                                "url": response.url, "status": response.status,
                                "headers": dict(response.headers or {})}
                    except Exception:
                        pass

                def _on_document_failed(request) -> None:
                    try:
                        if request.resource_type == "document" and request.frame == page.main_frame:
                            ctx.snapshots["last_document"] = {"url": request.url, "failed": True}
                    except Exception:
                        pass

                # A 200 page whose own scripts or data calls were refused renders
                # empty ("No Results") — the document status never shows it.
                def _on_throttled(response) -> None:
                    try:
                        # 429 only: sites 403 assorted own endpoints (logged-in
                        # user APIs) on perfectly healthy pages.
                        if (response.status == 429
                                and response.request.resource_type in ("script", "xhr", "fetch")
                                and _bare_host(response.url) == _bare_host(base_url)):
                            ctx.snapshots.setdefault("throttled", []).append(
                                (response.status, response.url.split("?")[0]))
                    except Exception:
                        pass

                page.on("response", _on_document)
                page.on("response", _on_throttled)
                page.on("requestfailed", _on_document_failed)

                ctx.check_cancel()
                try:
                    with ctx.timer.stage("homepage"):
                        home_response = page.goto(base_url, wait_until="domcontentloaded",
                                                   timeout=h.default_timeout_ms)
                        ctx.timer.incr("navigations")
                        wait_for_stable(page, h)
                        ctx.timer.incr("waits")
                        home = page.evaluate(PROBE_JS, list(h.detail_segments))
                except Exception as exc:
                    result.summary = (
                        "The website could not be opened — it may be blocking automated "
                        "browsers, or be temporarily unavailable. This site needs a custom "
                        "scraper build."
                    )
                    ctx.report.add_note(f"Could not load homepage: {type(exc).__name__}")
                    ctx.report.decision = DecisionReport(chosen_key=None, rationale="homepage unreachable")
                    return
                self.progress("explore", 1, 4)

                # Bot protection is worth calling out by name: it is the most common
                # reason a site needs paid custom work rather than a config tweak.
                # Classified from status code + response headers (vendor
                # fingerprints: Cloudflare, Akamai, PerimeterX, DataDome,
                # Incapsula) AND body text, not body text alone — the
                # pre-refactor check never looked at status/headers at all.
                try:
                    body_head = page.text_content("body") or ""
                except Exception:
                    body_head = ""

                # Which platform powers this site. Dealer websites cluster hard by
                # vendor, so knowing the platform is what makes the second dealer on
                # a known platform cheap to onboard — the admin sees it on the form
                # and the config from an existing same-platform dealer can be reused
                # instead of rediscovered.
                try:
                    with ctx.timer.stage("cms_fingerprint"):
                        cms_scores = detect_cms_platform(
                            page.content(),
                            dict(home_response.headers) if home_response else None,
                        )
                    platform = get_most_likely_platform(cms_scores)
                    if platform:
                        ctx.report.detected_cms = [platform]
                        result.detected_platform = platform
                        ctx.report.add_note(
                            f"Website platform detected: {platform} (confidence "
                            f"{cms_scores[platform]:.2f}). Dealers on this platform can "
                            f"usually reuse an existing scraper configuration."
                        )
                except Exception:
                    log.debug("CMS fingerprinting failed for %s", host, exc_info=True)

                antibot_verdict = classify_antibot(
                    status_code=home_response.status if home_response else None,
                    headers=home_response.headers if home_response else None,
                    body_text=body_head, heuristics=h,
                )
                if antibot_verdict.blocked:
                    ctx.evidence.add(Evidence(
                        EvidenceKind.ANTIBOT_SIGNAL, "explore_homepage", "", weight=1.0,
                        payload={"unresolved": True, "vendor": antibot_verdict.vendor,
                                 "reasons": antibot_verdict.reasons},
                        note="Bot-protection challenge detected on the homepage."))
                    ctx.report.antibot_events.append(antibot_verdict.vendor or "generic")
                    result.summary = (
                        "This site is protected by anti-bot software that blocks automated "
                        "access. It needs a custom scraper build (proxies / bypass work) "
                        "before it can be onboarded."
                    )
                    vendor_note = f" ({antibot_verdict.vendor})" if antibot_verdict.vendor else ""
                    ctx.report.add_note(f"Bot-protection challenge detected on the homepage{vendor_note}.")
                    ctx.report.decision = DecisionReport(chosen_key=None, rationale="anti-bot wall")
                    return

                # Zop's WordPress template never calls the dealer's inventory feed
                # from the page, so sniffing cannot find it. Fetch it directly;
                # the probe only accepts a feed whose vehicles link back to this
                # site, and adds it as captured traffic for the usual JSON path.
                platform_hits: list[dict] = []
                try:
                    home_html = page.content()
                    if zop.is_zop_site(home_html):
                        with ctx.timer.stage("platform_feed_probe"):
                            zop_hits = zop.probe_feed(base_url, home_html)
                        if zop_hits:
                            platform_hits = zop_hits
                            ctx.report.add_note(
                                f"Zop inventory feed found at {zop_hits[0]['url'].split('?')[0]} "
                                f"and confirmed to list this dealer's vehicles.")
                except Exception:
                    log.debug("Zop feed probe failed for %s", host, exc_info=True)
                # HillzDealer's newer template renders /cars on the server and
                # never fetches its page data in the browser, so read it directly.
                try:
                    if not platform_hits and hillz.is_hillz_site(home_html):
                        with ctx.timer.stage("platform_feed_probe"):
                            hillz_hits = hillz.probe_feed(page.url or base_url, home_html)
                        if hillz_hits:
                            platform_hits = hillz_hits
                            ctx.report.add_note(
                                "HillzDealer site: reading the inventory from its own "
                                "/cars page data.")
                except Exception:
                    log.debug("HillzDealer page data probe failed for %s", host, exc_info=True)

                candidates = _candidate_urls(base_url, home, host, h)
                # The homepage itself occasionally IS the listing.
                if home.get("bestCount", 0) >= 5 or home.get("jsonld", 0) >= 5:
                    candidates.insert(0, base_url)
                best: tuple[int, str, dict] | None = None
                candidates_evaluated = 0
                scoreboard: list[tuple[str, int]] = []
                for url in candidates:
                    if candidates_evaluated >= self.max_candidates:
                        break
                    ctx.check_cancel()
                    ctx.ensure_time(minimum_seconds=20.0)
                    try:
                        with ctx.timer.stage("candidate_nav"):
                            page.goto(url, wait_until="domcontentloaded", timeout=h.default_timeout_ms)
                            ctx.timer.incr("navigations")
                            wait_for_stable(page, h)
                            ctx.timer.incr("waits")
                            probe = page.evaluate(PROBE_JS, list(h.detail_segments))
                    except Exception:
                        # A guessed path that 404s or times out tells us nothing and
                        # must not consume one of the max_candidates slots — most of
                        # the guessed inventory paths miss on any given site, and
                        # spending the budget on them is how the real listing goes
                        # unevaluated.
                        continue
                    candidates_evaluated += 1
                    ctx.timer.incr("candidates_evaluated")
                    legacy_score = max(int(probe.get("jsonld") or 0), int(probe.get("bestCount") or 0))

                    score = legacy_score
                    if legacy_score < h.candidate_score_sufficient:
                        ctx.ensure_time(minimum_seconds=10.0)
                        try:
                            with ctx.timer.stage("candidate_scroll"):
                                scrolls = reveal_lazy_cards(page, heuristics=h)
                                ctx.timer.incr("scrolls", scrolls)
                        except Exception:
                            pass
                        raw_links = capture_links(page)
                        open_cluster = find_best_listing_cluster(
                            [LinkRecord(path=link["path"], text=link["text"],
                                        chrome=bool(link.get("chrome")))
                             for link in raw_links], h)
                        if open_cluster is not None and open_cluster.count > legacy_score:
                            probe = dict(probe)
                            probe["bestSeg"] = open_cluster.segment
                            probe["bestCount"] = open_cluster.count
                            probe["bestSample"] = open_cluster.paths[:8]
                            probe["_openEndedShape"] = open_cluster.shape
                            score = open_cluster.count
                            ctx.evidence.add(Evidence(
                                EvidenceKind.DOM_REPETITION, "dom_repetition_open_ended", f"page:{url}",
                                weight=1.0, payload={"shape": open_cluster.shape, "count": open_cluster.count}))

                    scoreboard.append((url, score))
                    # Strictly-greater keeps the earlier candidate on a tie, and
                    # candidates arrive best-looking-first — so /cars wins a 32-vs-32
                    # tie against a loan calculator that happens to embed a vehicle
                    # grid. Evaluating every candidate instead of breaking on the
                    # first convincing one is the point: that early exit is what
                    # stopped clutch.ca at candidate 2 and left /cars unseen.
                    if score >= h.candidate_score_min and (best is None or score > best[0]):
                        best = (score, url, probe)

                if scoreboard:
                    ctx.report.add_note(
                        "Evaluated " + ", ".join(f"{u} ({n} vehicle signal(s))" for u, n in scoreboard)
                    )

                if best is not None and best[2].get("_openEndedShape"):
                    ctx.report.add_note(
                        f"Detail pages found via open-ended URL-pattern clustering "
                        f"({best[2]['_openEndedShape']}) rather than a known segment name — "
                        f"this site uses a layout the built-in list didn't recognise by name.")
                self.progress("candidates", 2, 4)

                # A platform feed verified to list this dealer's own vehicles wins
                # over whatever the pages happened to call: on Zop's WordPress
                # template that was a 29-vehicle detail-page widget hitting the
                # same endpoint, whose size became page_size and stopped the
                # sync after the feed's first 10-vehicle page.
                if platform_hits:
                    json_hits[:] = platform_hits
                json_best = hillz.prefer_listing(
                    _best_vehicle_json(json_hits, h, listing_url=best[1] if best else None))

                if best is None and (json_best is None or json_best["score"] < h.json_min_score):
                    if _report_throttled(ctx, result):
                        return
                    result.summary = (
                        "No vehicle inventory listing could be found automatically. The site "
                        "likely uses a layout our engine does not yet support, so it needs a "
                        "custom scraper build before this dealer can be onboarded."
                    )
                    ctx.report.add_note(
                        "No page with a repeating vehicle grid found across the pages checked. "
                        "The inventory may sit behind a search form, a login, or a layout the "
                        "engine does not recognise.")
                    ctx.report.decision = DecisionReport(chosen_key=None, rationale="no candidate cleared minimum evidence")
                    return

                # How does the listing paginate? Detect from DOM signals on the
                # first page — the scraper verifies pagination at sync time.
                listing_url = best[1] if best is not None else base_url
                ctx.check_cancel()
                if best is not None:
                    ctx.ensure_time(minimum_seconds=10.0)
                    with ctx.timer.stage("pagination_dom"):
                        pag = _detect_dom_pagination(page, listing_url, h.default_timeout_ms, h)
                else:
                    pag = {"mode": "scroll", "page_url": None, "next_selector": None,
                           "verified": False, "observed": 0, "notes": []}
                pagination, page_url = pag["mode"], pag["page_url"]
                self.progress("pagination", 3, 4)

                pagination_json = None
                pagination_body = None
                if json_best is not None and json_best.get("method") == "POST":
                    def _matching_bodies() -> list[dict]:
                        key = _endpoint_key(json_best["url"])
                        return [hit["post_body"] for hit in json_hits
                                if _endpoint_key(hit["url"]) == key and hit.get("method") == "POST"
                                and hit.get("post_body")]

                    bodies = _matching_bodies()
                    if len(_dedup_dicts(bodies)) < 2:
                        ctx.check_cancel()
                        ctx.ensure_time(minimum_seconds=10.0)
                        try:
                            with ctx.timer.stage("pagination_json_scroll"):
                                scrolls = reveal_lazy_cards(page, heuristics=h)
                                ctx.timer.incr("scrolls", scrolls)
                        except Exception:
                            pass
                        bodies = _matching_bodies()
                    with ctx.timer.stage("pagination_json_probe"):
                        pagination_body = _detect_body_pagination(bodies)
                        if pagination_body is None:
                            pagination_body = _probe_body_pagination_via_http(
                                json_best["url"], json_best["list_path"], json_best["array"],
                                json_best.get("post_body") or {},
                                extra_headers=_extra_request_headers(json_best.get("headers"), h))
                elif json_best is not None:
                    def _matching_hits() -> list[str]:
                        key = _endpoint_key(json_best["url"])
                        return [hit["url"] for hit in json_hits if _endpoint_key(hit["url"]) == key]

                    from urllib.parse import parse_qsl

                    matching = _matching_hits()
                    distinct = {tuple(sorted(parse_qsl(urlparse(u).query))) for u in matching}
                    if len(distinct) < 2:
                        ctx.check_cancel()
                        ctx.ensure_time(minimum_seconds=10.0)
                        try:
                            with ctx.timer.stage("pagination_json_scroll"):
                                scrolls = reveal_lazy_cards(page, heuristics=h)
                                ctx.timer.incr("scrolls", scrolls)
                        except Exception:
                            pass
                        matching = _matching_hits()
                    with ctx.timer.stage("pagination_json_probe"):
                        pagination_json = _detect_pagination(matching, h)
                        # The page fetched only one batch (clutch.ca loads the
                        # rest on scroll through a separate call), so there is
                        # no page param to infer. Ask the API for page 2 under
                        # the common names instead of shipping a feed that
                        # reports thousands of vehicles and reads only the first.
                        if pagination_json is None:
                            # Probe from the grid's own call when one was seen:
                            # it carries every param the site sends (clutch adds
                            # interestRate and page=0 to the bare detail-page call).
                            paged = [u for u in matching
                                     if any(k in h.page_param_candidates
                                            for k, _ in parse_qsl(urlparse(u).query))]
                            pagination_json = _probe_pagination_via_http(
                                paged[0] if paged else json_best["url"],
                                json_best["list_path"], json_best["array"],
                                extra_headers=_extra_request_headers(json_best.get("headers"), h),
                                heuristics=h)

                # ------------------------------------------------- prefer json_api
                field_map: dict[str, str] = {}
                total_path = None
                use_json_api = False
                if json_best is not None:
                    sample_items = [it for it in json_best["array"][:8] if isinstance(it, dict)]
                    field_map = _build_field_map(sample_items, h)
                    total_path = _find_total_path(json_best["root"], len(json_best["array"]), h)
                    has_identity = any(f in field_map for f in h.identity_fields)
                    use_json_api = (
                        json_best["score"] >= h.json_min_score
                        and has_identity
                        and len(field_map) >= 4
                    )

                if use_json_api:
                    self._emit_json_api(ctx, result, json_best, field_map, total_path,
                                         pagination_json, pagination_body, listing_url, base_url)
                    return

                if best is None:
                    # Only a weak/unusable JSON signal was found, and no DOM listing either.
                    if _report_throttled(ctx, result):
                        return
                    result.summary = (
                        "No vehicle inventory listing could be found automatically. The site "
                        "likely uses a layout our engine does not yet support, so it needs a "
                        "custom scraper build before this dealer can be onboarded."
                    )
                    ctx.report.add_note(
                        "No page with a repeating vehicle grid found across the pages checked. "
                        "The inventory may sit behind a search form, a login, or a layout the "
                        "engine does not recognise.")
                    if json_best is not None:
                        ctx.report.add_note(
                            f"A JSON response with {len(json_best['array'])} car-like item(s) "
                            f"was seen at {json_best['url'].split('?')[0]}, but too few fields "
                            f"could be matched automatically. This dealer may still be "
                            f"onboardable as adapter_mode=json_api with a hand-written field_map.")
                    ctx.report.decision = DecisionReport(chosen_key=None, rationale="weak JSON signal, no DOM listing")
                    return

                self._emit_browser(ctx, result, best, pag, pagination, page_url, base_url, json_best, page)
            finally:
                browser.close()

    # -------------------------------------------------------------- emitters
    def _emit_json_api(self, ctx: DiscoveryContext, result: DiscoveryResult, json_best: dict,
                        field_map: dict, total_path: str | None, pagination_json: dict | None,
                        pagination_body: dict | None, listing_url: str, base_url: str) -> None:
        h = ctx.heuristics
        sample_items = [it for it in json_best["array"][:8] if isinstance(it, dict)]
        is_post = json_best.get("method") == "POST"

        if is_post:
            endpoint = json_best["url"]
            body_template = json_best.get("post_body") or {}
            if pagination_body is not None:
                if pagination_body["kind"] == "pair":
                    replacements = {pagination_body["start_key"]: "{page}",
                                     pagination_body["end_key"]: "{page_end}"}
                else:
                    replacements = {pagination_body["key"]: "{page}"}
                body = _template_body(body_template, replacements)
                start_page = int(pagination_body["start"])
                page_step = max(1, round(pagination_body["step"]))
            else:
                body = body_template
                start_page = 1
                page_step = 1
            page_size = len(json_best["array"])
        elif pagination_json is not None:
            replacements = {pagination_json["param"]: "{page}"}
            if pagination_json["size_param"]:
                replacements[pagination_json["size_param"]] = "{page_size}"
            endpoint = _template_endpoint(pagination_json["template_url"], replacements)
            start_page = int(pagination_json["start"])
            page_step = max(1, round(pagination_json["step"]))
            page_size = (
                int(pagination_json["size_value"]) if pagination_json["size_value"]
                else len(json_best["array"])
            )
            body = None
        else:
            endpoint = json_best["url"]
            start_page = 1
            page_step = 1
            page_size = len(json_best["array"])
            body = None

        pagination_confirmed = pagination_body is not None if is_post else pagination_json is not None

        reported_total = None
        if total_path:
            from ..adapters.json_api import _dig

            try:
                reported_total = int(_dig(json_best["root"], total_path))
            except (TypeError, ValueError):
                reported_total = None

        captured_headers = _extra_request_headers(json_best.get("headers"), h)

        from .net.headers import classify_headers
        durable_headers, volatile_headers = classify_headers(captured_headers)

        # A Next.js data route embeds the site's buildId, which changes on every
        # deploy of the dealer's site: a config that freezes today's id 404s after
        # their next release. Template it and record where the current id lives.
        build_id_url = None
        next_build = re.search(r"/_next/data/([A-Za-z0-9_-]+)/", endpoint)
        if next_build:
            endpoint = endpoint.replace(next_build.group(0), "/_next/data/{build_id}/", 1)
            build_id_url = listing_url

        ek = _endpoint_key(endpoint)
        cand_key = f"api:{ek[1]}{ek[2]}"
        if volatile_headers:
            ctx.evidence.add(Evidence(EvidenceKind.VOLATILE_AUTH, "volatile_headers_detected", cand_key,
                                      weight=-0.8, payload={"removed": list(volatile_headers.keys())},
                                      note="Request-specific headers (auth tokens/JWTs) removed from persisted config"))

        draft: dict = {
            "endpoint": endpoint,
            "list_path": json_best["list_path"],
            "field_map": field_map,
            "page_size": max(1, page_size),
            "start_page": start_page,
            "headers": {"Accept": "application/json", **durable_headers},
            "sample_array_len": len(json_best["array"]),
        }
        if is_post:
            draft["method"] = "POST"
            draft["body"] = body
        if total_path:
            draft["total_path"] = total_path
        if page_step != 1:
            draft["page_step"] = page_step
        if build_id_url:
            draft["next_build_id_url"] = build_id_url
        draft["max_pages"] = 500
        if _needs_url_base(field_map, sample_items):
            draft["url_base"] = base_url

        # HillzDealer's newer template lists 10 vehicles in full plus an index
        # of all of them; the rest are read from each vehicle's own page data.
        detail = hillz.detail_block(json_best["root"], json_best["list_path"], json_best["url"])
        if detail:
            draft["detail"] = detail
            draft["field_map"] = field_map = {**field_map, "url": "_index.slug"}
            draft["url_base"] = draft.get("url_base") or base_url
            draft["next_build_id_url"] = f"{draft['url_base'].rstrip('/')}/cars"
            from ..adapters.json_api import _dig

            index_len = len(_dig(json_best["root"], hillz.INDEX_PATH))
            try:
                reported_total = int(_dig(json_best["root"], hillz.TOTAL_PATH))
                total_path = draft["total_path"] = hillz.TOTAL_PATH
            except (TypeError, ValueError):
                reported_total = reported_total or index_len
            # The index names every vehicle, so nothing depends on paging.
            pagination_confirmed = True
            ctx.report.add_note(
                f"HillzDealer lists {len(json_best['array'])} vehicles in full and "
                f"indexes {index_len}; the rest are read from each vehicle's own page.")

        vehicles_found = reported_total if reported_total else len(json_best["array"])

        # Sitemap cross-check (Phase 2): corroborating evidence only — never
        # overrides the API's own reported total.
        total_agreement_raw = None
        sitemap_detail_count = None
        sitemap = ctx.sitemap
        if sitemap is not None and sitemap.best_cluster is not None and sitemap.best_cluster.count > 0:
            sitemap_count = sitemap_detail_count = sitemap.best_cluster.count
            total_agreement_raw = 1.0 - min(
                1.0, abs(sitemap_count - vehicles_found) / max(sitemap_count, vehicles_found, 1))

        cand = Candidate(
            key=cand_key, kind=CandidateKind.JSON_API, url=endpoint,
            draft={**draft, "vehicles_found": vehicles_found, "pagination_verified": pagination_confirmed,
                   "total_count_agreement": total_agreement_raw,
                   "sitemap_detail_count": sitemap_detail_count},
        )
        ctx.upsert_candidate(cand)
        note = (f"Detected a JSON inventory API ({'POST' if is_post else 'GET'}) at "
                f"{endpoint.split('?')[0]} ({len(field_map)} field(s) mapped automatically: "
                f"{', '.join(sorted(field_map))}).")
        ctx.evidence.add(Evidence(EvidenceKind.API_RESPONSE, "api_sniff", cand.key, weight=1.0,
                                   payload={"score": json_best["score"], "count": len(json_best["array"])},
                                   note=note))
        ctx.report.add_note(note)

        config = self.decision.build_config("json_api", cand)
        error = self.decision.validate("json_api", config, base_url)
        if error:
            cand.rejected_reason = error
            ctx.report.add_note(f"Discovered json_api config failed validation: {error}")
            result.summary = ("A possible inventory API was found, but the generated "
                               "configuration failed validation — this dealer needs manual review.")
            ctx.report.decision = DecisionReport(chosen_key=None, rationale=f"json_api config invalid: {error}")
            return

        self.decision.optimize_config("json_api", config, vehicles_found)

        ctx.check_cancel()
        with ctx.timer.stage("config_generation"):
            cscore = self.decision.score(cand, ctx.evidence)
            cand.score = cscore
            ready, readiness_reason = self.decision.assess_readiness("json_api", cand, cscore)
            confidence, verdict, requires_custom = self.decision.verdict(cscore, verified=ready)

        if not ready:
            cand.rejected_reason = readiness_reason
            ctx.report.add_note(f"JSON API config not ready: {readiness_reason}")
            ctx.report.candidates.append(CandidateReport(
                key=cand.key, kind=cand.kind.value, url=cand.url, score=cscore.total,
                contributions=[SignalContributionOut(signal=c.signal, raw=c.raw, weight=c.weight,
                                                      contribution=c.contribution) for c in cscore.contributions],
                hard_blockers=list(cscore.hard_blockers), rejected_reason=readiness_reason,
            ))
            ctx.report.decision = DecisionReport(chosen_key=None, rationale=readiness_reason)
            return

        ctx.report.candidates.append(CandidateReport(
            key=cand.key, kind=cand.kind.value, url=cand.url, score=cscore.total,
            contributions=[SignalContributionOut(signal=c.signal, raw=c.raw, weight=c.weight,
                                                  contribution=c.contribution) for c in cscore.contributions],
            hard_blockers=list(cscore.hard_blockers),
        ))

        result.ok = True
        result.scrape_config = config
        result.adapter_mode = "json_api"
        result.verdict = verdict
        result.requires_custom_work = requires_custom
        result.vehicles_found = vehicles_found
        result.reported_total = reported_total
        result.listing_url = listing_url
        result.pagination = (
            "single" if not pagination_confirmed else
            ("page" if page_step == 1 else "offset")
        )
        result.extract = "json_api"
        result.confidence = confidence
        result.summary = (
            f"Supported via the site's own inventory API. Detected "
            f"{len(json_best['array'])} vehicles in sample response with "
            f"{len(field_map)} field(s) mapped. "
            f"{'Ready to enable.' if verdict == 'ready' else 'Needs review before enabling.'}")

        if not pagination_confirmed:
            ctx.report.add_note(
                "Pagination was inferred from request patterns, not verified during "
                "discovery — the scraper will walk all pages at sync time.")
        if not total_path:
            ctx.report.add_note(
                "No total-count field was found in the API response — the removal "
                "safety gate has no denominator to compare against.")
        ctx.report.add_note(
            "field_map was guessed by matching source key names (e.g. 'vin', "
            "'make', 'sellingPrice') — verify it against a sample response before "
            "relying on it.")
        if captured_headers:
            ctx.report.add_note(
                f"This API required extra request header(s) to accept the call "
                f"({', '.join(sorted(captured_headers))}) — captured from the site's "
                f"own frontend and included in scrape_config.headers. If sync starts "
                f"returning 401/403 later, the site likely rotated this value and "
                f"needs re-detecting.")

        ctx.report.decision = DecisionReport(
            chosen_key=cand.key, rationale=f"json_api candidate scored {cscore.total:.2f}")

    def _emit_browser(self, ctx: DiscoveryContext, result: DiscoveryResult, best: tuple,
                       pag: dict, pagination: str, page_url: str | None, base_url: str,
                       json_best: dict | None, page=None) -> None:
        h = ctx.heuristics
        score, listing_url, probe = best
        seg = probe.get("bestSeg") or "vehicles"
        # The initial probe may have run before a client-rendered grid appeared,
        # and on such a site the schema.org blocks are injected together with the
        # cards — so an early count of 0 says "too early", not "no structured
        # data". Re-count now that the listing has been walked. Falling back to
        # text parsing when clean JSON-LD exists costs real field accuracy
        # (observed on clutch.ca: 32 Vehicle nodes per page, extract chose "dom").
        jsonld_count = int(probe.get("jsonld") or 0)
        if jsonld_count < 5 and page is not None:
            try:
                recount = count_jsonld_vehicles(page)
                if recount > jsonld_count:
                    log.info("JSON-LD re-probe after render: %s -> %s vehicle nodes",
                             jsonld_count, recount)
                    jsonld_count = recount
            except Exception:
                pass
        extract = "jsonld" if jsonld_count >= 5 else "dom"

        # What to tell the admin about inventory size: the page's own
        # "N results" text is the real total; otherwise the links seen during
        # pagination verification are a floor ("at least N") — discovery only
        # samples the first page plus one extra batch, the full sync walks
        # every page.
        reported = probe.get("reportedTotal")
        observed = max(score, int(pag.get("observed") or 0))
        count_txt = (str(reported) if reported
                     else f"at least {observed}" if pag["verified"] else str(observed))

        # Visual grid signal (Phase 2): re-navigate to the winning listing and
        # measure whether the sampled cards form a regular grid — a
        # layout-based confidence signal independent of link/JSON-LD counts.
        # Best-effort: any failure here just means this signal stays absent.
        visual_raw = None
        if page is not None:
            try:
                ctx.ensure_time(minimum_seconds=15.0)
            except DiscoveryBudgetExceeded:
                pass  # visual grid is corroborating-only — skip if budget tight
            else:
                try:
                    with ctx.timer.stage("visual_grid"):
                        # Pagination detection leaves the page on the listing
                        # already. Reloading it anyway is one more hit on sites
                        # that rate-limit hard — tabangimotors.com answers 429
                        # after about a dozen page loads, and this was the one
                        # that tipped it over.
                        if page.url.rstrip("/") != listing_url.rstrip("/"):
                            page.goto(listing_url, wait_until="domcontentloaded",
                                      timeout=h.default_timeout_ms)
                            ctx.timer.incr("navigations")
                            wait_for_stable(page, h)
                            ctx.timer.incr("waits")
                        sample_paths = probe.get("bestSample") or []
                        if sample_paths:
                            raw_boxes = capture_card_boxes(page, sample_paths)
                            boxes = [BoundingBox(**b) for b in raw_boxes]
                            grid = analyze_grid(boxes, h)
                            if grid.is_grid:
                                visual_raw = grid.raw_score
                                ctx.evidence.add(Evidence(
                                    EvidenceKind.VISUAL_GRID, "visual_structure", f"page:{listing_url}",
                                    weight=1.0, payload={"rows": grid.row_count, "cols": grid.col_count,
                                                          "box_count": grid.box_count},
                                    note=f"Cards form a {grid.row_count}x{grid.col_count} regular grid "
                                         f"({grid.box_count} sampled)."))
                except DiscoveryCancelled:
                    raise
                except Exception:
                    pass

        # Sitemap cross-check (Phase 2): corroborating evidence only — never
        # overrides the page's own reported total.
        total_agreement_raw = None
        sitemap_detail_count = None
        sitemap = ctx.sitemap
        if sitemap is not None and sitemap.best_cluster is not None and sitemap.best_cluster.count > 0:
            sitemap_count = sitemap_detail_count = sitemap.best_cluster.count
            reference = reported or observed
            if reference:
                total_agreement_raw = 1.0 - min(
                    1.0, abs(sitemap_count - reference) / max(sitemap_count, reference))

        card_anchor = f"a[href*='/{seg}/']"
        id_regex = _build_detail_id_regex(seg, probe.get("bestSample") or [])

        # Everything above this line is inference. THIS is the one check that
        # isn't: the selector we are about to hand the scraper must actually
        # match links on the page we just measured.
        #
        # tabangimotors.com is why. Its nav carries 15 SEO links shaped like
        # "/used-sedans-under-5000" — hyphenated with a digit, so `_looks_like_id`
        # read each as a record id and collapsed them to the shape "/*". That
        # cluster had no literal segment, the segment fallback invented "item",
        # and discovery shipped a[href*='/item/'] for a site with no /item/
        # anywhere. It reported "15 vehicles verified" at medium confidence, the
        # onboarding gate accepted it, and every sync since failed with 0
        # vehicles. Rule 1: never report a success we cannot substantiate.
        if page is not None:
            try:
                anchor_hits = page.eval_on_selector_all(card_anchor, "els => els.length")
            except Exception:
                anchor_hits = None          # selector not usable in the browser at all
            if anchor_hits == 0:
                # Pagination detection just reloaded the listing, and a
                # client-rendered grid may not be there yet: clutch.ca/cars shows
                # 7 nav anchors at first and its 32 vehicle links only after a
                # scroll. The candidate pass scrolled before clustering, so give
                # the check the same chance before calling the shape wrong.
                try:
                    page.wait_for_selector(card_anchor, state="attached",
                                           timeout=min(h.default_timeout_ms, 15000))
                except Exception:
                    try:
                        reveal_lazy_cards(page, heuristics=h)
                    except Exception:
                        pass
                try:
                    anchor_hits = page.eval_on_selector_all(card_anchor, "els => els.length")
                except Exception:
                    anchor_hits = None
            if not anchor_hits:
                # The candidate pass saw these links on this same page, so first
                # rule out that the page has since been replaced by a block. On
                # driftmotors.ca (CloudFront 403) and tabangimotors.com
                # (Cloudflare 429) the WAF kicks in after several page loads,
                # and blaming the layout sent both to "quote custom scraper".
                verdict = _current_page_block(page, ctx)
                if verdict.blocked:
                    vendor_note = f" ({verdict.vendor})" if verdict.vendor else ""
                    status_note = (f", HTTP {verdict.status_code}"
                                   if verdict.status_code else "")
                    reason = (f"site started blocking automated access part-way through "
                              f"discovery{vendor_note}{status_note}")
                    ctx.evidence.add(Evidence(
                        EvidenceKind.ANTIBOT_SIGNAL, "explore_listing", f"page:{listing_url}",
                        weight=1.0, payload={"unresolved": True, "vendor": verdict.vendor,
                                             "reasons": verdict.reasons},
                        note="Bot-protection block hit while verifying the listing page."))
                    ctx.report.antibot_events.append(verdict.vendor or "generic")
                    ctx.report.add_note(
                        f"Vehicle links were found on {listing_url}, but the {reason} "
                        f"before the generated selector could be verified. Retry later, "
                        f"or route this site through SCRAPER_PROXY.")
                    result.summary = (
                        "Vehicle listings were found, but the site began blocking automated "
                        "access (rate limiting / bot protection) before the configuration "
                        "could be verified. Retry discovery later, or enable a residential "
                        "proxy for this site.")
                    ctx.report.decision = DecisionReport(chosen_key=None, rationale=reason)
                    return
                if _report_throttled(ctx, result):
                    return
                reason = (f"generated vehicle-link selector {card_anchor!r} matches nothing "
                          f"on {listing_url} — the repeating link shape that was detected "
                          f"does not point at vehicle pages")
                ctx.report.add_note(f"Rejected browser config: {reason}")
                result.summary = (
                    "A repeating link pattern was found, but it does not lead to vehicle "
                    "pages, so no working scraper could be generated for this site.")
                ctx.report.decision = DecisionReport(chosen_key=None, rationale=reason)
                return

        # Which element holds a vehicle's details. Only meaningful for extract=dom,
        # where the scraper reads year/make/model/price out of the card's text; with
        # JSON-LD the fields come from the structured block instead. Without it the
        # scraper climbs a fixed number of ancestors and takes the first chatty one,
        # which on cardora.ca was a badge reading "SOLD" — 528 vehicles collected,
        # every field NULL, run reported successful.
        card_selector = None
        if extract != "jsonld" and page is not None:
            with ctx.timer.stage("card_selector"):
                card_selector = detect_card_selector(page, card_anchor, id_regex)
            if card_selector:
                ctx.report.add_note(
                    f"Vehicle details are read from '{card_selector}' cards.")
            else:
                ctx.report.add_note(
                    "Could not identify a repeating vehicle card, so details are read "
                    "from a best-guess container. Check the first sync's field values: "
                    "if year/make/model/price come back empty, set 'card_selector' by hand."
                )

        draft = {
            "list_url": listing_url,
            "base_url": base_url,
            "card_anchor": card_anchor,
            "wait_selector": card_anchor,
            "id_regex": id_regex,
            "extract": extract,
            "paginate": pagination,
            "settle_ms": 2500,
            # Hand the scraper the browser mode discovery actually succeeded in,
            # not an assumption. Sites behind AWS WAF (clutch.ca) serve an error
            # page to headless Chromium and render normally to a headed one, so
            # hardcoding True here meant a headed discovery run could prove a
            # config that then failed silently on every nightly sync.
            "headless": self.headless,
            "max_pages": 200,
            **({"card_selector": card_selector} if card_selector else {}),
        }
        if pagination == "url":
            draft["page_url"] = page_url
        elif pagination == "click":
            draft["next_selector"] = pag["next_selector"]
        elif pagination == "scroll":
            draft["scroll_rounds"] = 80

        cand = Candidate(
            key=f"page:{listing_url}", kind=CandidateKind.HTML_LISTING, url=listing_url,
            draft={**draft, "vehicles_found": reported or observed,
                   "jsonld_count": int(probe.get("jsonld") or 0),
                   "dom_link_count": observed,
                   "pagination_verified": pag["verified"],
                   "visual_grid": visual_raw,
                   "total_count_agreement": total_agreement_raw,
                   "sitemap_detail_count": sitemap_detail_count},
        )
        ctx.upsert_candidate(cand)
        if pag.get("verified"):
            ctx.evidence.add(Evidence(EvidenceKind.PAGINATION_VERIFIED, "dom_pagination", cand.key,
                                       weight=1.0, payload=pag))
        if extract == "jsonld":
            ctx.evidence.add(Evidence(EvidenceKind.JSONLD_VEHICLE, "explore_homepage", cand.key,
                                       weight=1.0, payload={"count": int(probe.get("jsonld") or 0)}))
        else:
            ctx.evidence.add(Evidence(EvidenceKind.DOM_REPETITION, "dom_analysis", cand.key,
                                       weight=1.0, payload={"count": observed}))

        config = self.decision.build_config("browser", cand)
        error = self.decision.validate("browser", config, base_url)
        if error:
            cand.rejected_reason = error
            ctx.report.add_note(f"Discovered browser config failed validation: {error}")
            result.summary = ("A possible vehicle listing was found, but the generated "
                               "configuration failed validation — this dealer needs manual review.")
            ctx.report.decision = DecisionReport(chosen_key=None, rationale=f"browser config invalid: {error}")
            return

        vehicles_found = reported or observed
        self.decision.optimize_config("browser", config, vehicles_found)

        ctx.check_cancel()
        with ctx.timer.stage("config_generation"):
            cscore = self.decision.score(cand, ctx.evidence)
            cand.score = cscore
            ready, readiness_reason = self.decision.assess_readiness("browser", cand, cscore)
            confidence, verdict, requires_custom = self.decision.verdict(cscore, verified=ready)

        if not ready:
            cand.rejected_reason = readiness_reason
            ctx.report.add_note(f"Browser config not ready: {readiness_reason}")
            ctx.report.candidates.append(CandidateReport(
                key=cand.key, kind=cand.kind.value, url=cand.url, score=cscore.total,
                contributions=[SignalContributionOut(signal=c.signal, raw=c.raw, weight=c.weight,
                                                      contribution=c.contribution) for c in cscore.contributions],
                hard_blockers=list(cscore.hard_blockers), rejected_reason=readiness_reason,
            ))
            ctx.report.decision = DecisionReport(chosen_key=None, rationale=readiness_reason)
            return

        ctx.report.candidates.append(CandidateReport(
            key=cand.key, kind=cand.kind.value, url=cand.url, score=cscore.total,
            contributions=[SignalContributionOut(signal=c.signal, raw=c.raw, weight=c.weight,
                                                  contribution=c.contribution) for c in cscore.contributions],
            hard_blockers=list(cscore.hard_blockers),
        ))

        result.ok = True
        result.scrape_config = config
        result.adapter_mode = "browser"
        result.verdict = verdict
        result.requires_custom_work = requires_custom
        result.vehicles_found = vehicles_found
        result.reported_total = reported
        result.listing_url = listing_url
        result.pagination = pagination
        result.extract = extract
        result.confidence = confidence
        result.summary = (
            f"Supported via DOM listing. Detected {vehicles_found} vehicle signal(s) "
            f"(extract={extract}, pagination={pagination}). "
            f"{'Ready to enable.' if verdict == 'ready' else 'Needs review before enabling.'}")

        ctx.report.add_note(
            f"Detected {vehicles_found} vehicle signal(s) on listing page "
            f"(extract={extract}, pagination={pagination})")
        for n in pag["notes"]:
            ctx.report.add_note(n)
        if extract == "dom":
            ctx.report.add_note(
                "No schema.org data — falling back to DOM text parsing, which is "
                "more fragile. Verify the first run's field values.")
        if json_best is not None:
            ctx.report.add_note(
                f"Also saw a JSON response with {len(json_best['array'])} car-like "
                f"item(s) at {json_best['url'].split('?')[0]}, but not enough fields "
                f"could be matched automatically to prefer it over DOM scraping.")

        ctx.report.decision = DecisionReport(
            chosen_key=cand.key, rationale=f"browser candidate scored {cscore.total:.2f}")

    def _write_learned_patterns(self, ctx: DiscoveryContext, result: DiscoveryResult) -> None:
        """Extract and persist patterns from a successful discovery to accelerate
        future runs for the same host."""
        if not result.scrape_config:
            return

        host = ctx.host
        adapter_mode = result.adapter_mode
        config = result.scrape_config
        cms_platform = None  # TODO: detect CMS in Phase 5

        if adapter_mode == "json_api":
            endpoint = config.get("endpoint") or config.get("endpoints")
            if endpoint:
                pattern = {
                    "endpoint": endpoint,
                    "endpoints": config.get("endpoints"),
                    "field_map": config.get("field_map", {}),
                    "pagination": {
                        "start_page": config.get("start_page"),
                        "page_step": config.get("page_step"),
                        "list_path": config.get("list_path"),
                        "total_path": config.get("total_path"),
                    },
                    "headers": config.get("headers", {}),
                }
                if should_write_pattern(result.confidence == "high" and 0.85 or 0.65, True):
                    write_pattern(self.db, host, cms_platform, "api_endpoint", pattern,
                                  self.run_id, success=True)

        elif adapter_mode == "browser":
            listing_url = config.get("list_url")
            if listing_url:
                pattern = {
                    "listing_url": listing_url,
                    "card_anchor": config.get("card_anchor"),
                    "id_regex": config.get("id_regex"),
                    "pagination": {
                        "mode": result.pagination,
                        "next_selector": config.get("next_selector"),
                    },
                }
                if should_write_pattern(result.confidence == "high" and 0.85 or 0.65, True):
                    write_pattern(self.db, host, cms_platform, "listing_url", pattern,
                                  self.run_id, success=True)

            # Always write field_map if available — it's valuable for all adapter modes
            field_map = config.get("field_map")
            if field_map:
                pattern = {"field_map": field_map}
                write_pattern(self.db, host, cms_platform, "field_map", pattern,
                              self.run_id, success=True)


def discover(base_url: str, headless: bool = True, timeout_ms: int = 45000,
             max_candidates: int = 6) -> DiscoveryResult:
    """Facade preserved 1:1 from the pre-refactor dealer_scraper/discovery.py."""
    heuristics = HeuristicsConfig.from_overrides({"default_timeout_ms": timeout_ms})
    coordinator = DiscoveryCoordinator(base_url, heuristics, headless=headless,
                                        max_candidates=max_candidates)
    return coordinator.run()
