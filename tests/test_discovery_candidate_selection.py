"""Regression tests for the clutch.ca "scraped page 1 and stopped" failure.

The live symptom was 32 of ~3,161 vehicles, reported green. Four separate
defects lined up to produce it, and each gets a test here so the combination
cannot come back:

  1. `_candidate_urls` ranked homepage links in raw DOM order, and the nav
     lexicon matches "car" inside /auto-financing/car-loan-calculator. The
     calculator page (which embeds a vehicle grid) sorted above /cars.
  2. The candidate loop broke out on the first page clearing
     candidate_score_sufficient, so /cars was never even loaded.
  3. Pagination was therefore probed on the calculator page, where ?page=N is
     ignored — so it concluded "click" against a View More button that isn't
     there, and the config captured one page.
  4. Nothing treated "sitemap says 3,100, config reaches 32" as a problem.

Pure functions only — no Playwright, no DB needed.

Run:  python -m tests.test_discovery_candidate_selection
"""
from __future__ import annotations

from dealer_scraper.discovery.context import Candidate, CandidateKind
from dealer_scraper.discovery.decision import DecisionEngine
from dealer_scraper.discovery.heuristics import DEFAULT_HEURISTICS
from dealer_scraper.discovery.scoring import CandidateScore
from dealer_scraper.discovery.strategies.exploration import (
    _candidate_urls,
    candidate_rank,
)

# The clutch.ca homepage nav, in the order the DOM offers it: three pages that
# match the nav lexicon on "car"/"used" before the real listing appears.
CLUTCH_NAV = [
    {"href": "/auto-financing/car-loan-calculator", "text": "Car Loan Calculator"},
    {"href": "/sell-your-car", "text": "Sell your car"},
    {"href": "/blog/best-used-cars-2026", "text": "Best used cars of 2026"},
    {"href": "/cars", "text": "Shop cars"},
]


def test_loan_calculator_ranks_below_real_listing():
    """The exact URL clutch.ca was misconfigured with must lose to /cars."""
    assert candidate_rank("https://clutch.ca/auto-financing/car-loan-calculator") == 4
    assert candidate_rank("https://clutch.ca/cars") == 0

    ordered = _candidate_urls("https://clutch.ca", {"navLinks": CLUTCH_NAV}, "clutch.ca")
    assert ordered[0] == "https://clutch.ca/cars"

    calculator = "https://clutch.ca/auto-financing/car-loan-calculator"
    if calculator in ordered:
        assert ordered.index(calculator) > ordered.index("https://clutch.ca/cars")


def test_marketing_pages_that_match_the_nav_lexicon_all_rank_last():
    """nav_hints_re is deliberately loose; these must not outrank a listing."""
    for path in (
        "/auto-financing/car-loan-calculator",
        "/sell-your-car",
        "/blog/best-used-cars-2026",
        "/trade-in-appraisal",
        "/protection-plans",
        "/extended-warranties",
        "/lease-return",
        "/service/book-appointment",
        "/financement/calculatrice",
        "/vendre-votre-vehicule",
    ):
        assert candidate_rank(f"https://x.ca{path}") == 4, path


def test_real_listing_paths_rank_first():
    for path in ("/cars", "/inventory", "/vehicles", "/used-cars", "/vehicules-occasion"):
        assert candidate_rank(f"https://x.ca{path}") == 0, path
    assert candidate_rank("https://x.ca/inventory/used") <= 1


def test_site_nav_link_beats_guessed_paths_when_listing_path_is_unpredictable():
    """Regression on the fix itself.

    Ranking on path shape alone would fill all 6 candidate slots with guessed
    /inventory, /cars, /vehicles… and never reach a listing the site actually
    links to under a name we didn't predict.
    """
    nav = [
        {"href": "/financing", "text": "Financing"},
        {"href": "/browse-our-selection", "text": "Browse our vehicles"},
    ]
    ordered = _candidate_urls("https://x.ca", {"navLinks": nav}, "x.ca")
    assert ordered[0] == "https://x.ca/browse-our-selection"


def test_anchor_text_only_link_still_gets_evaluated_early():
    """A listing at an opaque path, identified only by its link text."""
    nav = [{"href": "/s/inv-9931", "text": "View our Inventory"}]
    ordered = _candidate_urls("https://y.ca", {"navLinks": nav}, "y.ca")
    assert ordered[0] == "https://y.ca/s/inv-9931"


def test_faceted_filter_links_do_not_crowd_out_other_candidates() -> None:
    """cardora.ca linked dozens of Algolia-filtered views of /inventory; five of
    the six candidate slots went to permutations of one page."""
    nav = [
        {"href": "/inventory/?refinementList[body_type][0]=Sedan", "text": "Sedans"},
        {"href": "/inventory/?refinementList[body_type][0]=SUV", "text": "SUVs"},
        {"href": "/inventory", "text": "All inventory"},
        {"href": "/inventory/?refinementList[vehicle_type][0]=Used", "text": "Used"},
    ]
    ordered = _candidate_urls("https://cardora.ca", {"navLinks": nav}, "cardora.ca")

    inventory = [u for u in ordered if u.rstrip("/").endswith("/inventory")
                 or "/inventory/?" in u]
    assert len(inventory) == 1, f"expected one /inventory candidate, got {inventory}"
    assert inventory[0] == "https://cardora.ca/inventory", (
        "the unfiltered listing must win over its own filtered views")


def test_filtered_url_survives_when_it_is_the_only_listing_link() -> None:
    """Dropping every query-bearing URL would lose sites whose only listing link
    carries a filter."""
    nav = [{"href": "/stock?type=used", "text": "Our used vehicles"}]
    ordered = _candidate_urls("https://x.ca", {"navLinks": nav}, "x.ca")
    assert "https://x.ca/stock?type=used" in ordered


def _clutch_shaped_candidate(**overrides) -> Candidate:
    draft = {
        "list_url": "https://clutch.ca/cars",
        "card_anchor": "a[href*='/vehicles/']",
        "vehicles_found": 32,
        "dom_link_count": 32,
        "jsonld_count": 32,
        "pagination_verified": False,
        "sitemap_detail_count": 3100,
    }
    draft.update(overrides)
    return Candidate(key="page:x", kind=CandidateKind.HTML_LISTING,
                     url=draft["list_url"], draft=draft)


def _passing_score() -> CandidateScore:
    """A score comfortably above needs_review_threshold, so the sitemap check
    is what decides the outcome rather than the confidence floor."""
    return CandidateScore(total=0.76, contributions=[], hard_blockers=[])


def test_sitemap_contradiction_blocks_readiness():
    """32 reachable vs 3,100 in the sitemap, pagination unproven -> not ready."""
    engine = DecisionEngine(DEFAULT_HEURISTICS)
    ready, reason = engine.assess_readiness(
        "browser", _clutch_shaped_candidate(), _passing_score())
    assert ready is False
    assert "sitemap" in reason.lower()
    assert "3100" in reason and "32" in reason


def test_sitemap_contradiction_allows_readiness_when_pagination_is_proven():
    """The whole point: reaching 32 per page is fine if paging demonstrably
    works, because the scraper will walk the remaining pages."""
    engine = DecisionEngine(DEFAULT_HEURISTICS)
    ready, _ = engine.assess_readiness(
        "browser", _clutch_shaped_candidate(pagination_verified=True), _passing_score())
    assert ready is True


def test_sitemap_contradiction_ignored_when_counts_agree():
    engine = DecisionEngine(DEFAULT_HEURISTICS)
    ready, _ = engine.assess_readiness(
        "browser", _clutch_shaped_candidate(vehicles_found=3000, dom_link_count=3000),
        _passing_score())
    assert ready is True


def test_sitemap_contradiction_ignored_when_no_sitemap():
    """Plenty of dealer sites publish no usable sitemap; absence must not be
    read as disagreement."""
    engine = DecisionEngine(DEFAULT_HEURISTICS)
    ready, _ = engine.assess_readiness(
        "browser", _clutch_shaped_candidate(sitemap_detail_count=None), _passing_score())
    assert ready is True


def main() -> None:
    test_loan_calculator_ranks_below_real_listing()
    test_marketing_pages_that_match_the_nav_lexicon_all_rank_last()
    test_real_listing_paths_rank_first()
    test_site_nav_link_beats_guessed_paths_when_listing_path_is_unpredictable()
    test_anchor_text_only_link_still_gets_evaluated_early()
    test_faceted_filter_links_do_not_crowd_out_other_candidates()
    test_filtered_url_survives_when_it_is_the_only_listing_link()
    test_sitemap_contradiction_blocks_readiness()
    test_sitemap_contradiction_allows_readiness_when_pagination_is_proven()
    test_sitemap_contradiction_ignored_when_counts_agree()
    test_sitemap_contradiction_ignored_when_no_sitemap()
    print("PASSED")


if __name__ == "__main__":
    main()
