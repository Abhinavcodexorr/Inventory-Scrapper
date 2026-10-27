"""Telling "we are being blocked" apart from "we cannot read this layout".

The distinction is commercial, not cosmetic. A blocked site needs access work (a
proxy, a different egress region); an unsupported layout needs paid scraper
development. Reporting one as the other puts a wrong number in a client quote.

driftmotors.ca answers every request with a plain nginx/CloudFront `403` and no
vendor branding. Because a refusal status was only decisive when a vendor
fingerprint appeared alongside it, discovery walked six candidate URLs that were
all 403s, found no vehicles, and reported "layout not supported — quote custom
scraper" for a site that was simply refusing us.

Pure functions — no network, no browser.

Run:  python -m tests.test_antibot_classification
"""
from __future__ import annotations

from dealer_scraper.discovery.browser.antibot import classify

# The actual driftmotors.ca block page, trimmed.
_PLAIN_403_BODY = "403 - Forbidden 403 - Forbidden Access to this page is forbidden."
_CLOUDFRONT_HEADERS = {"server": "nginx", "via": "1.1 abc123.cloudfront.net (CloudFront)",
                       "x-cache": "Error from cloudfront"}


def test_unbranded_403_is_reported_as_blocked() -> None:
    """The driftmotors.ca case. No vendor name anywhere — just a refusal."""
    verdict = classify(status_code=403, headers=_CLOUDFRONT_HEADERS,
                       body_text=_PLAIN_403_BODY)
    assert verdict.blocked is True, (
        "a homepage we cannot load at all must never be reported as an "
        "unsupported layout — that quotes custom development for an access problem")


def test_every_refusal_status_is_decisive() -> None:
    for status in (403, 429, 503):
        assert classify(status_code=status, headers={}, body_text="Forbidden").blocked is True, status


def test_an_ordinary_page_is_not_blocked() -> None:
    body = ("Welcome to Bravo Motors. Browse our inventory of quality used "
            "vehicles. Financing available.")
    assert classify(status_code=200, headers={}, body_text=body).blocked is False


def test_a_footer_recaptcha_notice_does_not_block_a_healthy_dealer() -> None:
    """Regression on the earlier false positive: a 200 page merely mentioning
    reCAPTCHA in its privacy footer is a working site, not a wall."""
    body = ("2024 Honda Civic $28,900 · 2023 Toyota RAV4 $34,500 · "
            "This site is protected by reCAPTCHA and the Google Privacy Policy "
            "and Terms of Service apply.")
    assert classify(status_code=200, headers={}, body_text=body).blocked is False


def test_a_real_challenge_page_is_blocked() -> None:
    body = ("Checking your browser before accessing the site. "
            "Please verify you are human. Enable JavaScript and cookies to continue.")
    assert classify(status_code=200, headers={}, body_text=body).blocked is True


def test_missing_status_does_not_crash() -> None:
    assert classify(status_code=None, headers=None, body_text="").blocked is False


def main() -> None:
    test_unbranded_403_is_reported_as_blocked()
    test_every_refusal_status_is_decisive()
    test_an_ordinary_page_is_not_blocked()
    test_a_footer_recaptcha_notice_does_not_block_a_healthy_dealer()
    test_a_real_challenge_page_is_blocked()
    test_missing_status_does_not_crash()
    print("PASSED")


if __name__ == "__main__":
    main()
