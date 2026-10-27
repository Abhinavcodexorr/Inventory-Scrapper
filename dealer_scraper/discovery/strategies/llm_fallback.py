"""LLM fallback strategy: use Claude when heuristics are ambiguous.

Only invoked when:
1. discovery_llm_enabled is True
2. Confidence is 0.45–0.75 (ambiguous, not low/high)
3. LLM budget allows
4. At least one candidate field mapping is uncertain

Never used as primary discovery path.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..context import DiscoveryContext
from ..evidence import Evidence, EvidenceKind
from ..llm import LlmFieldMapper
from . import Strategy, CostClass, StrategyOutcome


@dataclass
class LlmFallbackStrategy(Strategy):
    """Use Claude API for field mapping disambiguation.

    Phase 4 implementation will:
    - Detect ambiguous field mappings (multiple candidates with similar scores)
    - Call LlmFieldMapper to disambiguate
    - Log prompt/response in report as LLM_JUDGMENT evidence
    - Update field_map based on LLM judgment
    - Track LLM calls against budget
    """
    name: str = "llm_fallback"
    phase: str = "decide"
    cost: CostClass = CostClass.EXPENSIVE

    def applicable(self, ctx: DiscoveryContext) -> bool:
        """Check if LLM is enabled and we have budget."""
        if not ctx.llm_budget or not ctx.llm_budget.available():
            return False

        # Check if any candidates have uncertain field mappings
        for cand in ctx.candidates.values():
            if cand.draft.get("field_map") and len(cand.draft["field_map"]) < 5:
                return True

        return False

    def run(self, ctx: DiscoveryContext) -> StrategyOutcome:
        """Attempt to disambiguate field mappings with LLM.

        Phase 4 stub — currently a no-op.
        """
        outcome = StrategyOutcome()

        if not ctx.llm_budget or not ctx.llm_budget.available():
            return outcome

        ctx.report.add_note("LLM fallback strategy not yet implemented (Phase 4)")
        return outcome
