"""Operator login: sessions, the API-key path, and what must stay locked.

The admin panel used to be protected only by an API key that was compiled into
the JavaScript bundle — readable by anyone who could open the page. Operators
now sign in and hold an httpOnly session cookie instead, and machines keep the
key. These tests pin down both paths and the ways each is meant to fail.

Run:  DATABASE_URL="sqlite:///./.auth_test.db" python -m tests.test_auth
"""
from __future__ import annotations

import os
import time

os.environ.setdefault("DATABASE_URL", "sqlite:///./.auth_test.db")
os.environ.setdefault("API_KEY", "test-key")
os.environ.setdefault("SYNC_BACKEND", "local")

from fastapi.testclient import TestClient  # noqa: E402

from api.auth import (  # noqa: E402
    SESSION_COOKIE,
    hash_password,
    issue_session,
    read_session,
    verify_password,
)
from api.main import app  # noqa: E402
from dealer_scraper.config import settings  # noqa: E402
from dealer_scraper.db import SessionLocal, engine  # noqa: E402
from dealer_scraper.models import Base, User  # noqa: E402

from . import require_disposable_db  # noqa: E402

EMAIL = "operator@example.com"
PASSWORD = "a-sufficiently-long-password"


def _fresh_user(active: bool = True) -> int:
    require_disposable_db()
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        db.query(User).delete()
        u = User(email=EMAIL, password_hash=hash_password(PASSWORD), is_active=active)
        db.add(u)
        db.commit()
        return u.id


# --- password hashing --------------------------------------------------------
def test_password_round_trip() -> None:
    h = hash_password(PASSWORD)
    assert h != PASSWORD, "the password itself must never be stored"
    assert verify_password(PASSWORD, h)
    assert not verify_password(PASSWORD + "x", h)


def test_long_passwords_do_not_collide() -> None:
    """bcrypt ignores everything past 72 bytes. Without pre-hashing, two long
    passwords sharing a 72-byte prefix authenticate each other — a silent
    authentication bypass for anyone using a passphrase."""
    a = "x" * 100 + "AAAA"
    b = "x" * 100 + "BBBB"
    assert not verify_password(b, hash_password(a))
    assert verify_password(a, hash_password(a))


def test_corrupt_stored_hash_denies_rather_than_raises() -> None:
    """A mangled row should fail the login, not 500 the endpoint."""
    assert verify_password(PASSWORD, "not-a-bcrypt-hash") is False
    assert verify_password(PASSWORD, "") is False


# --- session cookie ----------------------------------------------------------
def test_session_round_trip() -> None:
    assert read_session(issue_session(7)) == 7


def test_tampered_session_is_rejected() -> None:
    """The whole security of the cookie is this signature check."""
    cookie = issue_session(7)
    body, _, sig = cookie.rpartition(".")
    assert read_session(f"{body}.{'0' * len(sig)}") is None
    # Changing the user id invalidates the signature — no privilege escalation
    # by editing the cookie to another account's id.
    assert read_session(f"9.{body.split('.')[1]}.{sig}") is None


def test_malformed_session_is_rejected() -> None:
    for bad in ("", "nonsense", "1.2", "a.b.c", "1.notatime.deadbeef"):
        assert read_session(bad) is None, bad
    assert read_session(None) is None


def test_expired_session_is_rejected() -> None:
    import unittest.mock as mock

    cookie = issue_session(7)
    # Pretend the cookie was issued longer ago than the configured lifetime.
    with mock.patch("api.auth.time.time", return_value=time.time()
                    + settings.session_max_age_hours * 3600 + 60):
        assert read_session(cookie) is None


def test_session_issued_in_the_future_is_rejected() -> None:
    """Guards against a forged timestamp being used to extend a session."""
    import unittest.mock as mock

    with mock.patch("api.auth.time.time", return_value=time.time() + 86400):
        cookie = issue_session(7)
    assert read_session(cookie) is None


# --- login endpoint ----------------------------------------------------------
def test_login_sets_a_session_cookie() -> None:
    _fresh_user()
    with TestClient(app) as c:
        r = c.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD})
        assert r.status_code == 200, r.text
        assert r.json()["email"] == EMAIL
        assert SESSION_COOKIE in r.cookies


def test_login_is_case_insensitive_on_email() -> None:
    _fresh_user()
    with TestClient(app) as c:
        r = c.post("/api/auth/login", json={"email": EMAIL.upper(), "password": PASSWORD})
        assert r.status_code == 200, r.text


def test_wrong_password_and_unknown_email_are_indistinguishable() -> None:
    """Different messages would let an attacker enumerate valid addresses."""
    _fresh_user()
    with TestClient(app) as c:
        wrong = c.post("/api/auth/login", json={"email": EMAIL, "password": "nope"})
        missing = c.post("/api/auth/login", json={"email": "who@example.com",
                                                  "password": PASSWORD})
    assert wrong.status_code == missing.status_code == 401
    assert wrong.json()["detail"] == missing.json()["detail"]


def test_disabled_account_cannot_sign_in() -> None:
    _fresh_user(active=False)
    with TestClient(app) as c:
        r = c.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD})
    assert r.status_code == 401


def test_login_records_last_login_at() -> None:
    uid = _fresh_user()
    with TestClient(app) as c:
        c.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD})
    with SessionLocal() as db:
        assert db.get(User, uid).last_login_at is not None


# --- protected routes --------------------------------------------------------
def test_protected_route_rejects_anonymous() -> None:
    _fresh_user()
    with TestClient(app) as c:
        assert c.get("/api/dealers").status_code == 401


def test_session_cookie_grants_access() -> None:
    _fresh_user()
    with TestClient(app) as c:
        c.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD})
        assert c.get("/api/dealers").status_code == 200


def test_api_key_still_works_for_machines() -> None:
    """Health checks, cron and the ops runbook depend on this path."""
    _fresh_user()
    with TestClient(app) as c:
        r = c.get("/api/dealers", headers={"X-API-Key": settings.api_key or "test-key"})
        assert r.status_code == 200


def test_wrong_api_key_is_rejected() -> None:
    with TestClient(app) as c:
        assert c.get("/api/dealers", headers={"X-API-Key": "wrong"}).status_code == 401


def test_me_reflects_the_session() -> None:
    _fresh_user()
    with TestClient(app) as c:
        assert c.get("/api/auth/me").status_code == 401
        c.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD})
        assert c.get("/api/auth/me").json()["email"] == EMAIL


def test_logout_clears_the_session() -> None:
    _fresh_user()
    with TestClient(app) as c:
        c.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD})
        assert c.post("/api/auth/logout").status_code == 204
        assert c.get("/api/dealers").status_code == 401


def test_logout_works_without_a_session() -> None:
    """Pressing sign-out on an already-expired session must not error."""
    with TestClient(app) as c:
        assert c.post("/api/auth/logout").status_code == 204


def test_deactivating_a_user_invalidates_their_live_session() -> None:
    """A cookie stays cryptographically valid until it expires, so /me must
    re-check the account — otherwise disabling someone does nothing until
    their session runs out."""
    uid = _fresh_user()
    with TestClient(app) as c:
        c.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD})
        assert c.get("/api/auth/me").status_code == 200
        with SessionLocal() as db:
            db.get(User, uid).is_active = False
            db.commit()
        assert c.get("/api/auth/me").status_code == 401


# --- docs --------------------------------------------------------------------
def test_docs_and_schema_require_auth() -> None:
    """Previously public: the full API surface was readable by anyone who
    found the server."""
    with TestClient(app) as c:
        assert c.get("/openapi.json").status_code == 401
        assert c.get("/docs").status_code == 401


def test_docs_open_once_signed_in() -> None:
    _fresh_user()
    with TestClient(app) as c:
        c.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD})
        assert c.get("/openapi.json").status_code == 200
        assert c.get("/docs").status_code == 200


def test_liveness_probe_stays_public() -> None:
    """The load balancer has no credentials; /health must answer without them."""
    with TestClient(app) as c:
        assert c.get("/health").status_code == 200


def main() -> None:
    test_password_round_trip()
    test_long_passwords_do_not_collide()
    test_corrupt_stored_hash_denies_rather_than_raises()
    test_session_round_trip()
    test_tampered_session_is_rejected()
    test_malformed_session_is_rejected()
    test_expired_session_is_rejected()
    test_session_issued_in_the_future_is_rejected()
    test_login_sets_a_session_cookie()
    test_login_is_case_insensitive_on_email()
    test_wrong_password_and_unknown_email_are_indistinguishable()
    test_disabled_account_cannot_sign_in()
    test_login_records_last_login_at()
    test_protected_route_rejects_anonymous()
    test_session_cookie_grants_access()
    test_api_key_still_works_for_machines()
    test_wrong_api_key_is_rejected()
    test_me_reflects_the_session()
    test_logout_clears_the_session()
    test_logout_works_without_a_session()
    test_deactivating_a_user_invalidates_their_live_session()
    test_docs_and_schema_require_auth()
    test_docs_open_once_signed_in()
    test_liveness_probe_stays_public()
    print("AUTH TESTS PASSED — sessions, API-key path, and the docs are locked down")


if __name__ == "__main__":
    main()
