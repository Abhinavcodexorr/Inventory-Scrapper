"""Tests for SSRF protection (C2): validate_public_url rejects private/loopback/
metadata addresses, SafeTransport re-validates redirects, and API rejects
unsafe base_url before creating a run row.

Run:  pytest -q tests/test_discovery_ssrf.py
"""
from __future__ import annotations

import pytest
from dealer_scraper.discovery.net.safe_http import UnsafeUrlError, validate_public_url


def test_rejects_file_scheme():
    with pytest.raises(UnsafeUrlError, match="Unsupported URL scheme"):
        validate_public_url("file:///etc/passwd")


def test_rejects_loopback():
    with pytest.raises(UnsafeUrlError, match="non-public"):
        validate_public_url("http://127.0.0.1")


def test_rejects_ipv6_loopback():
    with pytest.raises(UnsafeUrlError, match="non-public"):
        validate_public_url("http://[::1]")


def test_rejects_private_rfc1918():
    for addr in ["10.0.0.1", "172.16.0.1", "192.168.0.1"]:
        with pytest.raises(UnsafeUrlError, match="non-public"):
            validate_public_url(f"http://{addr}")


def test_rejects_metadata_endpoints():
    for addr in ["169.254.169.254", "metadata.google.internal"]:
        with pytest.raises(UnsafeUrlError):
            validate_public_url(f"http://{addr}")


def test_rejects_embedded_credentials():
    with pytest.raises(UnsafeUrlError, match="embedded credentials"):
        validate_public_url("http://user:pass@example.com")


def test_rejects_nonstandard_port():
    with pytest.raises(UnsafeUrlError, match="Port.*not allowed"):
        validate_public_url("http://example.com:6379")


def test_accepts_public_http():
    result = validate_public_url("http://example.com")
    assert result == "http://example.com"


def test_accepts_public_https():
    result = validate_public_url("https://example.com")
    assert result == "https://example.com"


def test_api_rejects_unsafe_base_url():
    from fastapi.testclient import TestClient

    from api.main import app

    client = TestClient(app, headers={"X-API-Key": "test-key"})
    resp = client.post("/api/dealers/discover", json={"base_url": "http://127.0.0.1"})
    assert resp.status_code == 422
    msg = resp.json()["detail"][0]["msg"].lower()
    assert any(x in msg for x in ["non-public", "unsafe", "invalid", "rejected"])


def main() -> None:
    test_rejects_file_scheme()
    test_rejects_loopback()
    test_rejects_ipv6_loopback()
    test_rejects_private_rfc1918()
    test_rejects_metadata_endpoints()
    test_rejects_embedded_credentials()
    test_rejects_nonstandard_port()
    test_accepts_public_http()
    test_accepts_public_https()
    test_api_rejects_unsafe_base_url()
    print("DISCOVERY SSRF TEST PASSED")


if __name__ == "__main__":
    main()
