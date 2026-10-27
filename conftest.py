"""Shared pytest fixtures and configuration for the whole suite.

This file lives at the REPO ROOT, not inside tests/, deliberately: tests/ is a
package (tests/__init__.py exists), and tests/__init__.py itself does
`from dealer_scraper.config import settings` at module level. If this file
lived at tests/conftest.py, importing it as `tests.conftest` would force
Python to run tests/__init__.py first — constructing the Settings() singleton
from whatever DATABASE_URL/.env already says — before a single line of this
file's own env-var setup ran. A root conftest.py has no such parent package to
initialize first, so the os.environ.setdefault() calls below are guaranteed to
run before anything imports dealer_scraper.config.

Settings is a module-level singleton built at import time
(dealer_scraper/config.py:55), and dealer_scraper/db.py reads
settings.database_url at import time to build the SQLAlchemy engine — so
environment variables set anywhere after that first import are too late.

`os.environ.setdefault` means a DATABASE_URL already set in the real
environment (e.g. a developer pointed at staging) is respected, not silently
overridden — require_disposable_db() below then correctly refuses to run
rather than risk a real database, exactly as it does under the pre-existing
`python -m tests.test_x` invocations.

Tests that need a real browser or real network are marked `live` — either
explicitly with `@pytest.mark.live` or implicitly by requesting the
`playwright`/`browser` fixtures below — and are skipped unless `--live` is
passed, so `pytest -q` stays fast and fully offline by default.
"""
from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./.pytest_discovery.db")
os.environ.setdefault("SYNC_BACKEND", "local")
os.environ.setdefault("REDIS_URL", "not-a-valid-url")
os.environ.setdefault("API_KEY", "test-key")

import httpx
import pytest

from tests import require_disposable_db


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--live", action="store_true", default=False,
        help="also run tests needing real network access or a real browser",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list) -> None:
    if config.getoption("--live"):
        return
    skip_live = pytest.mark.skip(reason="needs --live (real network or a browser)")
    for item in items:
        needs_live = (
            "live" in item.keywords
            or "playwright" in getattr(item, "fixturenames", ())
            or "browser" in getattr(item, "fixturenames", ())
        )
        if needs_live:
            item.add_marker(skip_live)


@pytest.fixture(scope="session", autouse=True)
def db() -> None:
    """A clean, disposable schema for the whole test session.

    Mirrors what every DB-touching test module's own main() already does by
    hand (require_disposable_db() + drop_all() + create_all()) — this makes
    that guard apply under `pytest` too, not just under `python -m`, and lets
    DB-dependent tests run without each one repeating the setup. Autouse and
    session-scoped: runs once, before the first test, regardless of which
    modules pytest happens to collect first.
    """
    require_disposable_db()
    from dealer_scraper.db import engine
    from dealer_scraper.models import Base

    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield


@pytest.fixture(autouse=True)
def _offline_sitemap_phase(request, monkeypatch) -> None:
    """Keep DiscoveryCoordinator.run() off the network in offline tests.

    Coordinator tests stub the browser phase but not the sitemap phase, which
    fetched the real robots.txt of their placeholder host (example.com). Where
    that host is slow or unreachable every such test hung until the timeout.
    The sitemap code itself is tested directly with mock clients
    (test_discovery_sitemap.py), so nothing loses coverage.
    """
    if request.config.getoption("--live"):
        return
    from dealer_scraper.discovery.coordinator import DiscoveryCoordinator

    monkeypatch.setattr(DiscoveryCoordinator, "_run_sitemap_phase", lambda self, ctx: None)


@pytest.fixture(scope="session")
def playwright():
    """Real Playwright driver. Session-scoped since launching it is slow.
    Only ever reached by tests that request it, which
    pytest_collection_modifyitems above skips unless --live is passed."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        yield p


@pytest.fixture(scope="session")
def browser(playwright):
    b = playwright.chromium.launch(headless=True)
    yield b
    b.close()


@pytest.fixture
def client():
    """FastAPI TestClient pre-authenticated with the test API key, so call
    sites don't each need to know the auth header name/value."""
    from fastapi.testclient import TestClient

    from api.main import app

    return TestClient(app, headers={"X-API-Key": os.environ.get("API_KEY", "test-key")})


@pytest.fixture
def mock_client():
    """Factory for an offline httpx.Client backed by httpx.MockTransport —
    generalizes the pattern already proven in test_discovery_sitemap.py."""

    def _make(handler) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(handler))

    return _make
