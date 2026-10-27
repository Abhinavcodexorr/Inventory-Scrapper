"""Header analysis: classify headers as volatile (request-specific) or durable.

Volatile headers (auth tokens, CSRF tokens, session IDs) are request-specific
and vary across calls. Discovering a config that depends on a volatile header
is discovered, but confidence caps at medium and the header is stripped before
verification to prove the config works with durable headers only.
"""
from __future__ import annotations

import re


def _is_volatile_auth(name: str, value: str) -> bool:
    """Heuristically detect whether a header looks request-specific."""
    name_lower = name.lower()
    value_str = str(value).strip()

    if not value_str:
        return False

    # JWT-looking: eyJxxx... (base64 header.payload.signature)
    if value_str.startswith("eyJ") and value_str.count(".") == 2:
        return True

    # Bearer token: "Bearer eyJ..." or similar
    if name_lower in ("authorization",) and value_str.lower().startswith(("bearer ", "token ")):
        return True

    # Session/CSRF cookies (not headers per se, but relevant)
    if any(x in name_lower for x in ("cookie", "session", "csrf", "xsrf", "auth")):
        # Heuristic: if the value looks like a hash/token (36+ chars, mostly alphanumeric),
        # it's probably volatile
        if len(value_str) >= 32 and re.match(r"^[a-zA-Z0-9\-_=.]+$", value_str):
            return True

    # X-API-Key, X-Auth-Token, etc. with long values
    if any(x in name_lower for x in ("api-key", "auth-token", "auth-code", "access-token")):
        if len(value_str) >= 16 and re.match(r"^[a-zA-Z0-9\-_=.]+$", value_str):
            return True

    return False


def classify_headers(headers: dict | None) -> tuple[dict, dict]:
    """Split headers into durable and volatile groups.

    Returns (durable_headers, volatile_headers) both as dicts.
    Headers classified volatile should be stripped during verification
    to prove the config works with stable auth (e.g. API keys in env vars).
    """
    if not headers:
        return {}, {}

    durable = {}
    volatile = {}

    for name, value in headers.items():
        if _is_volatile_auth(name, value):
            volatile[name] = value
        else:
            durable[name] = value

    return durable, volatile
