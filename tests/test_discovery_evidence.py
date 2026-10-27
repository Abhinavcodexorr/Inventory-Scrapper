"""Unit tests for the evidence store and confidence scoring model.

Pure functions/dataclasses only — no Playwright, no DB needed.

Run:  python -m tests.test_discovery_evidence
"""
from __future__ import annotations

from dealer_scraper.discovery.context import Candidate, CandidateKind
from dealer_scraper.discovery.evidence import Evidence, EvidenceKind, EvidenceStore
from dealer_scraper.discovery.heuristics import DEFAULT_HEURISTICS
from dealer_scraper.discovery.scoring import ConfidenceModel, _volume_raw


def test_evidence_store_filters_by_subject_and_kind():
    store = EvidenceStore()
    store.add(Evidence(EvidenceKind.API_RESPONSE, "api_sniff", "api:x", weight=1.0))
    store.add(Evidence(EvidenceKind.PAGINATION_VERIFIED, "dom_pagination", "api:x", weight=1.0))
    store.add(Evidence(EvidenceKind.ANTIBOT_SIGNAL, "explore_homepage", "", weight=1.0,
                        payload={"unresolved": True}))

    assert len(store) == 3
    assert len(store.for_subject("api:x")) == 2
    assert len(store.for_subject("api:x", EvidenceKind.API_RESPONSE)) == 1
    assert len(store.for_subject("nonexistent")) == 0
    assert len(store.site_wide()) == 1
    assert len(store.site_wide(EvidenceKind.ANTIBOT_SIGNAL)) == 1
    assert len(store.all()) == 3


def test_volume_raw_scales_and_caps():
    assert _volume_raw(None) is None
    assert _volume_raw(0) is None
    low = _volume_raw(2, ceiling=50)
    high = _volume_raw(50, ceiling=50)
    beyond = _volume_raw(500, ceiling=50)
    assert 0 < low < high <= 1.0
    assert beyond == 1.0  # capped, never exceeds 1.0 even far past the ceiling


def _json_candidate(**draft_overrides) -> Candidate:
    draft = {
        "field_map": {"external_id": "id", "vin": "vin", "make": "make", "model": "model",
                       "year": "year", "price": "price", "url": "vdp_url"},
        "vehicles_found": 120,
        "pagination_verified": True,
    }
    draft.update(draft_overrides)
    return Candidate(key="api:example.com/v1", kind=CandidateKind.JSON_API,
                      url="https://example.com/v1", draft=draft)


def test_confidence_model_scores_present_signals_only():
    model = ConfidenceModel(DEFAULT_HEURISTICS)
    store = EvidenceStore()
    cand = _json_candidate()

    score = model.score(cand, store)
    signals = {c.signal for c in score.contributions}
    # Only signals whose underlying data was present on the candidate/evidence
    # contribute — dom_repetition/visual_grid/etc. were never set here.
    assert signals == {"field_map_coverage", "pagination_verified", "item_volume"}
    assert 0.0 < score.total <= 1.0
    assert score.hard_blockers == ()


def test_confidence_model_normalizes_denominator_by_active_weight():
    # A candidate with FEWER present signals should not be punished merely for
    # having fewer applicable signals — score is normalised by active weight,
    # not by the full signal catalogue.
    model = ConfidenceModel(DEFAULT_HEURISTICS)
    store = EvidenceStore()
    rich = _json_candidate()
    sparse = _json_candidate(pagination_verified=None)
    del sparse.draft["pagination_verified"]

    rich_score = model.score(rich, store)
    sparse_score = model.score(sparse, store)
    # Both are strong on the signals they DO have (full field_map + volume),
    # so scores should be close, not artificially low for the sparse one.
    assert abs(rich_score.total - sparse_score.total) < 0.35


def test_confidence_model_hard_blocker_caps_score_and_verdict():
    model = ConfidenceModel(DEFAULT_HEURISTICS)
    store = EvidenceStore()
    cand = _json_candidate()
    store.add(Evidence(EvidenceKind.ANTIBOT_SIGNAL, "explore_homepage", cand.key, weight=1.0,
                        payload={"unresolved": True}))

    score = model.score(cand, store)
    assert "antibot_unresolved" in score.hard_blockers
    assert score.total <= 0.3

    confidence, verdict, requires_custom_work = model.verdict(score, verified=True)
    assert confidence == "failed"
    assert verdict == "unsupported"
    assert requires_custom_work is True


def test_confidence_model_verdict_requires_verification_for_ready():
    model = ConfidenceModel(DEFAULT_HEURISTICS)
    store = EvidenceStore()
    cand = _json_candidate()
    score = model.score(cand, store)

    unverified = model.verdict(score, verified=False)
    verified = model.verdict(score, verified=True)
    assert unverified[1] in ("needs_review", "unsupported")
    # Same score, but verification turns a strong candidate into "ready" — this
    # is the Phase 3 seam: verification.ok becomes this boolean once the
    # verification run in decision.py is no longer a stub.
    if score.total >= DEFAULT_HEURISTICS.scoring.ready_threshold:
        assert verified[0] == "high" and verified[1] == "ready"


def main() -> None:
    test_evidence_store_filters_by_subject_and_kind()
    test_volume_raw_scales_and_caps()
    test_confidence_model_scores_present_signals_only()
    test_confidence_model_normalizes_denominator_by_active_weight()
    test_confidence_model_hard_blocker_caps_score_and_verdict()
    test_confidence_model_verdict_requires_verification_for_ready()
    print("DISCOVERY EVIDENCE/SCORING TEST PASSED")


if __name__ == "__main__":
    main()
