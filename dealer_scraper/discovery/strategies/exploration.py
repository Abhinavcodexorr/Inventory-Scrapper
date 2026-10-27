"""Homepage probing and candidate inventory-URL discovery. Ported from
dealer_scraper/discovery.py (pre-refactor); Phase 2 extends this with a
multilingual nav-hint lexicon and sitemap-driven candidates.
"""
from __future__ import annotations

from urllib.parse import urljoin, urlparse, urlsplit

from ..heuristics import DEFAULT_HEURISTICS, HeuristicsConfig

# Runs in the browser: reports what this page looks like to a scraper.
PROBE_JS = r"""
(detailSegments) => {
  // --- schema.org Vehicle nodes (the reliable, structured path) ---
  let jsonld = 0;
  for (const s of document.querySelectorAll('script[type="application/ld+json"]')) {
    let j; try { j = JSON.parse(s.textContent); } catch (e) { continue; }
    const arr = Array.isArray(j) ? j : (j['@graph'] || [j]);
    for (const n of arr) {
      const t = n && n['@type'];
      if (t && /Vehicle|Car/i.test(String(t))) jsonld++;
    }
  }

  // --- repeated vehicle-detail links: /<segment>/<id> ---
  const groups = {};
  for (const a of document.querySelectorAll('a[href]')) {
    let href = a.getAttribute('href') || '';
    if (!href || href.startsWith('#') || href.startsWith('mailto:')) continue;
    let path;
    try { path = new URL(href, location.origin).pathname; } catch (e) { continue; }
    const parts = path.split('/').filter(Boolean);
    if (parts.length < 2) continue;
    for (let i = 0; i < parts.length - 1; i++) {
      const seg = parts[i].toLowerCase();
      if (!detailSegments.includes(seg)) continue;
      const idPart = parts[i + 1];
      // an id looks like digits, or a slug ending in digits (…-12345)
      if (!/^\d+$/.test(idPart) && !/[-_]\d{3,}$/.test(idPart) && !/^[a-z0-9-]{8,}$/i.test(idPart)) continue;
      groups[seg] = groups[seg] || new Set();
      groups[seg].add(path);
      break;
    }
  }
  let bestSeg = null, bestCount = 0;
  for (const [seg, set] of Object.entries(groups)) {
    if (set.size > bestCount) { bestSeg = seg; bestCount = set.size; }
  }
  const bestSample = bestSeg ? [...groups[bestSeg]].slice(0, 8) : [];

  // --- a "N results/vehicles" count anywhere on the page ---
  const txt = (document.body.innerText || '');
  const m = txt.match(/([\d,]{2,})\s*(?:results|vehicles|cars|listings|matches)/i);

  // --- pagination signals ---
  const hasPageLink = !!document.querySelector("a[href*='page='], a[href*='/page/']");
  const nextBtn = [...document.querySelectorAll('a,button')].some((e) =>
    /next|›|»/i.test(((e.textContent || '') + ' ' + (e.getAttribute('aria-label') || '')).trim()));
  const loadMore = [...document.querySelectorAll('a,button')].some((e) =>
    /load more|show more|view more/i.test(e.textContent || ''));

  // --- inventory-looking nav links (used when this page isn't the listing) ---
  const navLinks = [...document.querySelectorAll('a[href]')]
    .map((a) => ({ href: a.getAttribute('href') || '', text: (a.textContent || '').trim().slice(0, 40) }))
    .filter((l) => l.href && !l.href.startsWith('#'))
    .slice(0, 400);

  return {
    jsonld, bestSeg, bestCount, bestSample,
    reportedTotal: m ? Number(m[1].replace(/,/g, '')) : null,
    hasPageLink, nextBtn, loadMore, navLinks,
  };
}
"""


def _same_host(url: str, host: str) -> bool:
    try:
        h = urlparse(url).netloc.lower().split(":")[0]
    except Exception:
        return False
    h = h[4:] if h.startswith("www.") else h
    return not h or h == host or h.endswith("." + host) or host.endswith("." + h)


def _bare_host(url: str) -> str:
    h = urlparse(url).netloc.lower().split(":")[0]
    return h[4:] if h.startswith("www.") else h


#: `candidate_rank` at or above this means "matched a negative term" — never
#: an inventory listing, whatever else the URL has going for it.
_RANK_NEGATIVE = 4


def candidate_rank(url: str, heuristics: HeuristicsConfig = DEFAULT_HEURISTICS) -> int:
    """Lower sorts first. How much a URL *looks like* an inventory listing,
    judged on path shape alone — before any page is loaded.

    Ordering matters more than it looks. The candidate loop stops early once a
    page shows enough vehicle links, so whatever sorts first is what the dealer
    gets scraped with. Ranking by nothing but homepage DOM order is how
    clutch.ca ended up configured against /auto-financing/car-loan-calculator
    while /cars sat further down the list, never evaluated.

    Ranks:
      0  exact known-good inventory path      (/cars, /inventory, /vehicules)
      1  known-good path plus filters/depth   (/inventory/used, /cars?type=suv)
      2  hints in the path, nothing negative  (/browse-our-selection)
      3  hints in the anchor text only        (path itself says nothing)
      4  matches a negative term              (/auto-financing/car-loan-calculator)
    """
    try:
        path = urlparse(url).path.lower().rstrip("/") or "/"
    except Exception:
        return 4

    if heuristics.nav_negative_re.search(path):
        return 4

    known = {p.rstrip("/") for p in heuristics.candidate_paths}
    if path in known:
        return 0
    if any(path.startswith(p + "/") or path == p for p in known):
        return 1
    if heuristics.nav_hints_re.search(path):
        return 2
    return 3


def _candidate_urls(base_url: str, probe: dict, host: str,
                     heuristics: HeuristicsConfig = DEFAULT_HEURISTICS) -> list[str]:
    """Inventory-page candidates, best-looking first.

    Sources are the homepage's own nav links plus the common inventory paths.
    Both are ranked by `candidate_rank`, so a real listing URL is evaluated
    before a financing or blog page that merely matched the loose nav lexicon.

    Ordering is: every plausible link the homepage actually offers (best-ranked
    first), then the guessed paths, then anything that matched a negative term.
    Source outranks path shape among plausible candidates on purpose — there
    are ~20 guessed paths against a candidate budget of 6, so sorting on shape
    alone lets guesses (most of which 404) crowd out the real nav link on any
    site whose listing isn't named something we predicted. A negative match
    still sorts last whatever its source: clutch.ca's own homepage links to its
    loan calculator, and that link must not outrank a guessed /cars.
    """
    seen: set[str] = set()
    scored: list[tuple[int, int, int, int, str]] = []

    def push(u: str, from_nav: bool) -> None:
        u = u.split("#")[0].rstrip("/")
        if not u or u in seen or not _same_host(u, host):
            return
        seen.add(u)
        rank = candidate_rank(u, heuristics)
        plausible = 1 if rank >= _RANK_NEGATIVE else 0
        # An unfiltered listing beats a filtered view of the same path: the
        # facet links are subsets, and DOM order alone would often put one of
        # them first.
        filtered = 1 if urlsplit(u).query else 0
        scored.append((plausible, 0 if from_nav else 1, rank, filtered, len(scored), u))

    for link in probe.get("navLinks", []):
        href, text = link.get("href", ""), link.get("text", "")
        if heuristics.nav_hints_re.search(href) or heuristics.nav_hints_re.search(text):
            push(urljoin(base_url, href), True)
    for path in heuristics.candidate_paths:
        push(urljoin(base_url, path), False)

    scored.sort(key=lambda t: t[:5])

    # One candidate per path. Faceted listings link dozens of filtered views of
    # the same page (`/inventory?refinementList[body_type][0]=Sedan`), which are
    # subsets of a page already in the list — on cardora.ca five of the six
    # candidate slots went to Algolia filter permutations of /inventory. The
    # best-ranked variant of each path survives; an unfiltered URL sorts ahead of
    # its own filtered forms, so that is normally the plain listing.
    by_path: dict[str, str] = {}
    for *_, url in scored:
        key = urlsplit(url).path.rstrip("/").lower()
        if key not in by_path:
            by_path[key] = url
        if len(by_path) >= 12:
            break
    return list(by_path.values())
