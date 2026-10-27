"""Layout-based confidence signal: do a candidate's "cards" form a regular
grid (similar sizes, arranged in rows/columns)? Pure Python over already-
captured bounding boxes — decoupled from the browser capture step so it is
testable without Playwright, and produces the raw value for scoring.py's
`visual_grid` signal.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass

from ..heuristics import DEFAULT_HEURISTICS, HeuristicsConfig


@dataclass
class BoundingBox:
    x: float
    y: float
    width: float
    height: float


@dataclass
class VisualGridResult:
    is_grid: bool
    raw_score: float          # 0..1, feeds scoring.CandidateScore's visual_grid signal
    row_count: int
    col_count: int
    width_cv: float           # coefficient of variation (stdev/mean)
    height_cv: float
    box_count: int


def _coefficient_of_variation(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = statistics.fmean(values)
    if mean <= 0:
        return 1.0
    return statistics.pstdev(values) / mean


def _cluster_axis(values: list[float], tolerance: float) -> int:
    """Count distinct 'rows'/'columns' by greedily grouping sorted coordinates
    that fall within `tolerance` of the current group's anchor."""
    if not values:
        return 0
    ordered = sorted(values)
    groups = 1
    anchor = ordered[0]
    for v in ordered[1:]:
        if v - anchor > tolerance:
            groups += 1
            anchor = v
    return groups


def analyze_grid(boxes: list[BoundingBox], heuristics: HeuristicsConfig = DEFAULT_HEURISTICS
                  ) -> VisualGridResult:
    """Regular grids have low width/height variance, and their boxes' top-left
    corners cluster into a small number of discrete rows/columns relative to
    the total box count (e.g. 12 boxes arranged 3-per-row -> 4 rows)."""
    usable = [b for b in boxes if b.width > 0 and b.height > 0]
    if len(usable) < heuristics.visual_grid_min_boxes:
        return VisualGridResult(is_grid=False, raw_score=0.0, row_count=0, col_count=0,
                                 width_cv=0.0, height_cv=0.0, box_count=len(usable))

    widths = [b.width for b in usable]
    heights = [b.height for b in usable]
    width_cv = _coefficient_of_variation(widths)
    height_cv = _coefficient_of_variation(heights)

    median_w = statistics.median(widths)
    median_h = statistics.median(heights)
    col_count = _cluster_axis([b.x for b in usable], tolerance=max(median_w * 0.5, 1))
    row_count = _cluster_axis([b.y for b in usable], tolerance=max(median_h * 0.5, 1))

    regular = width_cv <= heuristics.visual_grid_cv_max and height_cv <= heuristics.visual_grid_cv_max
    if not regular:
        return VisualGridResult(is_grid=False, raw_score=0.0, row_count=row_count,
                                 col_count=col_count, width_cv=width_cv, height_cv=height_cv,
                                 box_count=len(usable))

    # A believable grid packs roughly row_count * col_count cells for its box
    # count. A single-column list (col=1) or single-row carousel (row=1) still
    # counts as "regular" when sizes agree, just scored a little lower than a
    # true multi-row/column grid.
    grid_shape_bonus = 1.0 if (row_count > 1 and col_count > 1) else 0.6
    variance_score = 1.0 - min(1.0, (width_cv + height_cv) / (2 * heuristics.visual_grid_cv_max))
    raw_score = max(0.0, min(1.0, variance_score * grid_shape_bonus))

    return VisualGridResult(is_grid=True, raw_score=raw_score, row_count=row_count,
                             col_count=col_count, width_cv=width_cv, height_cv=height_cv,
                             box_count=len(usable))
