"""Operator login: session in, session out, who am I.

These three endpoints are the only ones in the API that are NOT behind the
auth dependency — a login form cannot require a login. They are rate-limit
sensitive by nature, so failures here are deliberately slow and vague.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from dealer_scraper.config import settings
from dealer_scraper.models import User

from ..auth import SESSION_COOKIE, hash_password, issue_session, read_session, verify_password
from ..deps import get_db

log = logging.getLogger("api.auth")

router = APIRouter(prefix="/api/auth", tags=["auth"])

# Cost of one bcrypt verification, spent even when the email does not exist.
# Without it, "no such user" returns visibly faster than "wrong password", and
# the difference is enough to enumerate which addresses are registered.
_DUMMY_HASH = hash_password("not-a-real-password")


class LoginRequest(BaseModel):
    # Plain str, not EmailStr: pydantic's email validation needs an extra
    # dependency and buys nothing here — an address that isn't in the users
    # table fails the lookup regardless of whether it is well-formed.
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=1, max_length=200)


class UserOut(BaseModel):
    id: int
    email: str
    full_name: str | None = None


def _set_session_cookie(response: Response, user_id: int) -> None:
    response.set_cookie(
        key=SESSION_COOKIE,
        value=issue_session(user_id),
        max_age=settings.session_max_age_hours * 3600,
        httponly=True,        # JavaScript cannot read it, so XSS cannot steal the session
        samesite="lax",       # not sent on cross-site writes — this is the CSRF defence
        secure=settings.session_cookie_secure,
        path="/",
    )


@router.post("/login", response_model=UserOut)
def login(payload: LoginRequest, response: Response, db: Session = Depends(get_db)) -> UserOut:
    """Exchange email + password for a session cookie.

    Every failure returns the same 401 and the same message. Saying "no such
    user" versus "wrong password" tells an attacker which half to keep
    guessing, which is a free gift for no operational benefit — the operator
    who genuinely forgot which address they used is better served by asking.
    """
    started = time.monotonic()
    email = payload.email.strip().lower()
    user = db.execute(
        select(User).where(func.lower(User.email) == email)
    ).scalar_one_or_none()

    # Verify against a dummy hash when the user is missing, so both paths cost
    # the same wall-clock time (see _DUMMY_HASH).
    stored = user.password_hash if user else _DUMMY_HASH
    ok = verify_password(payload.password, stored)

    if not user or not ok or not user.is_active:
        reason = ("unknown email" if not user
                  else "inactive account" if not user.is_active
                  else "bad password")
        log.warning("Failed login for %r (%s) in %.0fms",
                    email, reason, (time.monotonic() - started) * 1000)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect email or password.")

    user.last_login_at = datetime.now(timezone.utc)
    db.commit()

    _set_session_cookie(response, user.id)
    log.info("Login: user %s (%s)", user.id, email)
    return UserOut(id=user.id, email=user.email, full_name=user.full_name)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout() -> Response:
    """Clear the session cookie.

    Deliberately succeeds even without a valid session: "log me out" should
    never fail, and an already-expired cookie is exactly when someone presses it.

    The delete must be set on the response that is actually RETURNED. Setting
    it on an injected `Response` and then returning a different object throws
    the Set-Cookie header away, and logout silently leaves the session live —
    which is how this was written first, and what the test caught.
    """
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


@router.get("/me", response_model=UserOut)
def me(request: Request, db: Session = Depends(get_db)) -> UserOut:
    """The signed-in operator. The frontend calls this on load to decide
    between showing the app and redirecting to the login page."""
    user_id = read_session(request.cookies.get(SESSION_COOKIE))
    if user_id is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not signed in.")
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        # Account deleted or disabled while the cookie was still valid.
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not signed in.")
    return UserOut(id=user.id, email=user.email, full_name=user.full_name)
