"""LLM fallback tier: Claude API for ambiguous field mapping and discovery.

Used only when confidence is ambiguous (0.45–0.75) and explicitly enabled
via `discovery_llm_enabled` setting. Budget-capped, logged in report.

Never used as primary discovery path — heuristics/evidence always run first.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .evidence import Evidence, EvidenceKind
from .heuristics import HeuristicsConfig


@dataclass
class LlmBudget:
    """Budget tracking for LLM API calls."""
    max_calls: int
    calls_used: int = 0
    total_tokens: int = 0
    max_tokens: int = 10_000

    def available(self) -> bool:
        return self.calls_used < self.max_calls and self.total_tokens < self.max_tokens

    def use(self, tokens: int) -> bool:
        if not self.available():
            return False
        self.calls_used += 1
        self.total_tokens += tokens
        return True


class LlmFieldMapper:
    """Use Claude to disambiguate field mappings when heuristics are uncertain.

    Phase 4 stub — full implementation uses anthropic SDK with structured output.
    """

    def __init__(self, api_key: str | None = None, budget: LlmBudget | None = None):
        self.api_key = api_key
        self.budget = budget or LlmBudget(max_calls=1)

    def map_fields(
        self,
        sample_items: list[dict],
        required_fields: list[str],
        confidence_threshold: float = 0.5,
    ) -> dict[str, str] | None:
        """Use LLM to map field names when heuristics fail.

        Phase 4 stub — returns None (no LLM call).
        Real implementation:
        - Calls Claude with sample items and field list
        - Returns field_map if confidence >= threshold
        - Records LLM_JUDGMENT evidence
        - Logs prompt/response in report
        """
        if not self.budget or not self.budget.available():
            return None

        # Phase 4: placeholder
        return None

    def classify_page(self, html: str) -> str | None:
        """Use LLM to determine page type (listing, detail, filter, etc).

        Phase 4 stub — returns None.
        """
        if not self.budget or not self.budget.available():
            return None

        # Phase 4: placeholder
        return None

    def expand_lexicon(
        self,
        known_terms: list[str],
        language: str = "en",
    ) -> list[str] | None:
        """Use LLM to expand search terms (e.g., "vehicules" for "vehicles").

        Phase 4 stub — returns None.
        """
        if not self.budget or not self.budget.available():
            return None

        # Phase 4: placeholder
        return None
