"""Regression tests: discovery must not run the scraper.

Discovery detects configuration; the scraper collects inventory. These tests
guard against re-introducing adapter verification or pagination walking into
the discovery path.

Run:  python -m tests.test_discovery_separation
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from dealer_scraper.discovery.context import Candidate, CandidateKind, DiscoveryBudgetExceeded, DiscoveryContext
from dealer_scraper.discovery.coordinator import DiscoveryCoordinator
from dealer_scraper.discovery.decision import DecisionEngine
from dealer_scraper.discovery.heuristics import DEFAULT_HEURISTICS


def test_coordinator_emit_json_api_does_not_call_verify():
    """Discovery must not invoke DecisionEngine.verify() — that is scraper work."""
    engine = DecisionEngine(DEFAULT_HEURISTICS)
    coordinator = DiscoveryCoordinator("https://example.com")
    coordinator.decision = engine

    ctx = DiscoveryContext(base_url="https://example.com", host="example.com",
                             heuristics=DEFAULT_HEURISTICS)
    from dealer_scraper.discovery.coordinator import DiscoveryResult
    result_obj = DiscoveryResult(base_url="https://example.com")

    json_best = {
        "url": "https://example.com/api/inventory",
        "list_path": "results",
        "array": [{"id": "1", "vin": "1HGCM82633A004352", "make": "Honda",
                   "model": "Accord", "year": 2020, "price": 25000}],
        "root": {"results": [], "total": 1},
        "score": 10,
        "method": "GET",
        "headers": {},
    }
    field_map = {"external_id": "id", "vin": "vin", "make": "make", "model": "model", "year": "year"}

    with patch.object(engine, "verify", wraps=engine.verify) as verify_mock:
        coordinator._emit_json_api(
            ctx, result_obj, json_best, field_map, "total",
            {"param": "page", "template_url": "https://example.com/api?page=1",
             "start": 1, "step": 1, "size_param": None, "size_value": None},
            None, "https://example.com/inventory", "https://example.com",
        )
        verify_mock.assert_not_called()


def test_assess_readiness_json_api_without_adapter():
    engine = DecisionEngine(DEFAULT_HEURISTICS)
    cand = Candidate(
        key="api:test", kind=CandidateKind.JSON_API, url="https://example.com/api",
        draft={
            "field_map": {"external_id": "id", "vin": "vin", "make": "make", "model": "model"},
            "sample_array_len": 5,
            "vehicles_found": 5,
            "pagination_verified": True,
        },
    )
    score = engine.score(cand, ctx_evidence_store := __import__(
        "dealer_scraper.discovery.evidence", fromlist=["EvidenceStore"]).EvidenceStore())
    ready, reason = engine.assess_readiness("json_api", cand, score)
    assert ready, reason


def test_assess_readiness_rejects_insufficient_fields():
    engine = DecisionEngine(DEFAULT_HEURISTICS)
    cand = Candidate(
        key="api:test", kind=CandidateKind.JSON_API, url="https://example.com/api",
        draft={"field_map": {"vin": "vin"}, "sample_array_len": 1},
    )
    from dealer_scraper.discovery.evidence import EvidenceStore
    score = engine.score(cand, EvidenceStore())
    ready, reason = engine.assess_readiness("json_api", cand, score)
    assert not ready
    assert "field_map" in reason.lower()


def test_budget_exceeded_not_swallowed_as_generic_failure():
    coordinator = DiscoveryCoordinator("https://example.com")

    def _budget(self, ctx, result):
        raise DiscoveryBudgetExceeded("budget exhausted")

    coordinator._run_browser_phase = _budget.__get__(coordinator)
    result = coordinator.run()

    assert "budget exhausted" in result.summary.lower() or any(
        "budget" in n.lower() for n in result.notes)
    assert result.report.decision.rationale == "budget_exhausted"


def test_reveal_lazy_cards_scrolls_then_waits_once():
    from dealer_scraper.discovery.browser.scroll import reveal_lazy_cards

    page = MagicMock()
    page.mouse.wheel = MagicMock()
    with patch("dealer_scraper.discovery.browser.scroll.wait_for_stable") as wait_mock:
        n = reveal_lazy_cards(page, rounds=3)
    assert n == 3
    assert page.mouse.wheel.call_count == 3
    wait_mock.assert_called_once()


def _emit_json_api(pagination_json):
    coordinator = DiscoveryCoordinator("https://example.com")
    ctx = DiscoveryContext(base_url="https://example.com", host="example.com",
                             heuristics=DEFAULT_HEURISTICS)
    from dealer_scraper.discovery.coordinator import DiscoveryResult
    result_obj = DiscoveryResult(base_url="https://example.com")
    json_best = {
        "url": "https://example.com/api/inventory",
        "list_path": "results",
        "array": [{"id": "1", "vin": "1HGCM82633A004352", "make": "Honda",
                   "model": "Accord", "year": 2020, "price": 25000}],
        "root": {"results": [], "total": 100},
        "score": 10,
        "method": "GET",
        "headers": {},
    }
    field_map = {"external_id": "id", "vin": "vin", "make": "make", "model": "model", "year": "year"}
    coordinator._emit_json_api(
        ctx, result_obj, json_best, field_map, "total",
        pagination_json, None, "https://example.com/inventory", "https://example.com",
    )
    return result_obj


def test_json_api_config_always_allows_scraper_pagination():
    """Discovery must not cap json_api at one page — scraper walks all pages."""
    result_obj = _emit_json_api({
        "param": "page", "size_param": None, "size_value": None,
        "template_url": "https://example.com/api/inventory?page=1",
        "start": 1, "step": 1,
    })
    assert result_obj.ok
    assert result_obj.scrape_config.get("max_pages") == 500
    assert "{page}" in result_obj.scrape_config["endpoint"]


def test_json_api_with_a_total_but_no_pagination_is_refused():
    """The feed says 100 vehicles and shows 1 per request, and no page parameter
    was found. Emitting that config scrapes page 1 on every request and stores a
    fraction of the lot while reporting success (B Town: 120 scraped, 12 stored).
    Discovery must say it could not find pagination instead."""
    result_obj = _emit_json_api(None)
    assert not result_obj.ok, "a config that can only read page 1 must not be emitted"


def main() -> None:
    test_coordinator_emit_json_api_does_not_call_verify()
    test_assess_readiness_json_api_without_adapter()
    test_assess_readiness_rejects_insufficient_fields()
    test_budget_exceeded_not_swallowed_as_generic_failure()
    test_reveal_lazy_cards_scrolls_then_waits_once()
    test_json_api_config_always_allows_scraper_pagination()
    test_json_api_with_a_total_but_no_pagination_is_refused()
    print("DISCOVERY SEPARATION TEST PASSED")


if __name__ == "__main__":
    main()
