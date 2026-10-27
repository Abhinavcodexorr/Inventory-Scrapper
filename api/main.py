"""FastAPI application entrypoint.

Run locally:
    uvicorn api.main:app --reload --port 8000

Interactive docs:  http://localhost:8000/docs
"""
from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Response, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.orm import Session

from dealer_scraper.config import settings
from worker.celery_app import broker_available, workers_available
from .deps import get_db, require_auth
from .routers import auth as auth_router
from .routers import changes, dashboard, dealers, vehicles

log = logging.getLogger("api.main")

# Populated once at startup (see _warm_readiness_checks) and read by
# /health/ready. Resolving the Chromium binary path is cheap but not free, and
# it never changes during the process's lifetime — checking it per request
# would mean every readiness poll touches disk for no reason.
_readiness: dict[str, bool | None] = {"chromium_installed": None}


def _playwright_browsers_dir() -> Path:
    """Default Playwright browser cache (override with PLAYWRIGHT_BROWSERS_PATH)."""
    import os

    import sys

    override = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if override:
        return Path(override)
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local) / "ms-playwright"
    if sys.platform == "darwin":
        # macOS Playwright caches under ~/Library/Caches, not ~/.cache. Using
        # the Linux path here reported chromium:false — and /health/ready
        # "degraded" — on every Mac dev machine with a perfectly good install.
        return Path.home() / "Library" / "Caches" / "ms-playwright"
    return Path.home() / ".cache" / "ms-playwright"


def _check_chromium_installed() -> bool:
    """True if Playwright's Chromium binary is on disk.

    Uses a filesystem probe only — never calls sync_playwright() here. On
    Windows/Python 3.13, launching Playwright from a background thread during
    uvicorn startup raises asyncio NotImplementedError for subprocess transport.
    """
    base = _playwright_browsers_dir()
    if not base.is_dir():
        return False
    for chromium_dir in base.glob("chromium-*"):
        candidates = (
            chromium_dir / "chrome-win" / "chrome.exe",
            chromium_dir / "chrome-linux" / "chrome",
            chromium_dir / "chrome-mac" / "Chromium.app",
        )
        if any(p.exists() for p in candidates):
            return True
    return False


def _warm_broker_probe() -> None:
    """Resolve (and cache) broker reachability off the request path.

    POST /dealers/{id}/sync picks Celery or the in-process pool based on this
    probe. With Redis down and a cold cache the probe costs up to ~1s, which the
    first user to click Sync would otherwise pay. Warming it at startup keeps
    that request fast, and tells the operator which backend is live.
    """
    available = broker_available()
    log.info(
        "Broker probe: Redis %s — syncs will run %s",
        "reachable" if available else "unreachable",
        "via Celery" if available else "in-process (no queue)",
    )


def _warm_readiness_checks() -> None:
    installed = _check_chromium_installed()
    _readiness["chromium_installed"] = installed
    if not installed:
        log.warning(
            "Chromium is not installed (run: playwright install chromium) — "
            "discovery and browser-mode scrapes will fail at runtime."
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    threading.Thread(target=_warm_broker_probe, daemon=True).start()
    threading.Thread(target=_warm_readiness_checks, daemon=True).start()
    yield


app = FastAPI(
    title="Dealership Inventory Hub API",
    version="1.0.0",
    description="Dealership onboarding, inventory scraping and daily inventory tracking.",
    lifespan=lifespan,
    # The generated docs are served by our own guarded routes below instead of
    # FastAPI's automatic ones, so the schema is not readable by anyone who
    # knows the URL. Turning them off here is what frees /docs and
    # /openapi.json to be re-declared with an auth dependency.
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allow_origins,
    # Credentialed now that operators authenticate with a session cookie: the
    # browser must be allowed to send it during local development, where the
    # Vite dev server and the API are different origins. In production nginx
    # serves both from one origin and CORS never engages. The cookie is
    # SameSite=Lax, which is what stops other sites using this.
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-API-Key"],
)

# Login endpoints are intentionally unguarded — a sign-in form cannot require
# a sign-in. Everything else is guarded at router level, not per-handler, so a
# newly added endpoint cannot forget it.
app.include_router(auth_router.router)
app.include_router(dealers.router, dependencies=[Depends(require_auth)])
app.include_router(vehicles.router, dependencies=[Depends(require_auth)])
app.include_router(dashboard.router, dependencies=[Depends(require_auth)])
app.include_router(changes.router, dependencies=[Depends(require_auth)])


@app.get("/openapi.json", include_in_schema=False, dependencies=[Depends(require_auth)])
def openapi_schema():
    """The API schema, behind auth. Previously public: anyone who found the
    server could read every endpoint, parameter and model without signing in."""
    return app.openapi()


@app.get("/docs", include_in_schema=False, dependencies=[Depends(require_auth)])
def swagger_docs():
    from fastapi.openapi.docs import get_swagger_ui_html

    return get_swagger_ui_html(openapi_url="/openapi.json", title="Inventory Hub API")


@app.get("/health", tags=["meta"])
def health():
    """Unauthenticated liveness probe for the load balancer: only confirms the
    process is up and answering HTTP. Does not touch the DB, Redis, or
    Chromium — see /health/ready for that."""
    return {"status": "ok"}


@app.get("/health/ready", tags=["meta"], dependencies=[Depends(require_auth)])
def health_ready(response: Response, db: Session = Depends(get_db)):
    """Authenticated readiness probe covering the dependencies a discovery or
    scrape run actually needs. A missing Chromium binary or a dead database
    shows up here instead of surfacing only when a background job fails."""
    checks: dict[str, bool] = {}

    try:
        db.execute(text("SELECT 1"))
        checks["database"] = True
    except Exception:
        checks["database"] = False

    redis_up = broker_available()
    checks["redis"] = redis_up
    checks["worker"] = workers_available() if redis_up else False
    checks["chromium"] = bool(_readiness.get("chromium_installed"))

    # Database and Chromium are hard requirements. Redis/worker are soft —
    # the local sync backend runs fine without either.
    critical_ok = checks["database"] and checks["chromium"]
    if not critical_ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "ok" if critical_ok else "degraded", "checks": checks}
