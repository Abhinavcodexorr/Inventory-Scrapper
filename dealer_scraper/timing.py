"""Per-stage latency instrumentation shared by the engine and every adapter.

One StageTimer is created per scrape/discovery run and threaded through to
whatever code does the work (sync engine loop, sync adapter, or the async
adapter's producer thread/coroutines in a later phase). `stage()` is a
context manager for synchronous call sites; `add()` is for code that already
has a duration (e.g. an awaited coroutine) and can't wrap it in a `with`.

Thread-safe: the async browser adapter's producer thread and the engine's
consumer thread both write to the same StageTimer instance concurrently.
"""
from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from typing import Iterator

# Fixed so every report has the same shape regardless of which stages a given
# adapter actually touches — a zero-time "ai" row is a deliberate signal, not
# an absence: it proves the LLM tier was on the hook and never fired.
STAGES: tuple[str, ...] = (
    "browser_launch", "warmup", "nav", "wait", "extract", "parse", "db", "ai",
)


class StageTimer:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._seconds: dict[str, float] = {s: 0.0 for s in STAGES}
        self._counts: dict[str, int] = {s: 0 for s in STAGES}
        self._counters: dict[str, int] = {}
        self._start = time.perf_counter()

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        t0 = time.perf_counter()
        try:
            yield
        finally:
            self.add(name, time.perf_counter() - t0)

    def add(self, name: str, seconds: float, count: int = 1) -> None:
        with self._lock:
            self._seconds[name] = self._seconds.get(name, 0.0) + max(seconds, 0.0)
            self._counts[name] = self._counts.get(name, 0) + count

    def incr(self, counter: str, n: int = 1) -> None:
        with self._lock:
            self._counters[counter] = self._counters.get(counter, 0) + n

    def elapsed(self) -> float:
        return time.perf_counter() - self._start

    def snapshot(self) -> dict:
        with self._lock:
            stages = {
                name: {"seconds": round(self._seconds[name], 3), "count": self._counts[name]}
                for name in self._seconds
                if self._counts[name] or self._seconds[name]
            }
            # Every fixed stage still appears (even at 0/0) so downstream
            # readers can rely on the key existing — most visibly "ai".
            for name in STAGES:
                stages.setdefault(name, {"seconds": 0.0, "count": 0})
            return {
                "stages": stages,
                "counters": dict(self._counters),
                "elapsed_seconds": round(self.elapsed(), 3),
            }

    def report_text(self, *, header: dict, total_seconds: float | None = None,
                     vehicles: int | None = None, pages: int | None = None) -> str:
        snap = self.snapshot()
        total = total_seconds if total_seconds is not None else snap["elapsed_seconds"]
        stages = snap["stages"]
        accounted = sum(v["seconds"] for k, v in stages.items() if k != "other")
        other = max(total - accounted, 0.0)

        lines = ["=" * 48]
        head_bits = "   ".join(f"{k}: {v}" for k, v in header.items())
        lines.append(head_bits)
        summary_bits = [f"Total: {total:.1f}s"]
        if vehicles is not None:
            summary_bits.append(f"Vehicles: {vehicles}")
        if pages is not None:
            summary_bits.append(f"Pages: {pages}")
        if vehicles and total > 0:
            summary_bits.append(f"Throughput: {vehicles / total:.1f} veh/s")
        lines.append("   ".join(summary_bits))
        lines.append("-" * 48)
        lines.append(f"{'Stage':<16}{'Time':>10}{'%':>8}{'Count':>10}")
        display_order = list(STAGES) + [k for k in stages if k not in STAGES]
        for name in display_order:
            v = stages.get(name, {"seconds": 0.0, "count": 0})
            pct = (v["seconds"] / total * 100) if total > 0 else 0.0
            lines.append(f"{name:<16}{v['seconds']:>9.1f}s{pct:>7.1f}%{v['count']:>10}")
        pct_other = (other / total * 100) if total > 0 else 0.0
        lines.append(f"{'other':<16}{other:>9.1f}s{pct_other:>7.1f}%{'-':>10}")
        lines.append("-" * 48)
        if snap["counters"]:
            counter_bits = "   ".join(f"{k}: {v}" for k, v in snap["counters"].items())
            lines.append(counter_bits)
        lines.append("=" * 48)
        return "\n".join(lines)
