"""Test helpers.

Both suites here start with Base.metadata.drop_all(), so they MUST be pointed at
a throwaway database. Run them like the module docstrings say:

    DATABASE_URL="sqlite:///./sync_test.db" python -m tests.test_sync_integration

Forgetting that env var used to silently wipe whatever DATABASE_URL pointed at —
i.e. the dev Postgres database. require_disposable_db() makes that a hard stop.
"""
from __future__ import annotations

from dealer_scraper.config import settings

# A DB is disposable if it is SQLite, or its name/URL is explicitly marked as a
# test database. Anything else is assumed to hold data someone cares about.
_TEST_MARKERS = ("test", "_tmp", "scratch")


def require_disposable_db() -> None:
    url = str(settings.database_url)
    lowered = url.lower()
    if lowered.startswith("sqlite"):
        return
    if any(marker in lowered.rsplit("/", 1)[-1] for marker in _TEST_MARKERS):
        return
    from dealer_scraper.proxy import redact_url

    raise SystemExit(
        "REFUSING TO RUN: this suite calls drop_all() and DATABASE_URL does not look\n"
        f"like a disposable database:\n    {redact_url(url)}\n\n"
        'Re-run against a throwaway DB, e.g.\n'
        '    DATABASE_URL="sqlite:///./sync_test.db" python -m tests.<module>\n'
    )
