"""Unit tests for the pure-Python Phase 2 analyzers: open-ended link-shape
clustering (dom_repetition.py) and bounding-box grid regularity (visual_structure.py).

Pure functions only — no Playwright, no DB needed.

Run:  python -m tests.test_discovery_analyzers
"""
from __future__ import annotations

from dealer_scraper.discovery.analyzers.dom_repetition import (
    LinkRecord,
    cluster_links_by_shape,
    find_best_listing_cluster,
    normalize_path_shape,
)
from dealer_scraper.discovery.analyzers.visual_structure import BoundingBox, analyze_grid
from dealer_scraper.discovery.heuristics import DEFAULT_HEURISTICS


# --------------------------------------------------------- dom_repetition
def test_normalize_path_shape_wildcards_id_like_segments_only():
    assert normalize_path_shape("/inventory/2024-toyota-rav4-le-awd/2516") == "/inventory/*/*"
    assert normalize_path_shape("/vehicules/9915201") == "/vehicules/*"
    assert normalize_path_shape("/vdp/2024-honda-civic-touring") == "/vdp/*"


def test_normalize_path_shape_leaves_plain_words_alone():
    # Plain dictionary words — even long ones like "inventory" — must NOT be
    # treated as id-like, or every static nav segment would look like a record.
    assert normalize_path_shape("/inventory") == "/inventory"
    assert normalize_path_shape("/about-us") == "/about-us"
    assert normalize_path_shape("/used-cars") == "/used-cars"
    assert normalize_path_shape("/vehicules-occasion") == "/vehicules-occasion"


def test_cluster_links_by_shape_finds_open_ended_detail_pattern():
    # A site using an unrecognised segment name ("stock") that the closed
    # _DETAIL_SEGMENTS list in the pre-refactor engine would have missed.
    links = [LinkRecord(f"/stock/unit-{i}-detail/{1000 + i}") for i in range(12)]
    links += [LinkRecord("/blog/2024/news-1"), LinkRecord("/about-us"), LinkRecord("/contact")]

    clusters = cluster_links_by_shape(links)
    assert clusters[0].shape == "/stock/*/*"
    assert clusters[0].count == 12
    assert clusters[0].segment == "stock"


def test_cluster_links_by_shape_ignores_boilerplate_prefixes():
    links = [LinkRecord(f"/blog/2024/post-{i}") for i in range(20)]
    links += [LinkRecord(f"/account/order-{i}") for i in range(20)]
    clusters = cluster_links_by_shape(links)
    assert clusters == []  # both shapes are denylisted despite high repeat counts


def test_find_best_listing_cluster_requires_minimum_count():
    links = [LinkRecord(f"/detail/{i}") for i in range(3)]  # below link_shape_min_count (6)
    assert find_best_listing_cluster(links) is None

    links += [LinkRecord(f"/detail/{i}") for i in range(3, 8)]
    best = find_best_listing_cluster(links)
    assert best is not None
    assert best.count == 8


def test_find_best_listing_cluster_prefers_largest_cluster():
    links = [LinkRecord(f"/vehicles/{i}") for i in range(10)]
    links += [LinkRecord(f"/vehicles/{i}/compare/{i}") for i in range(7)]
    best = find_best_listing_cluster(links)
    assert best.shape == "/vehicles/*"
    assert best.count == 10


def test_french_detail_url_shape_is_detected():
    links = [LinkRecord(f"/vehicules-occasion/2022-honda-civic-{i}/{5000 + i}") for i in range(9)]
    best = find_best_listing_cluster(links)
    assert best is not None
    assert best.shape == "/vehicules-occasion/*/*"


# --------------------------------------------------------- visual_structure
def test_analyze_grid_recognizes_regular_multi_row_grid():
    boxes = [BoundingBox(x=col * 300, y=row * 400, width=280, height=380)
             for row in range(4) for col in range(3)]
    result = analyze_grid(boxes)
    assert result.is_grid is True
    assert result.row_count == 4
    assert result.col_count == 3
    assert result.raw_score > 0.8


def test_analyze_grid_rejects_irregular_sizes():
    boxes = [BoundingBox(x=i * 50, y=i * 10, width=50 + i * 40, height=30 + i * 25) for i in range(6)]
    result = analyze_grid(boxes)
    assert result.is_grid is False
    assert result.raw_score == 0.0


def test_analyze_grid_below_minimum_box_count_is_not_a_grid():
    boxes = [BoundingBox(x=0, y=0, width=200, height=200)] * 2
    result = analyze_grid(boxes, DEFAULT_HEURISTICS)
    assert result.is_grid is False
    assert result.box_count == 2


def test_analyze_grid_single_column_list_scores_lower_than_true_grid():
    single_col = [BoundingBox(x=0, y=row * 200, width=600, height=190) for row in range(6)]
    true_grid = [BoundingBox(x=col * 300, y=row * 200, width=280, height=190)
                 for row in range(3) for col in range(2)]
    single_result = analyze_grid(single_col)
    grid_result = analyze_grid(true_grid)
    assert single_result.is_grid is True  # still regular, just not multi-axis
    assert single_result.raw_score < grid_result.raw_score


def main() -> None:
    test_normalize_path_shape_wildcards_id_like_segments_only()
    test_normalize_path_shape_leaves_plain_words_alone()
    test_cluster_links_by_shape_finds_open_ended_detail_pattern()
    test_cluster_links_by_shape_ignores_boilerplate_prefixes()
    test_find_best_listing_cluster_requires_minimum_count()
    test_find_best_listing_cluster_prefers_largest_cluster()
    test_french_detail_url_shape_is_detected()
    test_analyze_grid_recognizes_regular_multi_row_grid()
    test_analyze_grid_rejects_irregular_sizes()
    test_analyze_grid_below_minimum_box_count_is_not_a_grid()
    test_analyze_grid_single_column_list_scores_lower_than_true_grid()
    print("DISCOVERY ANALYZERS TEST PASSED")


if __name__ == "__main__":
    main()
