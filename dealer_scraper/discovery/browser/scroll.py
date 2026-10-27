"""Progressive scroll with growth detection: wheel-scroll the page, checking
after each round whether a caller-supplied probe reports new items, and
stopping once growth stalls for several consecutive rounds — instead of
scrolling a fixed number of rounds regardless of whether anything is still
loading (the pre-refactor pattern: a flat `for _ in range(N)` loop everywhere
scrolling was used).

Discovery uses `reveal_lazy_cards()` for a bounded scroll burst (expose
lazy-loaded cards on the first page only — never walk the full inventory).
"""
from __future__ import annotations

from typing import Callable

from ..heuristics import DEFAULT_HEURISTICS, HeuristicsConfig
from .waits import wait_for_stable


def reveal_lazy_cards(
    page,
    *,
    rounds: int = 3,
    scroll_amount: int = 4000,
    heuristics: HeuristicsConfig = DEFAULT_HEURISTICS,
) -> int:
    """Scroll a listing page a few times to expose lazy-loaded cards, then wait
    once for the DOM to settle. Returns the number of scroll rounds performed.

    Discovery-only: never scrolls the whole inventory or waits after every
    scroll — that belongs to the scraper.
    """
    done = 0
    for _ in range(rounds):
        try:
            page.mouse.wheel(0, scroll_amount)
            done += 1
        except Exception:
            break
    if done:
        try:
            wait_for_stable(page, heuristics)
        except Exception:
            pass
    return done


def progressive_scroll(
    page,
    collect: Callable[[], set],
    *,
    max_rounds: int = 40,
    stagnant_rounds_to_stop: int = 3,
    scroll_amount: int = 20000,
    heuristics: HeuristicsConfig = DEFAULT_HEURISTICS,
) -> dict:
    """Scroll `page` repeatedly, calling `collect()` after each scroll to get
    the current set of "seen" items (e.g. detail-link hrefs). Stops once
    `stagnant_rounds_to_stop` consecutive rounds add nothing new, or
    `max_rounds` is reached — whichever comes first.

    Returns {"rounds": int, "final_set": set, "stagnant": int}. Never raises —
    a scroll/collect failure just ends the loop with whatever was seen so far.
    """
    seen = set(collect())
    stagnant = 0
    rounds_done = 0
    for round_num in range(1, max_rounds + 1):
        rounds_done = round_num
        try:
            page.mouse.wheel(0, scroll_amount)
        except Exception:
            break
        wait_for_stable(page, heuristics)
        try:
            current = set(collect())
        except Exception:
            break
        new = current - seen
        seen |= current
        if new:
            stagnant = 0
        else:
            stagnant += 1
            if stagnant >= stagnant_rounds_to_stop:
                break
    return {"rounds": rounds_done, "final_set": seen, "stagnant": stagnant}
