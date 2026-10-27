"""Intelligent waiting: network-idle + DOM-mutation-stability, replacing the
fixed `page.wait_for_timeout(N)` sleeps used throughout the pre-refactor
discovery flow. A fast static page settles in well under a second; a heavy
SPA might need several — a fixed sleep either wastes time on the former or
under-waits the latter. The short sleeps inside the poll loop below are a
bounded polling INTERVAL, not the thing being replaced — the loop exits the
moment the page stops mutating instead of always waiting the full budget.
"""
from __future__ import annotations

import time

from ..heuristics import DEFAULT_HEURISTICS, HeuristicsConfig

# Installs a MutationObserver once per page (idempotent) and returns the running
# mutation count. Guard checks for undefined (not just falsy) to avoid re-attaching
# when count is legitimately 0 on a static page.
_STABILITY_JS = r"""
() => {
  if (window.__discoveryMutationObserver === undefined) {
    window.__discoveryMutationCount = 0;
    const observer = new MutationObserver((mutations) => {
      window.__discoveryMutationCount += mutations.length;
    });
    observer.observe(document.documentElement, {
      childList: true, subtree: true, attributes: true, characterData: true,
    });
    window.__discoveryMutationObserver = observer;
  }
  return window.__discoveryMutationCount;
}
"""


def wait_for_stable(page, heuristics: HeuristicsConfig = DEFAULT_HEURISTICS) -> dict:
    """Wait for `page` to settle after a navigation/interaction.

    First tries Playwright's own network-idle state (bounded, non-fatal on
    timeout — SPAs with polling/websockets never go idle and that is
    expected, not an error). Then polls a MutationObserver-backed counter
    until it stops changing for `dom_stability_quiet_rounds` consecutive
    checks, or `dom_stability_max_ms` elapses.

    Carousel pages that auto-animate at a steady rate (e.g., photo sliders)
    are tolerated: if the mutation rate is constant (low delta between polls),
    treat it as "settled" for practical purposes. This prevents photo carousels
    from blocking indefinitely.

    Returns {"networkidle": bool, "rounds": int, "elapsed_ms": float} for
    diagnostics. Never raises — any failure just means the wait ends early.
    """
    networkidle = False
    try:
        page.wait_for_load_state("networkidle", timeout=heuristics.networkidle_timeout_ms)
        networkidle = True
    except Exception:
        pass

    quiet_rounds = 0
    rounds = 0
    last_count = None
    last_delta = None
    steady_rate_rounds = 0
    start = time.monotonic()
    while (time.monotonic() - start) * 1000 < heuristics.dom_stability_max_ms:
        try:
            count = page.evaluate(_STABILITY_JS)
        except Exception:
            break
        rounds += 1
        if last_count is not None:
            delta = count - last_count
            if delta == 0:
                quiet_rounds += 1
                if quiet_rounds >= heuristics.dom_stability_quiet_rounds:
                    break
            elif last_delta is not None and delta == last_delta:
                steady_rate_rounds += 1
                if steady_rate_rounds >= heuristics.dom_stability_quiet_rounds:
                    break
            else:
                quiet_rounds = 0
                steady_rate_rounds = 0
            last_delta = delta
        else:
            quiet_rounds = 0
        last_count = count
        try:
            page.wait_for_timeout(heuristics.dom_stability_poll_ms)
        except Exception:
            break

    elapsed_ms = (time.monotonic() - start) * 1000
    return {"networkidle": networkidle, "rounds": rounds, "elapsed_ms": round(elapsed_ms, 1)}
