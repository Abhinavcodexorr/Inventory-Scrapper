"""Password hashing and signed session cookies for the admin panel.

Two deliberate choices worth knowing before changing anything here.

**The cookie is signed, not encrypted, and holds no secret** — just a user id
and an issue time. Its integrity comes from an HMAC the browser cannot forge.
That keeps the whole mechanism in the standard library: no extra dependency to
audit, and nothing to get subtly wrong in a wrapper.

**Passwords are SHA-256'd before bcrypt.** bcrypt silently ignores everything
past 72 bytes, so without this two different long passwords can hash equal —
a real, quiet authentication bypass. Hashing first gives bcrypt a fixed
64-character input and removes the limit entirely. Base64 (not hex) because
bcrypt also stops at the first NUL byte, which raw digest bytes can contain.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time

import bcrypt

from dealer_scraper.config import settings

# Name kept boring on purpose: it tells an attacker nothing about the stack.
SESSION_COOKIE = "inventory_session"


def _prepared(password: str) -> bytes:
    """Fixed-length, NUL-free representation of any password (see module docstring)."""
    digest = hashlib.sha256(password.encode("utf-8")).digest()
    return base64.b64encode(digest)


def hash_password(password: str) -> str:
    """bcrypt hash, safe to store. Cost is bcrypt's default (currently 12)."""
    return bcrypt.hashpw(_prepared(password), bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    """Constant-time check. False on a malformed stored hash rather than raising,
    so a corrupted row denies the login instead of 500-ing the endpoint."""
    try:
        return bcrypt.checkpw(_prepared(password), password_hash.encode("ascii"))
    except (ValueError, TypeError):
        return False


def _secret() -> bytes:
    """Key the session cookies are signed with.

    Falls back to deriving from API_KEY so there is one fewer value to
    configure, and — importantly on this deployment — so sessions survive a
    restart. A random per-boot key would sign everyone out on every deploy.
    The derivation means the session key is never the API key itself, so
    leaking one does not hand over the other.
    """
    raw = settings.session_secret or settings.api_key
    if not raw:
        # Callers must not reach here: require_api_key already refuses to serve
        # when neither is configured. Guard anyway rather than sign with "".
        raise RuntimeError("Neither SESSION_SECRET nor API_KEY is configured")
    return hashlib.sha256(f"session:{raw}".encode("utf-8")).digest()


def issue_session(user_id: int) -> str:
    """Cookie value binding a user id to an issue time, with an HMAC over both."""
    payload = f"{user_id}.{int(time.time())}"
    sig = hmac.new(_secret(), payload.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def read_session(cookie: str | None) -> int | None:
    """The user id in a valid, unexpired cookie — otherwise None.

    Every failure returns None rather than raising: a tampered or stale cookie
    is an ordinary unauthenticated request, not a server error.
    """
    if not cookie:
        return None
    parts = cookie.split(".")
    if len(parts) != 3:
        return None
    user_id_s, issued_s, sig = parts
    expected = hmac.new(_secret(), f"{user_id_s}.{issued_s}".encode("ascii"),
                        hashlib.sha256).hexdigest()
    # compare_digest, not ==, so the comparison cannot be timed to forge a signature.
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        user_id, issued = int(user_id_s), int(issued_s)
    except ValueError:
        return None
    if issued > time.time() + 60:  # issued in the future — clock skew or forgery
        return None
    if time.time() - issued > settings.session_max_age_hours * 3600:
        return None
    return user_id


def new_secret() -> str:
    """A fresh SESSION_SECRET value, for the setup docs."""
    return secrets.token_hex(32)
