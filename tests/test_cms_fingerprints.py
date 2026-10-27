"""CMS platform fingerprinting — the signal that makes config reuse automatic.

The signal table was always written as regexes (`CDK|Cobalt`,
`homenet\\.com|moto\\.com`) but matched with a substring `in` test, so every
pattern containing an alternation or an escape could never fire. That silently
disabled all of CDK and HomeNet plus most API-URL signals, and since the module
was never wired into discovery, nothing ever noticed.

Run:  python -m tests.test_cms_fingerprints
"""
from __future__ import annotations

import re

from dealer_scraper.discovery.cms.fingerprints import (
    CMS_SIGNALS_MAP,
    CmsSignal,
    detect_cms_platform,
    get_most_likely_platform,
)


def test_alternation_patterns_match() -> None:
    """`CDK|Cobalt` is a regex. Substring-matching it can never hit."""
    scores = detect_cms_platform('<meta name="generator" content="CDK Global">')
    assert get_most_likely_platform(scores) == "cdk"

    scores = detect_cms_platform('<meta name="generator" content="Cobalt Group">')
    assert get_most_likely_platform(scores) == "cdk"


def test_escaped_dot_patterns_match() -> None:
    """`api\\.dealeron\\.com` contains backslashes that a literal test never sees."""
    scores = detect_cms_platform(
        '<script src="https://api.dealeron.com/v1/inventory.js"></script>')
    assert get_most_likely_platform(scores) == "dealeron"

    scores = detect_cms_platform('<img src="https://homenet.com/photos/1.jpg">')
    assert get_most_likely_platform(scores) == "homenet"


def test_plain_platform_names_still_match() -> None:
    """The signals that DID work before must keep working."""
    for html, expected in (
        ('<meta name="generator" content="Dealer Inspire">', "dealer_inspire"),
        ('<meta name="generator" content="DealerOn">', "dealeron"),
        ('<meta name="generator" content="Dealer.com">', "dealer_com"),
        ('<meta name="generator" content="DealerSocket">', "dealersocket"),
        ('<meta name="generator" content="Dealer Spike">', "dealer_spike"),
        ('<meta name="generator" content="DealerFire">', "dealerfire"),
    ):
        assert get_most_likely_platform(detect_cms_platform(html)) == expected, html


def test_matching_is_case_insensitive() -> None:
    for content in ("DEALERON", "dealeron", "DealerOn"):
        html = f'<meta name="GENERATOR" content="{content}">'
        assert get_most_likely_platform(detect_cms_platform(html)) == "dealeron", html


def test_headers_can_identify_a_white_labelled_site() -> None:
    """Platforms announce themselves in headers even when the HTML is rebranded."""
    scores = detect_cms_platform("<html><body>Welcome</body></html>",
                                  {"x-powered-by": "DealerFire"})
    assert get_most_likely_platform(scores) == "dealerfire"


def test_an_ordinary_site_is_not_misidentified() -> None:
    """A false platform is worse than none — it would hand the dealer a config
    copied from an unrelated site."""
    html = ("<html><head><title>Joe's Used Cars</title></head>"
            "<body><h1>Great deals on used cars</h1>"
            "<a href='/inventory/2020-honda-civic/123'>Honda Civic</a></body></html>")
    assert get_most_likely_platform(detect_cms_platform(html)) is None


def test_dealership_named_motors_is_not_homenet() -> None:
    """`HomeNet|Moto` matched Motors / Motorsports / "motor vehicle", scoring 0.9
    on the plain name of a huge share of car dealerships. Wiring fingerprinting
    up with that in place would have copied HomeNet configs onto unrelated sites.
    """
    for html in (
        "<html><title>Bravo Motors - Used Cars</title></html>",
        "<html><body>Welcome to Riverside Motorsports</body></html>",
        "<html><body>We service every motor vehicle</body></html>",
        "<html><body>Great cars at Alpha Motors Ltd</body></html>",
    ):
        assert get_most_likely_platform(detect_cms_platform(html)) is None, html


def test_a_chevrolet_cobalt_listing_is_not_cdk() -> None:
    """Chevrolet sold a car called the Cobalt; `CDK|Cobalt` fingerprinted the
    listing page of any dealer with one on the lot."""
    html = "<html><body><h2>2010 Chevrolet Cobalt LT</h2><p>Cobalt Blue</p></body></html>"
    assert get_most_likely_platform(detect_cms_platform(html)) is None


def test_generator_signals_only_read_the_generator_tag() -> None:
    """A generator signal is worth near-full weight, so body copy must not carry
    it — otherwise 'site proudly built by DealerOn' in a footer decides it."""
    body_mention = "<html><body>Our old site was built by DealerOn</body></html>"
    assert get_most_likely_platform(detect_cms_platform(body_mention)) is None

    real_tag = '<html><head><meta name="generator" content="DealerOn"></head></html>'
    assert get_most_likely_platform(detect_cms_platform(real_tag)) == "dealeron"


def test_generator_tag_attribute_order_does_not_matter() -> None:
    reversed_attrs = '<meta content="Dealer Inspire" name="generator">'
    assert get_most_likely_platform(detect_cms_platform(reversed_attrs)) == "dealer_inspire"


def test_empty_input_is_safe() -> None:
    assert detect_cms_platform("") == {}
    assert detect_cms_platform("", {}) == {}
    assert get_most_likely_platform({}) is None


def test_weak_signal_alone_stays_below_the_threshold() -> None:
    """jQuery is on half the web; it must never decide a platform on its own."""
    scores = detect_cms_platform("<script src='/js/jquery.min.js'></script>")
    assert get_most_likely_platform(scores) is None


def test_every_signal_pattern_compiles() -> None:
    for platform, signals in CMS_SIGNALS_MAP.items():
        for signal in signals:
            try:
                re.compile(signal.pattern)
            except re.error as exc:
                raise AssertionError(f"{platform}/{signal.signal}: {exc}") from exc


def test_malformed_pattern_degrades_to_a_literal() -> None:
    """One bad table entry must not take discovery down for every site."""
    bad = CmsSignal("x", "meta_generator", "unclosed (group", weight=1.0)
    assert bad.regex.search("unclosed (group") is not None


def main() -> None:
    test_alternation_patterns_match()
    test_escaped_dot_patterns_match()
    test_plain_platform_names_still_match()
    test_matching_is_case_insensitive()
    test_headers_can_identify_a_white_labelled_site()
    test_an_ordinary_site_is_not_misidentified()
    test_dealership_named_motors_is_not_homenet()
    test_a_chevrolet_cobalt_listing_is_not_cdk()
    test_generator_signals_only_read_the_generator_tag()
    test_generator_tag_attribute_order_does_not_matter()
    test_empty_input_is_safe()
    test_weak_signal_alone_stays_below_the_threshold()
    test_every_signal_pattern_compiles()
    test_malformed_pattern_degrades_to_a_literal()
    print("PASSED")


if __name__ == "__main__":
    main()
