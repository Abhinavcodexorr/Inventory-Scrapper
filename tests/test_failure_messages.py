"""A failed run's error says what actually went wrong and what to do.

universalauto.ca served its host's "Account Suspended" page and the run blamed
bot protection. etobicokefinecars.com's saved settings had no API key and the
run showed only a raw 401.

Run:  python -m pytest tests/test_failure_messages.py
"""
from __future__ import annotations

import httpx

from dealer_scraper.adapters.base import suspended_site_error
from dealer_scraper.engine import _failure_message


def test_suspended_page_is_recognised_from_its_url_or_text() -> None:
    url = "https://universalauto.ca/cgi-sys/suspendedpage.cgi?page=1"
    assert "hosting account is suspended" in suspended_site_error(url, "", "")
    assert suspended_site_error("https://x.ca/cars", "Account Suspended", "")
    assert suspended_site_error("https://x.ca/cars", "", "This Account has been suspended.")


def test_a_normal_page_is_not_called_suspended() -> None:
    assert suspended_site_error("https://x.ca/cars", "Used Cars", "Suspension parts on sale") is None


def _status_error(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://api.example.com/vehicles")
    response = httpx.Response(status, request=request)
    return httpx.HTTPStatusError(f"{status}", request=request, response=response)


def test_401_tells_the_operator_to_detect_settings_again() -> None:
    message = _failure_message(_status_error(401))
    assert "Detect settings" in message
    assert "https://api.example.com/vehicles" in message


def test_other_failures_keep_their_original_text() -> None:
    assert _failure_message(_status_error(500)) == "HTTPStatusError: 500"
    assert _failure_message(RuntimeError("boom")) == "RuntimeError: boom"


def main() -> None:
    test_suspended_page_is_recognised_from_its_url_or_text()
    test_a_normal_page_is_not_called_suspended()
    test_401_tells_the_operator_to_detect_settings_again()
    test_other_failures_keep_their_original_text()
    print("failure message tests passed")


if __name__ == "__main__":
    main()
