"""Weighted multi-signal confidence scoring.

Replaces the old confidence/verdict if/elif ladders (magic numbers 5, 10, 20,
4, 5, 6.0 scattered through the pre-refactor dealer_scraper/discovery.py) with
one auditable model: every signal that is PRESENT for a candidate contributes
raw * weight to the total, normalised by the sum of active weights (a signal
that is absent — e.g. a JSON-API candidate has no DOM/visual signals — does
not drag the score down just because it was never applicable).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .context import Candidate
from .evidence import EvidenceKind, EvidenceStore
from .heuristics import DEFAULT_HEURISTICS, HeuristicsConfig, ScoringWeights


@dataclass(frozen=True)
class SignalContribution:
    signal: str
    raw: float          # 0..1 normalised signal value
    weight: float
    contribution: float  # raw * weight


@dataclass(frozen=True)
class CandidateScore:
    total: float                                   # 0..1
    contributions: tuple[SignalContribution, ...]
    hard_blockers: tuple[str, ...] = ()             # caps verdict regardless of total


def _volume_raw(count: int | None, ceiling: int = 50) -> float | None:
    """Log-scaled 0..1: a handful of vehicles is weak evidence, dozens is strong,
    beyond `ceiling` adds nothing further (mirrors the legacy min(len, 50) cap)."""
    if not count or count <= 0:
        return None
    return min(1.0, math.log(count + 1) / math.log(ceiling + 1))


class ConfidenceModel:
    """Scores one Candidate using whatever signals were recorded for it, either
    directly on Candidate.draft (concrete numbers the coordinator/decision layer
    already computed) or as Evidence attached to the candidate's key."""

    def __init__(self, heuristics: HeuristicsConfig = DEFAULT_HEURISTICS) -> None:
        self.heuristics = heuristics
        self.weights: ScoringWeights = heuristics.scoring

    def score(self, cand: Candidate, evidence: EvidenceStore) -> CandidateScore:
        w = self.weights
        contributions: list[SignalContribution] = []
        hard_blockers: list[str] = []

        def add(signal: str, raw: float | None, weight: float) -> None:
            if raw is None:
                return
            raw = max(0.0, min(1.0, raw))
            contributions.append(SignalContribution(signal, raw, weight, raw * weight))

        draft = cand.draft or {}

        # -- verification (decision.py fills this in after a real adapter run;
        #    Phase 1 leaves it unset, so this signal is simply absent) --
        verification = draft.get("verification")
        if verification is not None:
            raw = 1.0 if verification.get("ok") else float(verification.get("identity_rate") or 0.0)
            add("verification_success", raw, w.verification_success)

        # -- field mapping --
        field_map = draft.get("field_map")
        if field_map is not None:
            add("field_map_coverage", min(1.0, len(field_map) / 7), w.field_map_coverage)

        field_validity = draft.get("field_value_validity")
        add("field_value_validity", field_validity, w.field_value_validity)

        # -- pagination --
        pagination_verified = draft.get("pagination_verified")
        if pagination_verified is not None:
            raw = 1.0 if pagination_verified else w.pagination_inferred_factor
            add("pagination_verified", raw, w.pagination_verified)

        # -- volume / structure --
        add("item_volume", _volume_raw(draft.get("vehicles_found")), w.item_volume)
        add("structured_data", _volume_raw(draft.get("jsonld_count"), ceiling=5), w.structured_data)
        add("dom_repetition", _volume_raw(draft.get("dom_link_count"), ceiling=20), w.dom_repetition)
        add("semantic_match", draft.get("semantic_match"), w.semantic_match)
        add("total_count_agreement", draft.get("total_count_agreement"), w.total_count_agreement)
        add("cms_prior", draft.get("cms_prior"), w.cms_prior)
        add("learned_prior", draft.get("learned_prior"), w.learned_prior)
        add("visual_grid", draft.get("visual_grid"), w.visual_grid)

        llm_judgment = draft.get("llm_judgment")
        if llm_judgment is not None:
            add("llm_judgment", min(llm_judgment, w.llm_judgment), w.llm_judgment)

        # -- penalties / hard blockers --
        antibot = evidence.for_subject(cand.key, EvidenceKind.ANTIBOT_SIGNAL) or evidence.site_wide(
            EvidenceKind.ANTIBOT_SIGNAL)
        if antibot and any(e.payload.get("unresolved") for e in antibot):
            add("antibot_penalty", 1.0, w.antibot_penalty)
            hard_blockers.append("antibot_unresolved")

        if draft.get("volatile_auth_only"):
            add("volatile_auth_penalty", 1.0, w.volatile_auth_penalty)

        weight_sum = sum(c.weight for c in contributions if c.weight > 0)
        raw_total = sum(c.contribution for c in contributions)
        total = raw_total / weight_sum if weight_sum else 0.0
        total = max(0.0, min(1.0, total))
        if hard_blockers:
            total = min(total, 0.3)

        return CandidateScore(total=total, contributions=tuple(contributions),
                               hard_blockers=tuple(hard_blockers))

    def verdict(self, score: CandidateScore, verified: bool) -> tuple[str, str, bool]:
        """-> (confidence, verdict, requires_custom_work)."""
        w = self.weights
        if score.hard_blockers:
            return "failed", "unsupported", True

        if score.total >= w.ready_threshold and verified:
            return "high", "ready", False
        if score.total >= w.needs_review_threshold:
            return "medium", "needs_review", False
        return "low", "needs_review", True
