"""Open-ended detail-page detection: cluster ALL internal links by normalized
path shape (numeric/slug segments wildcarded) rather than matching a closed
list of known segment names (the pre-refactor `_DETAIL_SEGMENTS` approach,
still used as the primary signal in coordinator.py). A shape that recurs many
times and isn't obviously boilerplate (nav/blog/account/pagination) is strong
evidence of a repeating record type — a vehicle detail page — regardless of
what the URL happens to call it (fixing false negatives on sites using
`/stock/`, `/detail/`, `/auto/`, `/unit/`, or French `/vehicule/`).

Pure Python — the caller supplies already-captured link records (href/path,
optional nearby text), so this is testable without a browser and reusable
against sitemap-derived URL lists too (strategies/sitemap.py).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..heuristics import DEFAULT_HEURISTICS, HeuristicsConfig

_NUMERIC_RE = re.compile(r"^\d+$")
_SLUG_WITH_DIGITS_RE = re.compile(r"[-_]\d{3,}$")
_HEX_ID_RE = re.compile(r"^[0-9a-f]{8,}$", re.I)  # short hash / uuid-ish tail
# A hyphenated multi-word slug that ALSO carries a digit somewhere (real
# vehicle slugs always embed a year: "2024-toyota-rav4-le-awd"). Requiring
# both a hyphen and a digit is what tells "2024-toyota-camry-xle" apart from
# a perfectly ordinary static segment like "inventory" or "used-cars" — a
# plain "^[a-z0-9-]{8,}$" (no digit requirement) would wrongly flag both.
_SLUG_WITH_EMBEDDED_DIGIT_RE = re.compile(r"^(?=.*-)(?=.*\d)[a-z0-9-]{8,}$", re.I)


def _looks_like_id(segment: str) -> bool:
    """A path segment that looks like a record identifier rather than a fixed
    word: pure digits, a slug ending in a numeric tail (…-12345), a
    multi-word slug with a digit embedded (…-2024-…), or a hex-ish id."""
    return bool(
        _NUMERIC_RE.fullmatch(segment)
        or _SLUG_WITH_DIGITS_RE.search(segment)
        or _SLUG_WITH_EMBEDDED_DIGIT_RE.fullmatch(segment)
        or _HEX_ID_RE.fullmatch(segment)
    )


def normalize_path_shape(path: str) -> str:
    """/inventory/2024-toyota-rav4-le-awd/2516 -> /inventory/*/*
    /vehicles/9915201                          -> /vehicles/*
    /about-us                                  -> /about-us   (no id-like segment)
    """
    parts = [p for p in path.split("/") if p]
    shaped = ["*" if _looks_like_id(p) else p for p in parts]
    return "/" + "/".join(shaped)


@dataclass
class LinkRecord:
    path: str
    text: str = ""          # nearby/anchor text, if captured — feeds semantic scoring
    #: True when the anchor sits in the site's nav/header/footer/sidebar. Such
    #: links repeat identically on every page, so they form large, perfectly
    #: stable shape clusters that outscore real inventory — especially the
    #: "/used-sedans-under-5000" SEO menus common on dealer sites, which
    #: `_looks_like_id` cannot tell apart from a vehicle slug. Excluded from
    #: clustering. Defaults False so older callers keep their behaviour.
    chrome: bool = False


@dataclass
class LinkCluster:
    shape: str
    paths: list[str] = field(default_factory=list)
    sample_texts: list[str] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.paths)

    @property
    def segment(self) -> str:
        """First non-wildcard path segment — the same role `bestSeg` played in
        the legacy PROBE_JS output, so it feeds the existing
        strategies.pagination._build_detail_id_regex() / card_anchor building
        unchanged.

        Returns "" when the shape is ALL wildcards (`/*`), because such a shape
        names nothing: there is no literal segment to build `a[href*='/X/']`
        from. This used to return the invented literal "item", which produced
        `a[href*='/item/']` for a site that has no /item/ anywhere — discovery
        then "verified" 15 vehicles (they were footer links) and onboarded a
        dealer whose every sync failed. A cluster that cannot name itself is
        not evidence; see find_best_listing_cluster.
        """
        for part in self.shape.split("/"):
            if part and part != "*":
                return part
        return ""


def generalize_varying_segments(
    paths: list[str], min_distinct: int = 3,
) -> dict[str, str]:
    """Map each path to a shape where positions that VARY across otherwise
    identical URLs are wildcarded too — not just ones that look like ids.

    `normalize_path_shape` alone only wildcards segments that pattern-match an
    identifier (digits, hex, slug-with-number). That is blind to URL schemes
    which spell the record out in words:

        /vehicles/2026/mercedes-benz/gle/mississauga/on/70833715
        /vehicles/2025/nissan/rogue/mississauga/on/70889806

    Only the year and the trailing id get wildcarded, so every make/model pair
    becomes its own shape and a 500-car lot shatters into 200 clusters of two,
    none of which clears `link_shape_min_count`. Discovery then concludes the
    site has no repeating record type — the Convertus/Achilles platform, which
    is a large slice of Canadian dealers, was unreadable for exactly this
    reason.

    The signal we actually want is positional: within a set of same-depth URLs
    sharing a first segment, a position holding many different values is a
    variable, and one holding a single constant ("vehicles", "mississauga",
    "on") is a label. Position 0 is never generalised — a cluster whose own
    root varies is not one cluster.
    """
    groups: dict[tuple[int, str], list[tuple[str, list[str], list[str]]]] = {}
    for path in paths:
        parts = [p for p in path.split("/") if p]
        if not parts:
            continue
        shaped = ["*" if _looks_like_id(p) else p for p in parts]
        groups.setdefault((len(parts), shaped[0]), []).append((path, parts, shaped))

    out: dict[str, str] = {}
    for (depth, _root), members in groups.items():
        variable: set[int] = set()
        for i in range(1, depth):                       # never position 0
            values = {shaped[i] for _p, _parts, shaped in members if shaped[i] != "*"}
            if len(values) >= min_distinct:
                variable.add(i)
        for path, _parts, shaped in members:
            final = ["*" if (i in variable or shaped[i] == "*") else shaped[i]
                     for i in range(depth)]
            out[path] = "/" + "/".join(final)
    return out


def cluster_links_by_shape(
    links: list[LinkRecord], heuristics: HeuristicsConfig = DEFAULT_HEURISTICS,
) -> list[LinkCluster]:
    """Group links by normalized path shape, dropping shapes with no
    id-like segment (a shape is only a "record type" if something in it
    varies) and known-boilerplate path prefixes. Sorted by cluster size,
    largest first.
    """
    considered = [
        link for link in links[: heuristics.link_shape_max_links]
        # nav/header/footer furniture — see LinkRecord.chrome
        if not link.chrome and not heuristics.link_shape_ignore_re.search(link.path)
    ]
    # Wildcard word-segments that vary across same-shaped URLs (make, model,
    # city), so a /vehicles/{year}/{make}/{model}/{city}/{prov}/{id} scheme
    # forms ONE cluster instead of one per make/model pair.
    generalized = generalize_varying_segments([link.path for link in considered])

    buckets: dict[str, LinkCluster] = {}
    for link in considered:
        shape = generalized.get(link.path) or normalize_path_shape(link.path)
        if "*" not in shape:
            continue
        cluster = buckets.setdefault(shape, LinkCluster(shape=shape))
        if link.path not in cluster.paths:
            cluster.paths.append(link.path)
        if link.text and len(cluster.sample_texts) < 8:
            cluster.sample_texts.append(link.text)
    return sorted(buckets.values(), key=lambda c: c.count, reverse=True)


def find_best_listing_cluster(
    links: list[LinkRecord], heuristics: HeuristicsConfig = DEFAULT_HEURISTICS,
) -> LinkCluster | None:
    """The strongest "this is the detail-page URL shape" cluster, or None if
    nothing clears the minimum repeat count."""
    clusters = cluster_links_by_shape(links, heuristics)
    # An all-wildcard shape ("/*") cannot name a URL segment, so nothing
    # downstream can build a selector from it. Skip those rather than let one
    # win the count and fall through to an invented segment name.
    clusters = [c for c in clusters if c.segment]
    if not clusters:
        return None
    best = clusters[0]
    if best.count < heuristics.link_shape_min_count:
        return None
    return best
