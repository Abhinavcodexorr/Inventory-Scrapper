"""Shared FastAPI dependencies."""
from __future__ import annotations

import secrets
from typing import Iterator

from fastapi import HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader
from sqlalchemy.orm import Session

from dealer_scraper.config import settings
from dealer_scraper.db import SessionLocal


def get_db() -> Iterator[Session]:
    """Request-scoped DB session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def require_api_key(key: str | None = Security(_api_key_header)) -> None:
    """Applied at router level in api/main.py so every route requires it —
    a new endpoint cannot forget to add it.

    An unconfigured API_KEY with api_auth_required still on fails closed
    (503): serving every request unauthenticated because someone forgot to
    set an env var in production is worse than the API being briefly down.
    """
    if not settings.api_auth_required:
        return
    if not settings.api_key:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "API_KEY is not configured on the server.",
        )
    if not key or not secrets.compare_digest(key, settings.api_key):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or missing X-API-Key.")


def require_auth(request: Request, key: str | None = Security(_api_key_header)) -> None:
    """Allow the request if it carries EITHER an operator session OR the API key.

    Two callers, two mechanisms, deliberately:

    * **People** sign in and get an httpOnly session cookie. The browser never
      holds a long-lived secret, so the admin bundle no longer has to ship one
      — which is what made the previous VITE_API_KEY arrangement uncomfortable:
      anyone who could load the page could read the key out of the JavaScript.
    * **Machines** — health checks, cron, integrations, the ops commands in
      SERVER_OPERATIONS.md — keep using X-API-Key. Requiring a login for a
      monitoring probe would mean storing a password in a monitoring config,
      which is worse than the header it replaced.

    Fails closed exactly as require_api_key does: with neither an API key nor a
    session secret configured, every request is refused with 503 rather than
    quietly served to anyone.
    """
    if not settings.api_auth_required:
        return

    # Session first: it is the common case in the browser, and checking it
    # costs one HMAC rather than a database round-trip.
    from .auth import SESSION_COOKIE, read_session  # local: avoids an import cycle

    if settings.session_secret or settings.api_key:
        if read_session(request.cookies.get(SESSION_COOKIE)) is not None:
            return

    if not settings.api_key:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Neither API_KEY nor SESSION_SECRET is configured on the server.",
        )
    if key and secrets.compare_digest(key, settings.api_key):
        return

    raise HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        "Sign in, or send a valid X-API-Key header.",
    )
