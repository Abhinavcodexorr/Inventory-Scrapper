"""Auto-discovery: derive a working scrape_config from a dealer's website URL.

Goal: onboarding a dealer should need only their homepage URL. This package
opens the site, finds the inventory listing, works out how it paginates and
where the vehicle data lives, and returns a ready-to-use scrape_config.

Two adapter shapes can come out of this:
  * json_api — preferred when the site's own frontend loads inventory from a
    JSON endpoint (most modern dealer SPAs do). We sniff the page's network
    traffic for an XHR/fetch response whose body looks like a list of vehicles,
    then derive endpoint/list_path/total_path/field_map from it directly. This
    is the adapter dealer_scraper/adapters/json_api.py was written for, and it
    is far more robust than scraping rendered HTML.
  * browser — the fallback when no such API is found: derived from the
    rendered DOM (schema.org JSON-LD if present, otherwise repeating detail
    links), same as before.

It is deliberately *evidence-based*: every proposal reports how many vehicles
it actually saw and a structured breakdown of every signal that contributed to
its confidence (see diagnostics.DiscoveryReport), so a low-confidence result
can be flagged for a human instead of silently scraping the wrong thing.

Architecture (see HANDOFF/07_DISCOVERY_ENGINE_REDESIGN.md for the full design):
    coordinator.py    orchestration + confidence/verdict decisions
    context.py        DiscoveryContext / Candidate — shared per-run state
    evidence.py        typed Evidence + EvidenceStore
    heuristics.py       every tunable constant, in one overridable config object
    scoring.py          weighted multi-signal confidence model
    decision.py         scrape_config builders + validation + signal-based readiness
    diagnostics.py       structured DiscoveryReport (notes[] is generated from it)
    persistence.py       DiscoveryRun DB row lifecycle
    strategies/, net/, analyzers/, browser/, cms/   pluggable pieces

Usage:
    from dealer_scraper.discovery import discover
    result = discover("https://cardora.ca")
    result.scrape_config   # -> dict, ready for the dealers row
    result.confidence      # -> "high" | "medium" | "low" | "failed"

Requires Playwright (already a dependency).
"""
from __future__ import annotations

from .coordinator import DiscoveryCoordinator, DiscoveryResult, discover
from .context import DiscoveryCancelled
from .diagnostics import DiscoveryReport
from .heuristics import DEFAULT_HEURISTICS, HeuristicsConfig


def run_discovery(run_id: int) -> dict:
    """Entry point used by worker/tasks.py::discover_site and the local-thread
    dispatch path — imported lazily so importing dealer_scraper.discovery (e.g.
    from the CLI, or from tests/test_discovery_parse.py) never requires a DB.
    """
    from .persistence import run_discovery as _run_discovery

    return _run_discovery(run_id)

# Re-exported so tests/test_discovery_parse.py (and any other historical
# `from dealer_scraper.discovery import _foo` import) keeps working unchanged
# after the module -> package conversion.
from .net.fieldmap import _build_field_map, _find_total_path, _needs_url_base
from .net.pagination_probe import (
    _detect_body_pagination,
    _detect_pagination,
    _template_body,
    _template_endpoint,
)
from .net.scorer import _best_vehicle_json, _extra_request_headers, _find_vehicle_array
from .strategies.pagination import (
    _DETAIL_LINKS_JS,
    _FIND_MORE_BUTTON_JS,
    _build_detail_id_regex,
    _detect_dom_pagination,
    _verify_dom_pagination,
)

__all__ = [
    "discover",
    "run_discovery",
    "DiscoveryCoordinator",
    "DiscoveryResult",
    "DiscoveryReport",
    "DiscoveryCancelled",
    "HeuristicsConfig",
    "DEFAULT_HEURISTICS",
]
