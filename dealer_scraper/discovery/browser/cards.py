"""Find the repeating card container that holds a vehicle's details.

`extract: "dom"` reads a vehicle's year/make/model/price out of the text of the
element wrapping its detail link. Which element that is has to come from the
page: the anchor itself is usually a bare image or title link, and the fields sit
in a sibling subtree.

Without a `card_selector` the scraper falls back to climbing a fixed number of
ancestors and stopping at the first one with more than a little text. That guess
fails quietly and completely — on cardora.ca every anchor's nearest chatty
ancestor was a `div.vehicle-image` containing only the word "SOLD", so a run
collected 528 vehicles and populated not one field, while reporting success.

The selector is chosen by looking for what a card actually *is*: the shallowest
ancestor whose text carries the things a listing card always shows — a model
year, and a price or an odometer reading.
"""
from __future__ import annotations

import re
from collections import Counter

# Runs in the page. For each vehicle anchor, walk up and report the first
# ancestor whose text looks like a whole listing card, plus a selector for it.
FIND_CARDS_JS = r"""
({anchorSel, idPattern, maxDepth}) => {
  const idRe = idPattern ? new RegExp(idPattern) : null;
  const YEAR = /\b(19|20)\d{2}\b/;
  const MONEY = /\$\s?[\d,]{3,}/;
  const ODO = /[\d,]{3,}\s*(?:km|mi\b|miles)/i;

  // A card is the smallest thing that shows a year AND a price or odometer.
  const looksLikeCard = (t) => YEAR.test(t) && (MONEY.test(t) || ODO.test(t));

  // Stable, structural identity for an element: tag plus the class names that
  // are plausibly layout/component names rather than per-item state.
  const identify = (el) => {
    const tag = el.tagName.toLowerCase();
    const raw = (el.getAttribute('class') || '').trim();
    if (!raw) return null;
    const classes = raw.split(/\s+/).filter((c) =>
      c.length > 1 && c.length < 40 &&
      !/^(is|has|js)-/.test(c) &&
      !/^(active|selected|open|sold|hidden|show|hide)$/i.test(c) &&
      !/\d{3,}/.test(c) &&            // hashed/generated names
      !/^[a-z]{1,2}$/i.test(c)
    );
    if (!classes.length) return null;
    return {
      tag,
      // Most specific first; the caller picks whichever is consistent.
      selectors: [
        tag + '.' + classes.join('.'),
        ...classes.map((c) => tag + '.' + c),
      ],
    };
  };

  const seen = new Set();
  const found = [];
  for (const a of document.querySelectorAll(anchorSel)) {
    const href = a.getAttribute('href') || '';
    if (!href || seen.has(href)) continue;
    if (idRe && !idRe.test(href)) continue;   // nav links, not vehicles
    seen.add(href);

    let node = a;
    for (let i = 0; i < maxDepth && node.parentElement; i++) {
      node = node.parentElement;
      const text = (node.textContent || '').replace(/\s+/g, ' ').trim();
      if (!looksLikeCard(text)) continue;
      // Guard against overshooting into the grid container, which also
      // satisfies looksLikeCard but holds every vehicle at once. Count DISTINCT
      // vehicles, not links: one card routinely links the same vehicle three
      // times (image, title, "view details"), so an anchor count rejects every
      // real card.
      const hrefs = new Set();
      for (const inner of node.querySelectorAll(anchorSel)) {
        const h = inner.getAttribute('href') || '';
        if (h && (!idRe || idRe.test(h))) hrefs.add(h);
      }
      const id = identify(node);
      found.push({
        depth: i + 1,
        vehicles: hrefs.size,
        selectors: id ? id.selectors : [],
        textLen: text.length,
      });
      break;
    }
    if (found.length >= 25) break;
  }
  return found;
}
"""

_MIN_SAMPLES = 3


def choose_card_selector(samples: list[dict]) -> str | None:
    """Pick the selector that identifies one card, from per-anchor observations.

    Requires the selector to be seen for most sampled vehicles (a card layout is
    by definition repeated) and to wrap exactly one vehicle — an ancestor holding
    several is the results grid, not a card, and using it would hand every
    vehicle the whole page's text.
    """
    if len(samples) < _MIN_SAMPLES:
        return None

    single = [s for s in samples if s.get("vehicles", 0) <= 1]
    if len(single) < _MIN_SAMPLES:
        return None

    counts: Counter[str] = Counter()
    for sample in single:
        # Count each selector once per card, not once per occurrence.
        for selector in dict.fromkeys(sample.get("selectors") or []):
            counts[selector] += 1

    quorum = max(_MIN_SAMPLES, int(len(single) * 0.6))
    winners = [(sel, n) for sel, n in counts.items() if n >= quorum]
    if not winners:
        return None
    # Best coverage first, then the SHORTER selector. Specificity is a trap here:
    # the sampled cards come off the top of the listing and are often all the same
    # kind, so a compound selector happily absorbs per-item state
    # ("div.vehicle.list-view.new-vehicle.publish") and then fails to match every
    # used vehicle further down the page. The class shared by all sampled cards is
    # the one that describes the card, not its state.
    winners.sort(key=lambda t: (-t[1], len(t[0])))
    return winners[0][0]


def detect_card_selector(page, anchor_selector: str, id_regex: str | None = None,
                          max_depth: int = 8) -> str | None:
    """Best-effort `card_selector` for a rendered listing page.

    Returns None rather than a guess when the page does not show a consistent
    card shape — the scraper's own fallback is no worse, and a wrong selector is.
    """
    if not anchor_selector:
        return None
    try:
        samples = page.evaluate(FIND_CARDS_JS, {
            "anchorSel": anchor_selector,
            "idPattern": _js_safe_pattern(id_regex),
            "maxDepth": max_depth,
        })
    except Exception:
        return None
    return choose_card_selector(samples or [])


def _js_safe_pattern(pattern: str | None) -> str | None:
    """Python regexes that JavaScript's RegExp cannot compile are dropped rather
    than allowed to throw inside the page."""
    if not pattern:
        return None
    # Python-only constructs that would raise in JS.
    if re.search(r"\(\?P<|\(\?P=|\(\?#", pattern):
        return None
    return pattern
