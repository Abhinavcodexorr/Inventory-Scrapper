"""Pagination ends when the inventory does — not when the safety cap does.

Run:  python -m pytest tests/test_pagination_dedup.py
"""
from __future__ import annotations

from dealer_scraper.adapters.json_api import JsonApiAdapter

BASE = {"endpoint": "https://x.example/api?page={page}", "list_path": "items",
        "field_map": {"external_id": "stock"}, "page_size": 3, "delay_seconds": 0,
        "use_proxy": False, "fetch_concurrency": 1}


class Pages(JsonApiAdapter):
    def __init__(self, cfg: dict, pages: dict[int, list[str]]) -> None:
        super().__init__(cfg)
        self.pages, self.fetched = pages, []

    def _get(self, endpoint: str, page: int):
        self.fetched.append(page)
        return {"items": [{"stock": s} for s in self.pages.get(page, [])],
                "total": sum(len(v) for v in self.pages.values())}


def test_ignored_page_parameter_stops_after_one_repeat() -> None:
    a = Pages({**BASE, "max_pages": 500}, {p: ["A", "B", "C"] for p in range(1, 501)})
    got = [v.external_id for v in a.iter_vehicles()]
    assert got == ["A", "B", "C"]
    assert a.fetched == [1, 2], "the repeated page ends pagination"


def test_overlapping_pages_are_deduplicated_and_reading_continues() -> None:
    a = Pages(BASE, {1: ["A", "B", "C"], 2: ["C", "D", "E"], 3: ["F"]})
    assert [v.external_id for v in a.iter_vehicles()] == ["A", "B", "C", "D", "E", "F"]
    assert a.timer.snapshot()["counters"]["duplicates_skipped"] == 1
    assert a.fetched == [1, 2, 3]


def test_short_page_ends_pagination() -> None:
    a = Pages(BASE, {1: ["A", "B", "C"], 2: ["D"]})
    assert len(list(a.iter_vehicles())) == 4 and a.fetched == [1, 2]


def test_max_pages_is_a_hard_stop() -> None:
    a = Pages({**BASE, "max_pages": 2}, {p: [f"{p}{c}" for c in "abc"] for p in range(1, 10)})
    assert len(list(a.iter_vehicles())) == 6 and a.fetched == [1, 2]


def test_known_total_fetches_exactly_the_needed_pages_without_duplicates() -> None:
    a = Pages({**BASE, "total_path": "total"}, {1: ["A", "B", "C"], 2: ["C", "D", "E"], 3: ["F"]})
    got = [v.external_id for v in a.iter_vehicles()]
    assert sorted(got) == ["A", "B", "C", "D", "E", "F"] and len(got) == len(set(got))
    assert sorted(a.fetched) == [1, 2, 3]


def main() -> None:
    """`python -m tests.test_pagination_dedup` — several tests take pytest fixtures
    (parametrize, monkeypatch, caplog), so each named test runs through pytest."""
    import sys

    import pytest as _pytest

    tests = (
        test_ignored_page_parameter_stops_after_one_repeat,
        test_overlapping_pages_are_deduplicated_and_reading_continues,
        test_short_page_ends_pagination,
        test_max_pages_is_a_hard_stop,
        test_known_total_fetches_exactly_the_needed_pages_without_duplicates,
    )
    sys.exit(_pytest.main([__file__, "-q", "-p", "no:cacheprovider",
                           "-k", " or ".join(t.__name__ for t in tests)]))


if __name__ == "__main__":
    main()
