"""Structured data detection strategy: discover inventory via JSON-LD, microdata, etc.

Many modern dealer sites embed structured data (JSON-LD, microdata, Apollo cache)
in HTML. This strategy detects and extracts from those sources as an alternative
to DOM scraping or API sniffing.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..context import DiscoveryContext
from ..evidence import Evidence, EvidenceKind
from . import Strategy, CostClass, StrategyOutcome


@dataclass
class StructuredDataStrategy(Strategy):
    """Extract inventory from structured data markers (JSON-LD, microdata, etc).

    Phase 3 implementation will:
    - Detect __NEXT_DATA__, __NUXT__, __INITIAL_STATE__, Apollo cache
    - Extract JSON-LD from <script type="application/ld+json">
    - Parse microdata itemscope/itemtype attributes
    - Map extracted fields to normalized vehicle shape
    - Add STRUCTURED_DATA evidence for each extracted record
    """
    name: str = "structured_data"
    phase: str = "analyze"
    cost: CostClass = CostClass.CHEAP

    def applicable(self, ctx: DiscoveryContext) -> bool:
        """Check if site likely has structured data."""
        # Will look for markers in snapshots
        return bool(ctx.snapshots.get("homepage"))

    def run(self, ctx: DiscoveryContext) -> StrategyOutcome:
        """Attempt to extract inventory from structured data.

        Phase 3 stub — currently a no-op that logs a note.
        """
        outcome = StrategyOutcome()
        ctx.report.add_note("Structured data extraction not yet implemented (Phase 3)")
        return outcome
