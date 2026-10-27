"""CMS detection strategy: identify dealer platform and set detected_platform.

This strategy detects the CMS/platform powering a dealer site using fingerprints.
When successful, it sets `dealers.detected_platform` for audit/analytics.
Detection is used to seed priors but never bypasses verification.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..cms.fingerprints import detect_cms_platform, get_most_likely_platform
from ..context import DiscoveryContext
from ..evidence import Evidence, EvidenceKind
from . import Strategy, CostClass, StrategyOutcome


@dataclass
class CmsDetectionStrategy(Strategy):
    """Detect the CMS platform powering a dealer site."""
    name: str = "cms_detection"
    phase: str = "analyze"
    cost: CostClass = CostClass.CHEAP

    def applicable(self, ctx: DiscoveryContext) -> bool:
        """CMS detection is always applicable if we have a homepage snapshot."""
        return bool(ctx.snapshots.get("homepage"))

    def run(self, ctx: DiscoveryContext) -> StrategyOutcome:
        """Detect CMS platform from homepage HTML."""
        outcome = StrategyOutcome()

        homepage = ctx.snapshots.get("homepage")
        if not homepage or not homepage.html:
            return outcome

        # Detect platform
        scores = detect_cms_platform(homepage.html)
        platform = get_most_likely_platform(scores)

        if platform:
            # Record as evidence
            ctx.evidence.add(Evidence(
                kind=EvidenceKind.CMS_FINGERPRINT,
                source="cms_fingerprints",
                subject="",
                weight=scores.get(platform, 0.5),
                payload={"platform": platform, "scores": scores},
                note=f"Detected CMS platform: {platform}"
            ))

            ctx.report.add_note(f"Detected CMS platform: {platform} (confidence: {scores[platform]:.0%})")
            outcome.metadata["detected_platform"] = platform

        return outcome
