"""Typed evidence: every analyzer/strategy reports what it found as an Evidence
record instead of an ad-hoc prose string, so confidence scoring and diagnostics
can both consume the same structured facts.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class EvidenceKind(str, Enum):
    NAV_LINK = "nav_link"
    SITEMAP_URL = "sitemap_url"
    JSONLD_VEHICLE = "jsonld_vehicle"
    MICRODATA = "microdata"
    HYDRATION_STATE = "hydration_state"
    API_RESPONSE = "api_response"
    GRAPHQL_RESPONSE = "graphql_response"
    DOM_REPETITION = "dom_repetition"
    SEMANTIC_MATCH = "semantic_match"
    VISUAL_GRID = "visual_grid"
    RESULT_COUNT_TEXT = "result_count_text"
    PAGINATION_VERIFIED = "pagination_verified"
    CMS_FINGERPRINT = "cms_fingerprint"
    ANTIBOT_SIGNAL = "antibot_signal"
    LEARNED_PRIOR = "learned_prior"
    LLM_JUDGMENT = "llm_judgment"
    VERIFICATION_RESULT = "verification_result"
    FIELD_VALUE_VALID = "field_value_valid"
    VOLATILE_AUTH = "volatile_auth"


@dataclass(frozen=True, slots=True)
class Evidence:
    kind: EvidenceKind
    source: str                      # strategy/analyzer name that produced it
    subject: str                     # candidate key this evidence is about ("" = site-wide)
    weight: float                    # producer's raw signal strength (0..1)
    payload: dict[str, Any] = field(default_factory=dict)
    note: str = ""                   # one human-readable line (feeds diagnostics.to_notes())


class EvidenceStore:
    """Append-only evidence log for one discovery run, indexed by subject."""

    def __init__(self) -> None:
        self._items: list[Evidence] = []

    def add(self, ev: Evidence) -> None:
        self._items.append(ev)

    def for_subject(self, subject: str, kind: EvidenceKind | None = None) -> list[Evidence]:
        return [e for e in self._items
                if e.subject == subject and (kind is None or e.kind == kind)]

    def site_wide(self, kind: EvidenceKind | None = None) -> list[Evidence]:
        return self.for_subject("", kind)

    def all(self) -> list[Evidence]:
        return list(self._items)

    def __len__(self) -> int:
        return len(self._items)
