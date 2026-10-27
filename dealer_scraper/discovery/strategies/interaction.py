"""Interaction-gated capture strategy: probe sites that require user interaction.

Some dealer sites hide inventory behind filters, dropdowns, or modal dialogs
that must be interacted with to reveal the actual listing. This strategy
detects and captures those flows.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..context import DiscoveryContext
from ..evidence import Evidence, EvidenceKind
from . import Strategy, CostClass, StrategyOutcome


@dataclass
class InteractionGatedStrategy(Strategy):
    """Detect interaction-gated inventory (filters, modals, etc).

    Phase 3 stub — real implementation will:
    - Identify filter/dropdown/modal elements blocking inventory
    - Simulate interactions (click, select, submit)
    - Capture revealed listings and APIs
    - Track interaction chains for reproducibility
    """
    name: str = "interaction_gated"
    phase: str = "capture"
    cost: CostClass = CostClass.BROWSER

    def applicable(self, ctx: DiscoveryContext) -> bool:
        """Check if site appears to have interaction-gated inventory."""
        # Heuristic: look for common filter/modal selectors
        # Real implementation checks actual DOM
        return True  # Phase 3: will be refined

    def run(self, ctx: DiscoveryContext) -> StrategyOutcome:
        """Attempt to detect and interact with inventory gates.

        Phase 3 stub — currently a no-op that logs a note.
        """
        outcome = StrategyOutcome()
        ctx.report.add_note("Interaction-gated inventory detection not yet implemented (Phase 3)")
        return outcome
