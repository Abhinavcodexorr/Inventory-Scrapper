"""DOM pagination detection: infer HOW a listing likely paginates from DOM
signals without exhaustively walking pages (that belongs to the scraper).

Discovery identifies pagination type and emits scrape_config hints; the scraper
proves pagination works by collecting the full inventory.
"""
from __future__ import annotations

import re

from ..browser.scroll import reveal_lazy_cards
from ..browser.waits import wait_for_stable
from ..heuristics import DEFAULT_HEURISTICS, HeuristicsConfig

# Runs in the browser: the set of vehicle-detail link paths currently in the DOM.
_DETAIL_LINKS_JS = r"""
(detailSegments) => {
  const out = new Set();
  for (const a of document.querySelectorAll('a[href]')) {
    const href = a.getAttribute('href') || '';
    if (!href || href.startsWith('#') || href.startsWith('mailto:')) continue;
    let path;
    try { path = new URL(href, location.origin).pathname; } catch (e) { continue; }
    const parts = path.split('/').filter(Boolean);
    if (parts.length < 2) continue;
    for (let i = 0; i < parts.length - 1; i++) {
      const seg = parts[i].toLowerCase();
      if (!detailSegments.includes(seg)) continue;
      const idPart = parts[i + 1];
      if (!/^\d+$/.test(idPart) && !/[-_]\d{3,}$/.test(idPart) && !/^[a-z0-9-]{8,}$/i.test(idPart)) continue;
      out.add(path);
      break;
    }
  }
  return [...out];
}
"""

# schema.org Vehicle nodes currently in the DOM. Counted AFTER the grid has
# rendered: on a client-rendered listing the JSON-LD is injected with the cards,
# so probing at first paint sees zero and wrongly falls back to text parsing.
_JSONLD_COUNT_JS = r"""
() => {
  let n = 0;
  for (const s of document.querySelectorAll('script[type="application/ld+json"]')) {
    let j; try { j = JSON.parse(s.textContent); } catch (e) { continue; }
    const arr = Array.isArray(j) ? j : (j['@graph'] || [j]);
    for (const node of arr) {
      const t = node && node['@type'];
      if (t && /Vehicle|Car/i.test(String(t))) n++;
    }
  }
  return n;
}
"""


def _wait_for_detail_links(page, detail_segments, cap_ms: int, poll_ms: int = 250) -> set:
    """Poll until vehicle-detail links exist, or cap_ms elapses.

    Everything downstream — pagination verification and the JSON-LD count —
    is meaningless until the grid is actually on the page. A plain settle wait
    is not enough on a client-rendered listing, and a selector wait is satisfied
    by the site's own nav links, which share the listing path.
    """
    import time as _time

    deadline = _time.monotonic() + cap_ms / 1000
    while True:
        try:
            links = set(page.evaluate(_DETAIL_LINKS_JS, list(detail_segments)))
        except Exception:
            links = set()
        if links or _time.monotonic() >= deadline:
            return links
        _time.sleep(poll_ms / 1000)


def verify_url_pagination(page, listing_url: str, base_links: set, timeout_ms: int,
                          detail_segments, param: str = "page") -> dict:
    """Prove ?page=2 works by fetching it and comparing vehicles against page 1.

    Inference alone cannot see this. A React/SPA listing renders pagination as
    buttons, not `<a href="?page=2">`, so hint-scanning concludes "scroll" — yet
    ?page=2 often works perfectly (clutch.ca does). One extra navigation settles
    it, and being wrong here costs the whole inventory: the scraper collects page
    one and stops.

    Verified only when page 2 returns vehicles that are NOT page 1's — a site
    that ignores the parameter serves identical content, which must not pass.
    """
    sep = "&" if "?" in listing_url else "?"
    trial = f"{listing_url}{sep}{param}=2"
    try:
        page.goto(trial, wait_until="domcontentloaded", timeout=timeout_ms)
    except Exception as exc:
        return {"verified": False, "reason": f"page 2 did not load: {type(exc).__name__}"}

    links = _wait_for_detail_links(page, detail_segments, cap_ms=min(timeout_ms, 15000))
    if not links:
        return {"verified": False, "reason": "page 2 rendered no vehicles"}

    fresh = links - base_links
    if not fresh:
        return {"verified": False,
                "reason": "page 2 returned the same vehicles as page 1 "
                          "(the site ignores the page parameter)"}
    return {
        "verified": True,
        "page_url": f"{listing_url}{sep}{param}={{page}}",
        "observed": len(links),
        "reason": f"page 2 returned {len(fresh)} vehicles not present on page 1",
    }


def count_jsonld_vehicles(page) -> int:
    """schema.org Vehicle nodes on the current page (0 on any failure)."""
    try:
        return int(page.evaluate(_JSONLD_COUNT_JS))
    except Exception:
        return 0


# Scan the DOM for numbered pagination links (page=2, /page/2, etc.).
_DETECT_PAGINATION_HINTS_JS = r"""
() => {
  const hints = { urlPaging: false, pageUrlTemplate: null };
  for (const a of document.querySelectorAll('a[href]')) {
    const href = a.getAttribute('href') || '';
    if (!href) continue;
    if (/[?&]page=\d+/i.test(href) || /\/page\/\d+/i.test(href)) {
      hints.urlPaging = true;
      const m = href.match(/([?&]page=)\d+/i);
      if (m) {
        hints.pageUrlTemplate = href.replace(/\d+$/, '{page}').replace(/page=\d+/i, 'page={page}');
      }
      break;
    }
  }
  return hints;
}
"""

# Runs in the browser: the most promising load-more / next-page control, with a
# selector the BrowserAdapter can reuse at scrape time (next_selector config).
_FIND_MORE_BUTTON_JS = r"""
() => {
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    if (!r.width && !r.height) return false;
    const st = getComputedStyle(el);
    return st.display !== 'none' && st.visibility !== 'hidden';
  };
  const esc = (s) => (window.CSS && CSS.escape) ? CSS.escape(s) : s;
  const candidates = [];
  for (const el of document.querySelectorAll('a,button')) {
    const label = ((el.textContent || '') + ' ' + (el.getAttribute('aria-label') || '')).trim();
    if (!label || label.length > 60 || !visible(el)) continue;
    let kind = null;
    if (/load\s*more|show\s*more|view\s*more|see\s*more|more\s*(vehicles|results|cars|listings)/i.test(label)) kind = 'more';
    else if (/^next\b|next\s*page|›|»/i.test(label)) kind = 'next';
    if (!kind) continue;
    let sel = null;
    if (el.id) {
      const s = '#' + esc(el.id);
      if (document.querySelectorAll(s).length === 1) sel = s;
    }
    if (!sel && el.classList.length) {
      const s = el.tagName.toLowerCase() + [...el.classList].map((c) => '.' + esc(c)).join('');
      if (document.querySelectorAll(s).length === 1) sel = s;
    }
    if (!sel) {
      const t = (el.textContent || '').trim().replace(/"/g, '\\"').slice(0, 40);
      if (t) sel = el.tagName.toLowerCase() + ':has-text("' + t + '")';
    }
    if (sel) candidates.push({ selector: sel, kind, label: label.slice(0, 40) });
  }
  candidates.sort((a, b) => (a.kind === 'more' ? 0 : 1) - (b.kind === 'more' ? 0 : 1));
  return candidates[0] || null;
}
"""


def _build_detail_id_regex(seg: str, sample_paths: list[str]) -> str:
    """Detail-URL id_regex for the browser-fallback adapter."""
    tails = []
    for p in sample_paths:
        parts = [seg_ for seg_ in p.split("/") if seg_]
        if len(parts) >= 3:
            tails.append(parts[-1])
    if tails and len(tails) == len(sample_paths) and all(re.fullmatch(r"\d+", t) for t in tails):
        # One slug before the id is the common shape; deeper ones
        # (/vehicles/2015/honda/civic/mississauga/on/12345678) need any depth.
        for numeric in (rf"/{seg}/[\w-]+/(\d+)", rf"/{seg}/(?:[\w-]+/)+(\d+)"):
            if _distinct_ids(numeric, sample_paths):
                return numeric
    pattern = rf"/{seg}/([\w-]+)"
    # The id must tell the sampled vehicles apart. On driftmotors.ca every
    # detail URL is /cars/used/<slug>, so the segment after /cars/ is "used"
    # for all of them: every row got external_id "used", the engine matched
    # them all to one car, and a sync reported "88 rows, 1 distinct". When
    # that happens, take the last path segment — the per-vehicle part.
    if len(sample_paths) >= 2 and not _distinct_ids(pattern, sample_paths):
        last = rf"/{seg}/(?:[\w-]+/)*([\w-]+)/?(?=[?#]|$)"
        if _distinct_ids(last, sample_paths):
            return last
    return pattern


def _distinct_ids(pattern: str, paths: list[str]) -> bool:
    ids = []
    for p in paths:
        m = re.search(pattern, p)
        if not m:
            return False
        ids.append(m.group(1))
    return len(set(ids)) == len(ids)


def _detect_dom_pagination(page, listing_url: str, timeout_ms: int,
                            heuristics: HeuristicsConfig = DEFAULT_HEURISTICS) -> dict:
    """Infer likely pagination mechanism from DOM signals on the first page.

    Does NOT navigate to page 2, click load-more repeatedly, or scroll through
    the inventory — those are scraper responsibilities.

    Returns {"mode", "page_url", "next_selector", "verified", "observed", "notes"}
    where verified=False means inferred (not empirically walked).
    """
    notes: list[str] = []

    def links() -> set:
        try:
            return set(page.evaluate(_DETAIL_LINKS_JS, list(heuristics.detail_segments)))
        except Exception:
            return set()

    def goto(url: str) -> bool:
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            wait_for_stable(page, heuristics)
            return True
        except Exception:
            return False

    if not goto(listing_url):
        return {"mode": "scroll", "page_url": None, "next_selector": None,
                "verified": False, "observed": 0,
                "notes": ["Could not reload the listing page for pagination detection."]}

    base = links()

    # URL pagination hints from pagination links in the DOM (no ?page=2 navigation).
    try:
        hints = page.evaluate(_DETECT_PAGINATION_HINTS_JS) or {}
    except Exception:
        hints = {}
    if hints.get("urlPaging"):
        page_url = hints.get("pageUrlTemplate")
        if not page_url:
            page_url = f"{listing_url}{'&' if '?' in listing_url else '?'}page={{page}}"
        return {"mode": "url", "page_url": page_url, "next_selector": None,
                "verified": False, "observed": len(base),
                "notes": notes + ["URL pagination inferred from page-number links in the DOM."]}

    # No page-number anchors — but that is exactly what a client-rendered listing
    # looks like even when ?page=N works (its pager is buttons, not links). Prove
    # it by fetching page 2 rather than falling through to scroll and silently
    # collecting only the first page.
    if base:
        trial = verify_url_pagination(page, listing_url, base, timeout_ms,
                                      heuristics.detail_segments)
        if trial.get("verified"):
            goto(listing_url)  # leave the caller on page 1
            return {"mode": "url", "page_url": trial["page_url"], "next_selector": None,
                    "verified": True, "observed": trial.get("observed", len(base)),
                    "notes": notes + [
                        f"URL pagination confirmed by fetching page 2 — {trial['reason']}."]}
        notes.append(f"?page=2 did not paginate: {trial.get('reason', 'unknown')}.")
        goto(listing_url)

    # Load-more / next button presence (not clicked — scraper verifies it works).
    try:
        btn = page.evaluate(_FIND_MORE_BUTTON_JS)
    except Exception:
        btn = None
    if btn and btn.get("selector"):
        return {"mode": "click", "page_url": None, "next_selector": btn["selector"],
                "verified": False, "observed": len(base),
                "notes": notes + [
                    f"'{btn.get('label', '?')}' control detected "
                    f"(selector: {btn['selector']}) — scraper will verify at sync time."]}

    # Minimal scroll to expose lazy-loaded cards; if count grows, infinite scroll is likely.
    before = len(base)
    reveal_lazy_cards(page, heuristics=heuristics)
    after_links = links()
    observed = len(after_links)
    if observed > before:
        notes.append(
            f"Lazy-loaded cards detected after scroll ({before} → {observed} detail links) — "
            "infinite scroll inferred.")
        return {"mode": "scroll", "page_url": None, "next_selector": None,
                "verified": False, "observed": observed, "notes": notes}

    notes.append(
        "No pagination hints found — defaulting to scroll mode; scraper will "
        "determine whether more inventory loads.")
    return {"mode": "scroll", "page_url": None, "next_selector": None,
            "verified": False, "observed": observed or len(base), "notes": notes}


def _verify_dom_pagination(page, listing_url: str, timeout_ms: int,
                            heuristics: HeuristicsConfig = DEFAULT_HEURISTICS) -> dict:
    """Backward-compatible alias — discovery now uses detection-only pagination."""
    return _detect_dom_pagination(page, listing_url, timeout_ms, heuristics)
