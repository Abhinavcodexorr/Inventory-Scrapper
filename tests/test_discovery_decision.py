"""Unit tests for decision.py's config builders + validation.

Verifies every config discovery can emit actually round-trips through the
SAME validate_scrape_config()/build_adapter() the dealer save/scrape path
uses — a discovered config must never be something the API would 422 later.

Pure functions/classes only — no Playwright, no DB needed.

Run:  python -m tests.test_discovery_decision
"""
from __future__ import annotations

from dealer_scraper.adapters.json_api import JsonApiAdapter
from dealer_scraper.adapters.browser import BrowserAdapter
from dealer_scraper.adapters.registry import build_adapter, validate_scrape_config
from dealer_scraper.discovery.context import Candidate, CandidateKind
from dealer_scraper.discovery.decision import DecisionEngine
from dealer_scraper.discovery.heuristics import DEFAULT_HEURISTICS


def test_json_api_builder_round_trips_through_validate_and_build_adapter():
    engine = DecisionEngine(DEFAULT_HEURISTICS)
    cand = Candidate(
        key="api:example.com/v1/vehicles", kind=CandidateKind.JSON_API,
        url="https://example.com/v1/vehicles",
        draft={
            "endpoint": "https://example.com/v1/vehicles?page={page}&limit={page_size}",
            "list_path": "results.vehicles",
            "field_map": {"external_id": "id", "vin": "vin", "make": "make", "model": "model"},
            "page_size": 25, "start_page": 1, "total_path": "results.total",
            "headers": {"Accept": "application/json"}, "max_pages": 500,
        },
    )
    config = engine.build_config("json_api", cand)
    assert config["endpoint"].startswith("https://example.com/")
    assert config["field_map"]["vin"] == "vin"

    error = engine.validate("json_api", config, "https://example.com")
    assert error is None

    adapter = build_adapter("json_api", config)
    assert isinstance(adapter, JsonApiAdapter)
    assert adapter.field_map == config["field_map"]


def test_json_api_builder_rejects_cross_site_url_base():
    # A discovered config whose url_base points somewhere else must fail the
    # SAME guard the dealer save endpoint enforces — discovery is not exempt.
    engine = DecisionEngine(DEFAULT_HEURISTICS)
    cand = Candidate(
        key="api:other.example/v1", kind=CandidateKind.JSON_API, url="https://other.example/v1",
        draft={
            "endpoint": "https://other.example/v1/vehicles",
            "field_map": {"external_id": "id"},
            "url_base": "https://not-the-dealer.example",
        },
    )
    config = engine.build_config("json_api", cand)
    error = engine.validate("json_api", config, "https://the-real-dealer.example")
    assert error is not None
    assert "not-the-dealer.example" in error


def test_browser_builder_round_trips_through_validate_and_build_adapter():
    engine = DecisionEngine(DEFAULT_HEURISTICS)
    cand = Candidate(
        key="page:https://example.com/inventory", kind=CandidateKind.HTML_LISTING,
        url="https://example.com/inventory",
        draft={
            "list_url": "https://example.com/inventory",
            "base_url": "https://example.com",
            "card_anchor": "a[href*='/vehicles/']",
            "id_regex": r"/vehicles/([\w-]+)",
            "extract": "dom",
            "paginate": "scroll",
            "scroll_rounds": 80,
            "max_pages": 200,
        },
    )
    config = engine.build_config("browser", cand)
    assert config["list_url"] == "https://example.com/inventory"

    error = engine.validate("browser", config, "https://example.com")
    assert error is None

    adapter = build_adapter("browser", config)
    assert isinstance(adapter, BrowserAdapter)


def test_browser_builder_missing_list_url_fails_validation():
    engine = DecisionEngine(DEFAULT_HEURISTICS)
    cand = Candidate(key="page:bad", kind=CandidateKind.HTML_LISTING, url="https://example.com",
                      draft={})
    try:
        engine.build_config("browser", cand)
        raised = False
    except Exception:
        raised = True
    assert raised, "BrowserConfig should require list_url"


def test_verify_handles_invalid_config():
    """Verification gracefully handles invalid configs during build_adapter."""
    engine = DecisionEngine(DEFAULT_HEURISTICS)
    result = engine.verify("json_api", {"endpoint": "https://x", "field_map": {}})
    assert result.attempted is True
    assert result.ok is False
    assert result.error is not None
    assert "missing required key" in result.error.lower()


def test_validate_scrape_config_directly_matches_decision_validate():
    # decision.validate() must be a thin, faithful wrapper — same outcome as
    # calling the registry validator directly.
    engine = DecisionEngine(DEFAULT_HEURISTICS)
    config = {"endpoint": "https://api.example.com/v1", "field_map": {"vin": "vin"}}
    direct_error = None
    try:
        validate_scrape_config("json_api", config, "https://example.com")
    except ValueError as exc:
        direct_error = str(exc)
    assert engine.validate("json_api", config, "https://example.com") == direct_error


def main() -> None:
    test_json_api_builder_round_trips_through_validate_and_build_adapter()
    test_json_api_builder_rejects_cross_site_url_base()
    test_browser_builder_round_trips_through_validate_and_build_adapter()
    test_browser_builder_missing_list_url_fails_validation()
    test_verify_handles_invalid_config()
    test_validate_scrape_config_directly_matches_decision_validate()
    print("DISCOVERY DECISION TEST PASSED")


if __name__ == "__main__":
    main()
