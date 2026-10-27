"""Shared mutable state for one discovery run."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from ..timing import StageTimer
from .diagnostics import DiscoveryReport
from .evidence import EvidenceStore
from .heuristics import DEFAULT_HEURISTICS, HeuristicsConfig


class DiscoveryBudgetExceeded(Exception):
    """Raised when discovery remaining budget falls below a safety threshold."""
    pass


class CandidateKind(str, Enum):
    HTML_LISTING = "html_listing"
    JSON_API = "json_api"
    GRAPHQL = "graphql"
    HYDRATION = "hydration"
    SITEMAP_FEED = "sitemap_feed"


@dataclass
class Candidate:
    key: str                          # stable id, e.g. "api:GET host/path" or "page:/inventory"
    kind: CandidateKind
    url: str
    draft: dict[str, Any] = field(default_factory=dict)   # accumulating config fragments + raw signals
    score: Any = None                 # scoring.CandidateScore, set once scored
    rejected_reason: str | None = None


class DiscoveryCancelled(Exception):
    """Raised via DiscoveryContext.check_cancel() when a run has been asked to stop."""


@dataclass
class DiscoveryContext:
    base_url: str
    host: str
    heuristics: HeuristicsConfig = field(default_factory=lambda: DEFAULT_HEURISTICS)
    deadline: float = field(default_factory=lambda: time.monotonic() + 300.0)
    evidence: EvidenceStore = field(default_factory=EvidenceStore)
    candidates: dict[str, Candidate] = field(default_factory=dict)
    report: DiscoveryReport = None  # type: ignore[assignment]  # set in __post_init__
    # Browser/traffic wiring lands in Phase 2/3 (browser.session.SessionManager,
    # net.recorder.TrafficRecorder) — kept loosely typed so Phase 1 has nothing
    # concrete to depend on yet.
    browser: Any | None = None
    traffic: Any | None = None
    snapshots: dict[str, Any] = field(default_factory=dict)
    priors: list[Any] = field(default_factory=list)
    # strategies.sitemap.SitemapDiscovery, set by the coordinator's early
    # (browser-free) sitemap phase if it finds anything — corroborating
    # evidence for the total_count_agreement signal, never authoritative.
    sitemap: Any | None = None
    check_cancel: Callable[[], None] = field(default=lambda: None)
    progress: Callable[[str, int, int], None] = field(default=lambda phase, done, total: None)
    llm_budget: Any | None = None
    timer: StageTimer = field(default_factory=StageTimer)

    def __post_init__(self) -> None:
        if self.report is None:
            self.report = DiscoveryReport(base_url=self.base_url, budget_seconds=self.remaining_seconds())

    def remaining_seconds(self) -> float:
        return max(0.0, self.deadline - time.monotonic())

    def ensure_time(self, minimum_seconds: float = 30.0) -> None:
        """Raise DiscoveryBudgetExceeded if remaining time is below minimum_seconds."""
        if self.remaining_seconds() < minimum_seconds:
            raise DiscoveryBudgetExceeded(
                f"Discovery budget exhausted: {self.remaining_seconds():.1f}s remaining, "
                f"need {minimum_seconds}s minimum"
            )

    def upsert_candidate(self, cand: Candidate) -> Candidate:
        existing = self.candidates.get(cand.key)
        if existing is None:
            self.candidates[cand.key] = cand
            return cand
        existing.draft.update(cand.draft)
        if cand.rejected_reason:
            existing.rejected_reason = cand.rejected_reason
        return existing
