"""Unit tests for DiscoveryCoordinator.run()'s own bookkeeping: it must never
raise, must distinguish a cancelled run from a crashed one, and must always
attach a usable DiscoveryReport — regardless of what happens inside
_run_browser_phase (mocked here so no Playwright/browser is needed).

Run:  python -m tests.test_discovery_coordinator
"""
from __future__ import annotations

from dealer_scraper.discovery.context import DiscoveryCancelled
from dealer_scraper.discovery.coordinator import DiscoveryCoordinator


def test_run_never_raises_on_internal_exception():
    coordinator = DiscoveryCoordinator("https://example.com")

    def _boom(self, ctx, result):
        raise RuntimeError("simulated failure")

    coordinator._run_browser_phase = _boom.__get__(coordinator)
    result = coordinator.run()

    assert result.ok is False
    assert result.confidence == "failed"
    assert "custom scraper build" in result.summary
    assert any("simulated failure" in n for n in result.notes)
    assert result.report is not None
    assert result.report.duration_seconds >= 0


def test_run_distinguishes_cancellation_from_failure():
    coordinator = DiscoveryCoordinator("https://example.com")

    def _cancel(self, ctx, result):
        raise DiscoveryCancelled("stop requested")

    coordinator._run_browser_phase = _cancel.__get__(coordinator)
    result = coordinator.run()

    assert result.summary == "Discovery was cancelled."
    assert "Discovery cancelled by request." in result.notes
    assert result.report.decision is not None
    assert result.report.decision.rationale == "cancelled"


def test_progress_and_check_cancel_callbacks_are_invoked():
    calls: list[tuple[str, int, int]] = []
    cancel_checks = {"count": 0}

    def _progress(phase: str, done: int, total: int) -> None:
        calls.append((phase, done, total))

    def _check_cancel() -> None:
        cancel_checks["count"] += 1

    coordinator = DiscoveryCoordinator(
        "https://example.com", progress=_progress, check_cancel=_check_cancel,
    )

    def _touch(self, ctx, result):
        ctx.check_cancel()
        self.progress("explore", 1, 4)
        result.ok = True
        result.confidence = "high"
        result.verdict = "ready"

    coordinator._run_browser_phase = _touch.__get__(coordinator)
    result = coordinator.run()

    assert cancel_checks["count"] >= 1
    assert ("explore", 1, 4) in calls
    assert ("done", 4, 4) in calls  # run() always reports terminal progress
    assert result.ok is True


def test_report_evidence_counts_reflect_recorded_evidence():
    from dealer_scraper.discovery.evidence import Evidence, EvidenceKind

    coordinator = DiscoveryCoordinator("https://example.com")

    def _emit(self, ctx, result):
        ctx.evidence.add(Evidence(EvidenceKind.API_RESPONSE, "api_sniff", "api:x", weight=1.0))
        ctx.evidence.add(Evidence(EvidenceKind.API_RESPONSE, "api_sniff", "api:y", weight=1.0))
        ctx.evidence.add(Evidence(EvidenceKind.ANTIBOT_SIGNAL, "explore_homepage", "", weight=1.0))

    coordinator._run_browser_phase = _emit.__get__(coordinator)
    result = coordinator.run()

    assert result.report.evidence_counts.get("api_response") == 2
    assert result.report.evidence_counts.get("antibot_signal") == 1


def test_build_phases_from_stage_timings_merges_and_orders():
    from dealer_scraper.discovery.diagnostics import PhaseRecord, build_phases_from_stage_timings

    snapshot = {
        "stages": {
            "homepage": {"seconds": 2.5, "count": 1},
            "candidate_nav": {"seconds": 4.0, "count": 3},
            "pagination_json_scroll": {"seconds": 1.2, "count": 1},
            "pagination_json_probe": {"seconds": 0.3, "count": 1},
            "config_generation": {"seconds": 0.1, "count": 1},
        },
        "counters": {"navigations": 4},
        "elapsed_seconds": 8.1,
    }
    phases = build_phases_from_stage_timings(snapshot)
    assert phases[0] == PhaseRecord(name="Homepage", duration_seconds=2.5)
    assert any(p.name == "Candidate Navigation" and p.duration_seconds == 4.0 for p in phases)
    api_phase = next(p for p in phases if p.name == "API Detection")
    assert api_phase.duration_seconds == 1.5


def main() -> None:
    test_run_never_raises_on_internal_exception()
    test_run_distinguishes_cancellation_from_failure()
    test_progress_and_check_cancel_callbacks_are_invoked()
    test_report_evidence_counts_reflect_recorded_evidence()
    test_build_phases_from_stage_timings_merges_and_orders()
    print("DISCOVERY COORDINATOR TEST PASSED")


if __name__ == "__main__":
    main()
