"""Regression tests for dealer_scraper.timing.StageTimer.

Pure Python, no DB — StageTimer is the shared instrumentation used by the
engine, the sync browser adapter, the json_api adapter, and (from Phase 2 on)
the async browser adapter's producer thread.

Run:  python -m tests.test_timing
"""
from __future__ import annotations

import threading
import time

from dealer_scraper.timing import STAGES, StageTimer


def test_stage_accumulates() -> None:
    t = StageTimer()
    with t.stage("nav"):
        time.sleep(0.01)
    with t.stage("nav"):
        time.sleep(0.01)
    snap = t.snapshot()
    assert snap["stages"]["nav"]["count"] == 2
    assert snap["stages"]["nav"]["seconds"] >= 0.02
    print("  stage() accumulates duration and count across calls OK")


def test_add_for_async_callers() -> None:
    t = StageTimer()
    t.add("wait", 1.5)
    t.add("wait", 0.5, count=2)
    snap = t.snapshot()
    assert snap["stages"]["wait"]["seconds"] == 2.0
    assert snap["stages"]["wait"]["count"] == 3
    print("  add() works for code that already has a duration (no context manager) OK")


def test_incr_counters() -> None:
    t = StageTimer()
    t.incr("pages")
    t.incr("pages")
    t.incr("requests_blocked", 40)
    snap = t.snapshot()
    assert snap["counters"] == {"pages": 2, "requests_blocked": 40}
    print("  incr() tracks free-form counters (pages, requests_blocked) OK")


def test_all_fixed_stages_present_even_at_zero() -> None:
    t = StageTimer()
    snap = t.snapshot()
    for name in STAGES:
        assert name in snap["stages"], name
    assert snap["stages"]["ai"] == {"seconds": 0.0, "count": 0}
    print("  every fixed stage (incl. 'ai') is present at 0/0 when untouched OK")


def test_thread_safety() -> None:
    t = StageTimer()

    def worker():
        for _ in range(2000):
            t.add("db", 0.0001)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    snap = t.snapshot()
    assert snap["stages"]["db"]["count"] == 8000
    print("  concurrent add() from multiple threads never loses an update OK")


def test_report_text_shape() -> None:
    t = StageTimer()
    with t.stage("nav"):
        time.sleep(0.01)
    t.incr("pages", 3)
    text = t.report_text(header={"Dealer": "Acme (#1)"}, total_seconds=1.0,
                          vehicles=10, pages=3)
    assert "Dealer: Acme (#1)" in text
    assert "Throughput: 10.0 veh/s" in text
    assert "nav" in text and "other" in text
    assert "pages: 3" in text
    print("  report_text() renders header, throughput, per-stage rows, and counters OK")


def main() -> None:
    test_stage_accumulates()
    test_add_for_async_callers()
    test_incr_counters()
    test_all_fixed_stages_present_even_at_zero()
    test_thread_safety()
    test_report_text_shape()
    print("TIMING TESTS PASSED")


if __name__ == "__main__":
    main()
