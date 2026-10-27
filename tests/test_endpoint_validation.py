"""A config that can never be requested must fail at build time, by name.

`UnsupportedProtocol: Request URL is missing an 'http://' or 'https://'
protocol` came from a placeholder endpoint (DISCOVERED_FORMULA7_INVENTORY_
ENDPOINT) reaching httpx mid-scrape. These tests pin that empty, relative,
malformed and placeholder endpoints are rejected as ConfigError when the
adapter is BUILT and by validate_scrape_config at onboarding — before any
request exists — while every real endpoint shape keeps working.

Run:  python -m pytest tests/test_endpoint_validation.py
"""
from __future__ import annotations

import httpx
import pytest

from dealer_scraper.adapters.base import ConfigError
from dealer_scraper.adapters.json_api import JsonApiAdapter
from dealer_scraper.adapters.registry import validate_scrape_config
from dealer_scraper.urls import EndpointError, validate_endpoint

FIELD_MAP = {"external_id": "stock_NO", "make": "Vehicle.make"}


@pytest.mark.parametrize("url", [
    "https://22gautosales.com/_next/data/FEY-NIZVXw7p4PVRcEO0F/cars.json",
    "https://api.example.com/v1/vehicles?page={page}&limit={page_size}",
    "https://formula7motors.ca/_next/data/{build_id}/cars.json",
    "http://localhost:8080/feed",
    "https://x.example/srp?startIndex={page}&endIndex={page_end}",
])
def test_valid_absolute_urls_are_accepted(url: str) -> None:
    assert validate_endpoint(url) == url


@pytest.mark.parametrize("url, needle", [
    ("", "empty"),
    ("   ", "empty"),
    (None, "empty"),
    ("DISCOVERED_FORMULA7_INVENTORY_ENDPOINT", "placeholder"),
    ("https://formula7motors.ca/_next/data/<buildId>/cars.json", "placeholder"),
    ("https://formula7motors.ca/_next/data/{buildId}/cars.json", "{buildId}"),
    ("https://api.example.com/{dealer_id}/cars", "{dealer_id}"),
    ("https://api.example.com/cars?x={}", "empty {}"),
    ("https://api.example.com/cars/{page", "unbalanced"),
    ("https://YOUR_DEALER_HOST/cars.json", "placeholder"),
    ("/api/vehicles?page={page}", "relative"),
    ("formula7motors.ca/cars", "scheme"),
    ("ftp://formula7motors.ca/cars", "unsupported scheme"),
    ("https:///cars.json", "hostname"),
    ("https://{host}/cars.json", "{host}"),
    ("https://formula7motors/cars", "not a domain"),
])
def test_bad_endpoints_are_rejected_with_a_named_reason(url, needle) -> None:
    with pytest.raises(EndpointError) as exc:
        validate_endpoint(url)
    assert needle.lower() in str(exc.value).lower(), str(exc.value)


def test_relative_endpoint_resolves_against_url_base_only() -> None:
    assert validate_endpoint("/api/vehicles?page={page}", base_url="https://dealer.example") \
        == "https://dealer.example/api/vehicles?page={page}"
    with pytest.raises(EndpointError):
        validate_endpoint("/api/vehicles", base_url="DISCOVERED_BASE")


def test_adapter_rejects_placeholder_before_any_request(monkeypatch) -> None:
    sent = []
    monkeypatch.setattr(httpx.Client, "send", lambda self, *a, **k: sent.append(a))
    with pytest.raises(ConfigError, match="placeholder"):
        JsonApiAdapter({"endpoint": "DISCOVERED_FORMULA7_INVENTORY_ENDPOINT",
                        "field_map": FIELD_MAP})
    assert sent == []


def test_adapter_resolves_relative_endpoint_with_url_base() -> None:
    a = JsonApiAdapter({"endpoint": "/api/cars.json", "url_base": "https://dealer.example",
                        "field_map": FIELD_MAP, "use_proxy": False})
    assert a.endpoints == ["https://dealer.example/api/cars.json"]


def test_build_id_without_source_is_a_config_error() -> None:
    with pytest.raises(ConfigError, match="next_build_id_url"):
        JsonApiAdapter({"endpoint": "https://d.example/_next/data/{build_id}/cars.json",
                        "field_map": FIELD_MAP})


def test_onboarding_rejects_placeholder_endpoint() -> None:
    """Saving such a config must 422, so it can never replace a working one."""
    with pytest.raises(ValueError, match="placeholder"):
        validate_scrape_config("json_api", {"endpoint": "DISCOVERED_FORMULA7_INVENTORY_ENDPOINT",
                                            "field_map": FIELD_MAP}, "https://formula7motors.ca")


@pytest.mark.parametrize("template", [
    "{url}", "https://{host}/x/{slug}.json", "/_next/data/{build_id}{slug}.json",
])
def test_detail_url_host_must_be_literal(template: str) -> None:
    with pytest.raises(ConfigError):
        JsonApiAdapter({"endpoint": "https://d.example/cars.json", "field_map": FIELD_MAP,
                        "detail": {"index_path": "ids", "key": "id", "url": template}})


def main() -> None:
    """`python -m tests.test_endpoint_validation` — several tests take pytest fixtures
    (parametrize, monkeypatch, caplog), so each named test runs through pytest."""
    import sys

    import pytest as _pytest

    tests = (
        test_valid_absolute_urls_are_accepted,
        test_bad_endpoints_are_rejected_with_a_named_reason,
        test_relative_endpoint_resolves_against_url_base_only,
        test_adapter_rejects_placeholder_before_any_request,
        test_adapter_resolves_relative_endpoint_with_url_base,
        test_build_id_without_source_is_a_config_error,
        test_onboarding_rejects_placeholder_endpoint,
        test_detail_url_host_must_be_literal,
    )
    sys.exit(_pytest.main([__file__, "-q", "-p", "no:cacheprovider",
                           "-k", " or ".join(t.__name__ for t in tests)]))


if __name__ == "__main__":
    main()
