"""Discovery must not fingerprint the site's furniture and call it inventory.

tabangimotors.com onboarded green and then failed every sync with 0 vehicles.
The cause was not the scraper. Its nav carries SEO links like
"/used-sedans-under-5000" — a single hyphenated segment ending in "-5000" —
and `_looks_like_id` classifies that as a record identifier. So all 15 of them
normalised to the shape "/*", formed the largest cluster on the page, and won.

An all-wildcard shape names no URL segment, so `LinkCluster.segment` fell
through to the literal "item" and discovery emitted `a[href*='/item/']` for a
site that has no /item/ anywhere. Because those links live in the nav, they
appear on EVERY page — which is why five different filtered pages each
reported exactly 15 "vehicles", a number no real filtered inventory would
repeat.

These tests pin the three properties that keep that from recurring.

Run:  python -m tests.test_link_shape_furniture
"""
from __future__ import annotations

from dealer_scraper.discovery.analyzers.dom_repetition import (
    LinkCluster,
    LinkRecord,
    cluster_links_by_shape,
    find_best_listing_cluster,
    normalize_path_shape,
)

# The real nav block, verbatim in shape.
FURNITURE = [
    "/used-sedans-under-5000", "/used-sedans-under-10000", "/used-sedans-under-15000",
    "/used-sedans-under-20000", "/used-sedans-under-25000",
    "/used-suvs-under-5000", "/used-suvs-under-10000", "/used-suvs-under-15000",
    "/used-suvs-under-20000", "/used-suvs-under-25000",
    "/used-trucks-under-15000", "/used-trucks-under-20000",
    "/used-trucks-under-40000", "/used-trucks-under-50000",
    "/used-cars-under-30000-mississauga",
]

# The real vehicle detail URLs, verbatim in shape.
VEHICLES = [
    "/vehicles/2026/mercedes-benz/gle/mississauga/on/70833715/",
    "/vehicles/2026/mercedes-benz/cla/mississauga/on/70810851/",
    "/vehicles/2026/audi/q7/mississauga/on/71131005/",
    "/vehicles/2026/volkswagen/tiguan/mississauga/on/71163304/",
    "/vehicles/2025/nissan/rogue/mississauga/on/70889806/",
    "/vehicles/2025/ford/f-150/mississauga/on/71163305/",
    "/vehicles/2024/toyota/rav4/mississauga/on/70912233/",
]


def test_marketing_slug_still_looks_like_an_id() -> None:
    """Not a bug to fix here — documenting WHY the DOM-position filter is the
    fix. "/used-sedans-under-5000" is shape-identical to a vehicle slug, so no
    amount of regex tightening separates them; only where they sit does."""
    assert normalize_path_shape("/used-sedans-under-5000") == "/*"
    assert normalize_path_shape("/2024-toyota-rav4-le-awd") == "/*"


def test_nav_links_are_excluded_from_clustering() -> None:
    links = [LinkRecord(path=p, chrome=True) for p in FURNITURE]
    assert cluster_links_by_shape(links) == [], (
        "nav/footer links must never form a cluster — they repeat on every "
        "page and will outscore real inventory")


def test_all_wildcard_shape_names_nothing() -> None:
    """The invented "item" segment is what turned a bad cluster into a bad
    selector. A shape with no literal segment must not name one."""
    assert LinkCluster(shape="/*", paths=["/a-1000"]).segment == ""
    assert LinkCluster(shape="/vehicles/*", paths=["/vehicles/1"]).segment == "vehicles"


def test_furniture_never_wins_even_when_it_outnumbers_inventory() -> None:
    """The exact Tabangi situation: 15 nav links vs 7 real vehicles. Before the
    fix the nav won on count and produced a[href*='/item/']."""
    links = ([LinkRecord(path=p, chrome=True) for p in FURNITURE]
             + [LinkRecord(path=p) for p in VEHICLES])
    best = find_best_listing_cluster(links)
    assert best is not None, "the real vehicle cluster should still be found"
    assert best.segment == "vehicles", f"expected the vehicle shape, got {best.shape!r}"
    assert best.count == len(VEHICLES)


def test_furniture_alone_yields_no_cluster_at_all() -> None:
    """When the grid hasn't rendered, the honest answer is "nothing found" —
    not a confident config built from the menu."""
    links = [LinkRecord(path=p, chrome=True) for p in FURNITURE]
    assert find_best_listing_cluster(links) is None


def test_vehicle_shape_survives_varied_makes_and_models() -> None:
    """Make/model are plain words, so they are NOT wildcarded and each
    make/model pair forms its own shape. The cluster must still be found —
    here via the year and the trailing stock id."""
    shapes = {normalize_path_shape(p) for p in VEHICLES}
    assert all(s.startswith("/vehicles/*/") for s in shapes), shapes
    assert all(s.endswith("/*") for s in shapes), shapes


def main() -> None:
    test_marketing_slug_still_looks_like_an_id()
    test_nav_links_are_excluded_from_clustering()
    test_all_wildcard_shape_names_nothing()
    test_furniture_never_wins_even_when_it_outnumbers_inventory()
    test_furniture_alone_yields_no_cluster_at_all()
    test_vehicle_shape_survives_varied_makes_and_models()
    print("LINK SHAPE TESTS PASSED — site furniture can no longer pass as inventory")


if __name__ == "__main__":
    main()
