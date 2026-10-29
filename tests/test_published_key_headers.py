"""A site's published client key is durable, not a request-specific token.

etobicokefinecars.com (a Lovable site) reads its inventory from Supabase REST
with the project's anon key, a JWT valid for ten years that ships in the JS
bundle. Every JWT was treated as volatile, so discovery saved the config with
neither `apikey` nor `Authorization`, and each sync got 401 Unauthorized.

Run:  python -m tests.test_published_key_headers
"""
from __future__ import annotations

import base64
import json

from dealer_scraper.discovery.net.headers import classify_headers


def _jwt(payload: dict) -> str:
    def enc(obj: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")
    return f"{enc({'alg': 'HS256', 'typ': 'JWT'})}.{enc(payload)}.c2lnbmF0dXJl"


ANON_KEY = _jwt({"iss": "supabase", "ref": "abcdefghijklmnop", "role": "anon",
                 "iat": 1778872672, "exp": 2094448672})
SESSION = _jwt({"sub": "user-1", "iat": 1778872672, "exp": 1778876272})


def test_supabase_anon_key_is_kept_in_both_headers() -> None:
    durable, volatile = classify_headers({"apikey": ANON_KEY, "Authorization": f"Bearer {ANON_KEY}"})
    assert durable == {"apikey": ANON_KEY, "Authorization": f"Bearer {ANON_KEY}"}
    assert volatile == {}


def test_short_lived_session_jwt_is_still_volatile() -> None:
    durable, volatile = classify_headers({"Authorization": f"Bearer {SESSION}", "x-token": SESSION})
    assert durable == {} and set(volatile) == {"Authorization", "x-token"}


def test_jwt_without_expiry_or_unreadable_payload_is_still_volatile() -> None:
    no_exp = _jwt({"sub": "user-1", "iat": 1778872672})
    _durable, volatile = classify_headers({"a": no_exp, "b": "eyJhbGciOi.not-base64!.sig"})
    assert set(volatile) == {"a", "b"}


def main() -> None:
    test_supabase_anon_key_is_kept_in_both_headers()
    test_short_lived_session_jwt_is_still_volatile()
    test_jwt_without_expiry_or_unreadable_payload_is_still_volatile()
    print("published-key header tests passed")


if __name__ == "__main__":
    main()
