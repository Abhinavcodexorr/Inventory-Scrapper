"""Structured, machine-readable discovery diagnostics.

Replaces the pre-refactor DiscoveryResult.notes (a flat list[str] of prose —
the only diagnostics channel, never persisted) with a typed report: phases with
timings, strategies attempted/succeeded/failed with reasons, per-candidate
score breakdowns, and a decision rationale. `notes` is still populated (for
backward compatibility with the DiscoveryOut API contract and the existing
frontend) but is now generated FROM the structured report, not the other way
around.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class StrategyRecord(BaseModel):
    name: str
    status: str = "ok"            # ok | failed | skipped
    duration_seconds: float = 0.0
    evidence_count: int = 0
    error: str | None = None


class PhaseRecord(BaseModel):
    name: str
    duration_seconds: float = 0.0
    strategies: list[StrategyRecord] = Field(default_factory=list)


class SignalContributionOut(BaseModel):
    signal: str
    raw: float
    weight: float
    contribution: float


class VerificationReportOut(BaseModel):
    ok: bool
    vehicles_yielded: int = 0
    identity_rate: float = 0.0
    field_coverage: dict[str, float] = Field(default_factory=dict)
    total_count: int | None = None
    duration_seconds: float = 0.0
    error: str | None = None


class CandidateReport(BaseModel):
    key: str
    kind: str
    url: str
    score: float | None = None
    contributions: list[SignalContributionOut] = Field(default_factory=list)
    hard_blockers: list[str] = Field(default_factory=list)
    rejected_reason: str | None = None
    verification: VerificationReportOut | None = None


class DecisionReport(BaseModel):
    chosen_key: str | None = None
    rationale: str = ""


class DiscoveryReport(BaseModel):
    base_url: str
    duration_seconds: float = 0.0
    budget_seconds: float = 0.0
    # Per-stage wall times from StageTimer (homepage, candidate_nav, etc.)
    stage_timings: dict = Field(default_factory=dict)
    phases: list[PhaseRecord] = Field(default_factory=list)
    detected_cms: list[str] = Field(default_factory=list)
    antibot_events: list[str] = Field(default_factory=list)
    rendering_mode: str | None = None
    candidates: list[CandidateReport] = Field(default_factory=list)
    decision: DecisionReport | None = None
    evidence_counts: dict[str, int] = Field(default_factory=dict)
    suggestions: list[str] = Field(default_factory=list)
    # Prose lines, appended in the same places the pre-refactor code called
    # notes.append(...) — kept flat and ordered for backward compatibility.
    notes: list[str] = Field(default_factory=list)

    def add_note(self, note: str) -> None:
        if note and note not in self.notes:
            self.notes.append(note)

    def to_notes(self) -> list[str]:
        """Prose summary for DiscoveryOut.notes / the CLI. Currently a direct
        passthrough of the notes recorded during the run; Phase 3+ can enrich
        this with the top positive/negative signal contributions per candidate."""
        return list(self.notes)


# Maps StageTimer stage keys → human-readable phase names for diagnostics.
_STAGE_PHASE_LABELS: dict[str, str] = {
    "sitemap": "Candidate Discovery",
    "homepage": "Homepage",
    "candidate_nav": "Candidate Navigation",
    "candidate_scroll": "DOM Analysis",
    "pagination_dom": "Selector Detection",
    "pagination_json_scroll": "API Detection",
    "pagination_json_probe": "API Detection",
    "visual_grid": "Selector Detection",
    "config_generation": "Configuration Generation",
}


def build_phases_from_stage_timings(stage_timings: dict) -> list[PhaseRecord]:
    """Convert a StageTimer snapshot into ordered PhaseRecord rows."""
    stages = stage_timings.get("stages") or {}
    seen_labels: set[str] = set()
    phases: list[PhaseRecord] = []
    for stage_key, label in _STAGE_PHASE_LABELS.items():
        entry = stages.get(stage_key, {"seconds": 0.0, "count": 0})
        seconds = float(entry.get("seconds") or 0.0)
        count = int(entry.get("count") or 0)
        if seconds <= 0 and count <= 0:
            continue
        # Merge duplicate labels (e.g. two API Detection stages).
        if label in seen_labels:
            for phase in phases:
                if phase.name == label:
                    phase.duration_seconds = round(phase.duration_seconds + seconds, 3)
                    break
        else:
            seen_labels.add(label)
            phases.append(PhaseRecord(name=label, duration_seconds=round(seconds, 3)))
    return phases
