"""CMS fingerprints: heuristic signals that identify which platform powers a dealer site.

Common dealer platforms: Dealer Inspire, DealerOn, Dealer.com, DealerSocket, CDK,
Dealer Spike, DealerFire, HomeNet, Cox Automotive, and direct integrations.

Fingerprints are used to seed priors (prior likelihoods for certain API shapes)
and optimize discovery strategies, but never bypass verification.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache


@dataclass
class CmsSignal:
    """A single heuristic signal indicating a CMS platform."""
    platform: str
    signal: str  # e.g. "meta_generator", "api_url_pattern", "js_library"
    pattern: str  # REGEX, matched case-insensitively — see `regex`
    weight: float = 0.5  # 0..1; higher = stronger signal

    @property
    def regex(self) -> re.Pattern:
        """Compiled form of `pattern`.

        These were always written as regexes (`CDK|Cobalt`, `homenet\\.com|moto\\.com`)
        but matched with a substring `in` test, so every pattern containing an
        alternation or an escape could never fire — that silently disabled all of
        CDK and HomeNet plus most of the API-URL signals. Matching through the
        compiled pattern is what makes the table mean what it says.
        """
        return _compile(self.pattern)


#: `content` of any <meta name="generator"> / <meta property="generator"> tag,
#: in either attribute order.
_GENERATOR_RE = re.compile(
    r"""<meta[^>]*?(?:name|property)\s*=\s*["']generator["'][^>]*?"""
    r"""content\s*=\s*["']([^"']*)["']"""
    r"""|<meta[^>]*?content\s*=\s*["']([^"']*)["'][^>]*?"""
    r"""(?:name|property)\s*=\s*["']generator["']""",
    re.IGNORECASE | re.VERBOSE,
)


def _find_generators(html: str) -> list[str]:
    return [a or b for a, b in _GENERATOR_RE.findall(html)]


@lru_cache(maxsize=256)
def _compile(pattern: str) -> re.Pattern:
    try:
        return re.compile(pattern, re.IGNORECASE)
    except re.error:
        # A malformed entry must degrade to a literal match, never take down
        # discovery for every site.
        return re.compile(re.escape(pattern), re.IGNORECASE)


# Known CMS fingerprints
DEALER_INSPIRE_SIGNALS = [
    CmsSignal("dealer_inspire", "meta_generator", r"Dealer Inspire", weight=0.9),
    CmsSignal("dealer_inspire", "api_url", r"/api/inventory", weight=0.6),
    CmsSignal("dealer_inspire", "js_library", r"jquery", weight=0.2),
]

DEALERON_SIGNALS = [
    CmsSignal("dealeron", "meta_generator", r"DealerOn", weight=0.9),
    CmsSignal("dealeron", "api_url", r"api\.dealeron\.com", weight=0.8),
    CmsSignal("dealeron", "html_comment", r"DealerOn", weight=0.6),
]

DEALER_COM_SIGNALS = [
    CmsSignal("dealer_com", "meta_generator", r"Dealer\.com", weight=0.9),
    CmsSignal("dealer_com", "api_url", r"inventory\.dealer\.com", weight=0.8),
    CmsSignal("dealer_com", "html_pattern", r"dealercom", weight=0.5),
]

DEALERSOCKET_SIGNALS = [
    CmsSignal("dealersocket", "meta_generator", r"DealerSocket", weight=0.9),
    CmsSignal("dealersocket", "api_url", r"api\.dealersocket", weight=0.8),
    CmsSignal("dealersocket", "js_library", r"dealersocket", weight=0.6),
]

CDK_SIGNALS = [
    # Not a bare "Cobalt": Chevrolet sold a car called the Cobalt, so a used-car
    # listing page would fingerprint every dealer on the lot as CDK.
    CmsSignal("cdk", "meta_generator", r"CDK\s*(?:Global|Digital)?|Cobalt\s*Group", weight=0.9),
    CmsSignal("cdk", "api_url", r"dealer-api\.cdk|cobaltgroup\.com", weight=0.8),
    CmsSignal("cdk", "html_pattern", r"cdk-tech|cdkglobal|cobaltgroup", weight=0.5),
]

DEALER_SPIKE_SIGNALS = [
    CmsSignal("dealer_spike", "meta_generator", r"Dealer Spike", weight=0.9),
    CmsSignal("dealer_spike", "api_url", r"dealerspike\.com", weight=0.8),
    CmsSignal("dealer_spike", "html_pattern", r"dealerspike", weight=0.5),
]

DEALERFIRE_SIGNALS = [
    CmsSignal("dealerfire", "meta_generator", r"DealerFire", weight=0.9),
    CmsSignal("dealerfire", "api_url", r"dealerfire\.com", weight=0.8),
    CmsSignal("dealerfire", "html_pattern", r"dealerfire", weight=0.5),
]

HOMENET_SIGNALS = [
    # "Moto" on its own matched Motors, Motorsports and "motor vehicle", so every
    # dealership with Motors in its name scored 0.9 as HomeNet. MotoSnap is the
    # actual HomeNet product name.
    CmsSignal("homenet", "meta_generator", r"HomeNet|MotoSnap", weight=0.9),
    CmsSignal("homenet", "api_url", r"homenet\.com|motosnap\.com", weight=0.8),
    CmsSignal("homenet", "html_pattern", r"homenet|motosnap", weight=0.5),
]

# Map platform → signals
CMS_SIGNALS_MAP = {
    "dealer_inspire": DEALER_INSPIRE_SIGNALS,
    "dealeron": DEALERON_SIGNALS,
    "dealer_com": DEALER_COM_SIGNALS,
    "dealersocket": DEALERSOCKET_SIGNALS,
    "cdk": CDK_SIGNALS,
    "dealer_spike": DEALER_SPIKE_SIGNALS,
    "dealerfire": DEALERFIRE_SIGNALS,
    "homenet": HOMENET_SIGNALS,
}


def detect_cms_platform(html: str, response_headers: dict | None = None) -> dict[str, float]:
    """Detect CMS platform from HTML and response headers.

    Returns a dict of platform → confidence score (0..1).
    """
    scores: dict[str, float] = {}
    html = html or ""

    #: How much of a signal's weight each kind contributes. A generator meta tag
    #: is close to proof; a shared JS library is barely a hint.
    kind_multiplier = {
        "meta_generator": 1.0,
        "api_url": 0.8,
        "html_pattern": 0.6,
        "html_comment": 0.6,
        "js_library": 0.3,
    }
    # A generator signal carries almost full weight, so it must be read from the
    # generator tag rather than anywhere in the document — otherwise a platform
    # name mentioned in body copy ("proudly built by …", a car model name) decides
    # the verdict, and the dealer inherits a config copied from an unrelated site.
    generator = " ".join(_find_generators(html))

    for platform, signals in CMS_SIGNALS_MAP.items():
        for signal in signals:
            multiplier = kind_multiplier.get(signal.signal)
            if multiplier is None:
                continue
            haystack = generator if signal.signal == "meta_generator" else html
            if not haystack or not signal.regex.search(haystack):
                continue
            scores[platform] = max(scores.get(platform, 0), signal.weight * multiplier)

    if response_headers:
        # Platforms routinely announce themselves in headers (x-powered-by,
        # server, x-cache) even when the HTML is white-labelled.
        header_blob = "\n".join(
            f"{name}: {value}" for name, value in response_headers.items()
        )
        for platform, signals in CMS_SIGNALS_MAP.items():
            for signal in signals:
                if signal.regex.search(header_blob):
                    scores[platform] = max(scores.get(platform, 0), signal.weight * 0.7)

    return scores


def get_most_likely_platform(scores: dict[str, float]) -> str | None:
    """Get the platform with highest confidence from scores."""
    if not scores:
        return None
    platform = max(scores, key=scores.get)
    if scores[platform] >= 0.5:  # Minimum confidence threshold
        return platform
    return None
