"""Every discovery magic number, regex, and lexicon in one typed, overridable
object — before this refactor they were scattered module-level constants that
no one could tune without editing code (the original dealer_scraper/discovery.py).

A single HeuristicsConfig instance flows through a run via DiscoveryContext.heuristics
so every strategy/analyzer reads the same values instead of importing globals.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


def _re(pattern: str, flags: int = re.I) -> re.Pattern:
    return re.compile(pattern, flags)


@dataclass
class ScoringWeights:
    """Per-signal weights for scoring.ConfidenceModel. See HANDOFF/07 for the
    rationale behind each weight."""

    verification_success: float = 3.0
    field_map_coverage: float = 1.5
    field_value_validity: float = 1.5
    pagination_verified: float = 1.5
    # Multiplier applied to pagination_verified's raw value when pagination was
    # only inferred (e.g. a single API param diff) rather than empirically proven.
    pagination_inferred_factor: float = 0.4
    item_volume: float = 1.0
    structured_data: float = 1.0
    dom_repetition: float = 1.0
    semantic_match: float = 0.8
    total_count_agreement: float = 0.8
    cms_prior: float = 0.5
    learned_prior: float = 0.5
    visual_grid: float = 0.4
    llm_judgment: float = 0.4
    antibot_penalty: float = -1.5
    volatile_auth_penalty: float = -0.8
    ready_threshold: float = 0.75
    needs_review_threshold: float = 0.45


@dataclass
class HeuristicsConfig:
    """Tunable discovery knobs, ported 1:1 from the pre-refactor module
    constants in dealer_scraper/discovery.py (git history)."""

    # -- candidate discovery --
    candidate_paths: tuple[str, ...] = (
        "/inventory", "/cars", "/vehicles", "/used-vehicles", "/used-cars",
        "/shop", "/buy", "/inventory/used", "/all-inventory", "/used", "/stock",
        "/vehicle-search", "/search/inventory",
        # French (Quebec/francophone dealer sites) — same shapes, translated.
        "/vehicules", "/vehicules-occasion", "/occasion", "/achat",
        "/vehicules-usages", "/recherche-inventaire", "/stock-vehicules",
    )
    # EN+FR nav-link/anchor-text hints (Phase 2 — the pre-refactor version was
    # English-only and missed /vehicules, /occasion style francophone sites).
    nav_hints_re: re.Pattern = field(default_factory=lambda: _re(
        r"inventory|vehicles?|cars?|shop|browse|buy|used|pre-?owned|stock|"
        r"v[ée]hicules?|occasion|achat|usag[ée]s?|recherche"))
    # Paths that match nav_hints_re but are never the inventory listing.
    # `nav_hints_re` is deliberately loose (it has to catch /vehicules and
    # /pre-owned), which means it also matches /auto-financing/car-loan-
    # calculator on "car", /blog/best-used-cars-2026 on "used", and
    # /sell-your-car on "car". clutch.ca was onboarded against its loan
    # calculator page for exactly this reason: the calculator embeds a vehicle
    # grid, so it looked like a perfectly good listing and scraped one page.
    nav_negative_re: re.Pattern = field(default_factory=lambda: _re(
        r"(^|[/\-_])(financ\w*|loan|calculator|payment|credit|lease|leasing|"
        r"insurance|warrant(?:y|ies)|protection|trade[-_]?in|sell|appraisal|valuation|"
        r"blog|news|article|guide|review|compare|about|contact|careers?|"
        r"service|parts|repair|book|appointment|test[-_]?drive|"
        r"financement|pr[eê]t|calculatrice|assurance|garantie|"
        r"[eé]change|vendre|[eé]valuation|blogue|nouvelles|carri[eè]res?)"
        r"($|[/\-_?.])"))
    detail_segments: tuple[str, ...] = (
        "vehicles", "vehicle", "inventory", "cars", "car", "listing", "vdp", "used",
        "vehicule", "vehicules", "occasion",
    )
    max_candidates_default: int = 6
    candidate_score_min: int = 5
    # If the sitemap advertises this many times more vehicle detail URLs than
    # the chosen listing yielded, and pagination was never proven, the config
    # almost certainly captures page 1 only. Corroborating agreement was
    # already scored softly; this makes the *disagreement* decisive, because
    # soft-scoring it let clutch.ca ship at "high / ready" on 32 of ~3,100.
    sitemap_contradiction_factor: float = 3.0
    # Vehicle-link count above which a candidate page is already convincing
    # enough to skip the extra scroll + link-clustering dig. It used to also
    # break the candidate loop; it no longer does, because a page clearing this
    # bar is not necessarily the *best* page — clutch.ca's loan calculator
    # embeds a 32-vehicle grid and stopped the search before /cars was seen.
    candidate_score_sufficient: int = 20

    # -- open-ended link-shape clustering (analyzers/dom_repetition.py) --
    # Detail-page detection beyond the closed `detail_segments` list: cluster
    # ALL internal links by normalized path shape (numeric/slug segments
    # wildcarded) and treat a large, non-boilerplate recurring shape as
    # evidence of a repeating record type (vehicle detail pages), regardless
    # of what the URL segment is actually called.
    link_shape_min_count: int = 6
    link_shape_max_links: int = 2000
    # Path-shape prefixes that are almost never inventory, even when they
    # recur often — filtered out before scoring so pagination/blog/account
    # links can't win by sheer repetition.
    link_shape_ignore_re: re.Pattern = field(default_factory=lambda: _re(
        r"^/(blog|news|article|press|about|contact|career[s]?|privacy|terms|"
        r"legal|login|logout|signin|signup|register|account|cart|checkout|"
        r"wp-content|wp-json|api|static|assets|css|js|images?|fonts|"
        r"sitemap|feed|rss|tag|category|categories|page|search|filter)(/|$)"))

    # -- visual structure (analyzers/visual_structure.py) --
    visual_grid_min_boxes: int = 4
    # Coefficient of variation (stdev/mean) ceiling for card width/height to
    # be called "a regular grid" — hand-tuned against synthetic CSS-grid and
    # flexbox fixtures (tests/test_discovery_analyzers.py).
    visual_grid_cv_max: float = 0.35

    # -- intelligent waits (browser/waits.py) --
    # Bounded to stay in the same ballpark as the fixed sleeps they replace
    # (settle_wait_ms=2500) in the worst case (a page whose network never
    # idles — common with chat widgets/analytics beacons), while exiting much
    # earlier than that in the common case once the page actually settles.
    networkidle_timeout_ms: int = 2000
    dom_stability_poll_ms: int = 150
    dom_stability_quiet_rounds: int = 3
    dom_stability_max_ms: int = 3000

    # -- anti-bot (browser/antibot.py) --
    antibot_retry_max_attempts: int = 3

    # -- traffic sniffing --
    noise_host_re: re.Pattern = field(default_factory=lambda: _re(
        r"google-analytics|googletagmanager|doubleclick|google\.[a-z.]+/pagead|"
        r"facebook\.(com|net)|hotjar|segment\.(io|com)|sentry\.io|intercom|"
        r"zendesk|recaptcha|gstatic\.com|cloudflareinsights|fullstory|clarity\.ms|"
        r"newrelic|nr-data|datadoghq|onetrust|cookielaw|bing\.com|adsystem|"
        r"criteo|taboola|outbrain|licdn\.com|linkedin\.com|twitter\.com|tiktok"))
    max_json_hits: int = 40
    max_json_body_bytes: int = 3_000_000
    json_min_score: float = 6.0

    vehicle_signal_re: re.Pattern = field(default_factory=lambda: _re(
        r"^vin$|make|model|trim|mileage|odometer|stocknum|stockno|stocknumber|"
        r"msrp|listprice|sellprice|sellingprice|internetprice|askingprice|"
        r"bodystyle|bodytype|exteriorcolor|extcolor|interiorcolor|intcolor|"
        r"transmission|drivetrain|driveline|fueltype|vehicleid|vdp|modelyear|"
        r"^year$|^price$"))

    field_synonyms: dict[str, tuple[str, ...]] = field(default_factory=lambda: {
        "external_id": ("vehicleid", "stocknumber", "stocknum", "stockno", "sku",
                         "inventoryid", "listingid", "itemid", "uid", "id"),
        "vin": ("vin", "serialno", "serialnumber", "vinnumber"),
        "make": ("make", "brand", "manufacturer"),
        "model": ("model", "modelname"),
        "year": ("year", "modelyear", "vehicleyear"),
        "trim": ("trim", "trimlevel"),
        "mileage": ("mileage", "odometer", "odometerkm", "mileagekm", "miles"),
        "price": ("price", "saleprice", "sellprice", "listprice", "sellingprice", "internetprice",
                  "askingprice", "originalprice", "msrpprice", "salesprice", "msrp"),
        "url": ("vdpurl", "detailurl", "detailpageurl", "canonicalurl",
                "permalink", "vdplink", "pageurl", "url", "link"),
        "body_type": ("bodytype", "bodystyle", "body"),
        "condition": ("condition", "newused", "stocktype"),
        "exterior_color": ("exteriorcolor", "extcolor", "exteriorcolour", "color"),
        "interior_color": ("interiorcolor", "intcolor", "interiorcolour"),
        "transmission": ("transmission", "trans"),
        "fuel_type": ("fueltype", "fuel"),
        "drivetrain": ("drivetrain", "driveline", "drivetype", "drive"),
        # The feed's own lifecycle word (Zop: Instock / Sold / Unpublished). Kept
        # by _build_field_map only when its values are words the engine maps.
        "source_status": ("status", "stockstatus", "inventorystatus", "availability"),
    })
    identity_fields: tuple[str, ...] = ("external_id", "vin", "url")

    total_synonyms: frozenset = field(default_factory=lambda: frozenset({
        "total", "totalcount", "totalresults", "totalitems", "totalrecords",
        "resultcount", "recordcount", "numfound", "hits", "count",
        "numberofitems", "numberofresults", "itemcount", "totalvehicles", "totalcars",
    }))
    size_param_re: re.Pattern = field(default_factory=lambda: _re(
        r"^(limit|pagesize|page_size|per_page|perpage|pn|count|take|size)$"))
    zero_based_param_re: re.Pattern = field(default_factory=lambda: _re(r"^(offset|skip|start)$"))
    one_based_param_re: re.Pattern = field(default_factory=lambda: _re(
        r"^(page|pg|p|pt|pageno|pagenum|pagenumber)$"))
    page_param_candidates: tuple[str, ...] = ("page", "p", "pg", "pageNumber", "pageNo")

    label_leaf_priority: tuple[str, ...] = ("name", "displayname", "label", "text", "title", "value")
    label_leaf_fallback: tuple[str, ...] = ("description", "slug", "code")
    link_leaf_re: re.Pattern = field(default_factory=lambda: _re(r"completeurl|url|link|href|permalink|src"))
    link_leaf_exclude_re: re.Pattern = field(default_factory=lambda: _re(r"logo|badge|icon"))

    header_denylist: frozenset = field(default_factory=lambda: frozenset({
        "user-agent", "accept-encoding", "accept-language", "connection",
        "content-length", "host", "cache-control", "pragma",
        "upgrade-insecure-requests", "content-type", "if-none-match",
        "if-modified-since", "te", "cookie", "priority",
    }))

    antibot_phrases: tuple[str, ...] = (
        "verify you are human", "checking your browser", "enable javascript and cookies",
        "access denied", "request blocked", "captcha",
        "un instant", "vérification en cours",  # fr: Cloudflare "just a moment" / "verifying"
    )

    # -- budgets / timeouts --
    default_timeout_ms: int = 45000
    settle_wait_ms: int = 2500
    homepage_wait_ms: int = 2000
    scroll_wait_ms: int = 1500
    body_scroll_wait_ms: int = 1200

    # -- scoring --
    scoring: ScoringWeights = field(default_factory=ScoringWeights)

    # -- verification run (decision.py) --
    verify_max_vehicles: int = 25
    verify_max_seconds: float = 90.0
    max_verification_attempts: int = 3

    # -- coordinator budget --
    default_budget_seconds: float = 300.0

    @classmethod
    def from_overrides(cls, overrides: dict | None = None) -> "HeuristicsConfig":
        """Apply scalar overrides (e.g. Settings.discovery_heuristics_overrides)
        onto a fresh HeuristicsConfig. Only existing int/float/str/bool fields are
        override-able — regex/lexicon fields need code changes, not a JSON blob."""
        cfg = cls()
        for key, value in (overrides or {}).items():
            if not hasattr(cfg, key):
                continue
            current = getattr(cfg, key)
            if isinstance(current, bool) == isinstance(value, bool) and isinstance(current, (int, float, str, bool)):
                try:
                    setattr(cfg, key, type(current)(value))
                except (TypeError, ValueError):
                    continue
        return cfg


DEFAULT_HEURISTICS = HeuristicsConfig()
