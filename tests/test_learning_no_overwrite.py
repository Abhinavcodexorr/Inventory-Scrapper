"""A failed discovery must not replace the pattern that last worked.

write_pattern(..., success=False) used to store the failing pattern over the
row's existing one, so one bad run erased the only configuration known to work
for that host.

Run:  DATABASE_URL="sqlite:///./t.db" python -m pytest tests/test_learning_no_overwrite.py
"""
from __future__ import annotations

from dealer_scraper.db import SessionLocal
from dealer_scraper.discovery.learning import write_pattern
from dealer_scraper.models import SitePattern

GOOD = {"endpoint": "https://22gautosales.com/_next/data/{build_id}/cars.json"}
BAD = {"endpoint": "DISCOVERED_FORMULA7_INVENTORY_ENDPOINT"}


def test_failure_counts_but_keeps_the_working_pattern() -> None:
    host = "learning-test.example"
    with SessionLocal() as db:
        write_pattern(db, host, None, "api_endpoint", GOOD, source_run_id=1, success=True)
        write_pattern(db, host, None, "api_endpoint", BAD, source_run_id=2, success=False)
        row = db.query(SitePattern).filter_by(host=host, pattern_kind="api_endpoint").one()
        assert row.pattern == GOOD
        assert (row.success_count, row.failure_count) == (1, 1)
        assert row.source_run_id == 1

        write_pattern(db, host, None, "api_endpoint", {"endpoint": "https://new.example/x"},
                      source_run_id=3, success=True)
        db.refresh(row)
        assert row.pattern == {"endpoint": "https://new.example/x"}


def main() -> None:
    """`python -m tests.test_learning_no_overwrite` — several tests take pytest fixtures
    (parametrize, monkeypatch, caplog), so each named test runs through pytest."""
    import sys

    import pytest as _pytest

    tests = (
        test_failure_counts_but_keeps_the_working_pattern,
    )
    sys.exit(_pytest.main([__file__, "-q", "-p", "no:cacheprovider",
                           "-k", " or ".join(t.__name__ for t in tests)]))


if __name__ == "__main__":
    main()
