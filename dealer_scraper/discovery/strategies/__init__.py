"""Strategy protocol: the unit of work the coordinator schedules.

A Strategy inspects/mutates a shared DiscoveryContext and reports what it did
via StrategyOutcome. A strategy that raises or reports an error is recorded and
skipped — it never aborts the run (see coordinator.DiscoveryCoordinator).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from ..context import DiscoveryContext


class CostClass(IntEnum):
    """How expensive a strategy is to run — cheap analyzers can always run;
    BROWSER/EXPENSIVE ones are what the coordinator's early-exit policy skips
    once a high-confidence candidate has already been found."""

    CHEAP = 0        # pure computation on data already in ctx
    NETWORK = 1      # httpx requests (robots, sitemap, probes)
    BROWSER = 2      # needs a live Playwright page
    EXPENSIVE = 3    # verification runs, LLM calls


@dataclass
class StrategyOutcome:
    evidence_count: int = 0
    candidates_touched: list[str] = field(default_factory=list)
    error: str | None = None          # non-None = failed; coordinator records, continues


@runtime_checkable
class Strategy(Protocol):
    name: str
    phase: str
    cost: CostClass

    def applicable(self, ctx: "DiscoveryContext") -> bool:
        """Cheap precondition check — False means "skip silently", not a failure."""
        ...

    def run(self, ctx: "DiscoveryContext") -> StrategyOutcome:
        """Do the work, mutate ctx (candidates/evidence/snapshots), report what happened."""
        ...


# phase name -> ordered list of Strategy instances registered for it.
STRATEGY_REGISTRY: dict[str, list[Strategy]] = {}


def register(strategy: Strategy) -> Strategy:
    STRATEGY_REGISTRY.setdefault(strategy.phase, []).append(strategy)
    return strategy
