"""Endpoint validation that runs BEFORE any request is made.

Without it, a config whose endpoint is "" or a leftover placeholder such as
`DISCOVERED_FORMULA7_INVENTORY_ENDPOINT` reached httpx and surfaced mid-scrape
as `UnsupportedProtocol: Request URL is missing an 'http://' or 'https://'
protocol` — accurate, but it names neither the dealer's config key nor the fix.

Pure: no network, no settings. Raises EndpointError (a ValueError, so the API's
existing validation path turns it into a 422 at onboarding).
"""
from __future__ import annotations

import re
from string import Formatter
from urllib.parse import urljoin, urlsplit

#: Placeholders json_api substitutes itself; anything else in braces is a
#: template variable nobody will fill in.
TEMPLATE_FIELDS = frozenset({"page", "page_size", "page_end", "build_id"})

# Markers people leave in hand-edited or generated configs. Matched against the
# URL with its legitimate {page}-style fields removed.
_PLACEHOLDER_RE = re.compile(
    r"<[^<>]*>"                                   # <buildId>, <your-endpoint>
    r"|\b(?:DISCOVERED|PLACEHOLDER|REPLACE_?ME|CHANGE_?ME|YOUR|TODO|TBD|FIXME)_[A-Z0-9_]*"
    r"|\b(?:PLACEHOLDER|REPLACE_?ME|CHANGE_?ME|TODO|TBD|FIXME)\b"
)
_BARE_TOKEN_RE = re.compile(r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+$")
_HOST_RE = re.compile(r"^[A-Za-z0-9.-]+$|^\[[0-9A-Fa-f:.]+\]$")


class EndpointError(ValueError):
    """A configured URL can never be requested as written."""


def template_fields(url: str) -> set[str]:
    """Names of the {field} placeholders in `url` (format-spec syntax)."""
    try:
        return {name for _, name, _, _ in Formatter().parse(url) if name is not None}
    except ValueError as exc:  # unbalanced braces
        raise EndpointError(f"has unbalanced braces ({exc}): {url[:120]}") from None


def validate_endpoint(
    url: object,
    *,
    key: str = "endpoint",
    base_url: str | None = None,
    allowed_fields: frozenset[str] = TEMPLATE_FIELDS,
) -> str:
    """Return `url` as an absolute http(s) URL template, or raise EndpointError.

    A root-relative URL ("/api/vehicles?page={page}") is resolved against
    `base_url` when one is supplied (json_api's `url_base`); with no base it is
    rejected rather than guessed at.
    """
    if not isinstance(url, str) or not url.strip():
        raise EndpointError(f"scrape_config.{key} is empty — set it to the absolute URL of the inventory feed.")
    url = url.strip()

    unknown = template_fields(url) - allowed_fields
    if "" in unknown:
        raise EndpointError(f"scrape_config.{key} contains an empty {{}} placeholder: {url[:120]}")
    if unknown:
        raise EndpointError(
            f"scrape_config.{key} contains unresolved placeholder(s) "
            f"{', '.join('{' + u + '}' for u in sorted(unknown))}; only "
            f"{', '.join('{' + f + '}' for f in sorted(allowed_fields))} are filled in at "
            f"request time: {url[:120]}"
        )
    literal = re.sub(r"\{[a-z_]+\}", "", url)
    if _BARE_TOKEN_RE.match(url) or _PLACEHOLDER_RE.search(literal):
        raise EndpointError(
            f"scrape_config.{key} is an unresolved placeholder, not a URL: {url[:120]}. "
            f"Replace it with the verified endpoint before enabling this dealer."
        )

    if url.startswith("/") and not url.startswith("//"):
        if not base_url:
            raise EndpointError(
                f"scrape_config.{key} is a relative URL ({url[:80]}) and no url_base is "
                f"set to resolve it against. Use the absolute URL."
            )
        base = validate_endpoint(base_url, key="url_base", allowed_fields=frozenset())
        url = urljoin(base.rstrip("/") + "/", url.lstrip("/"))

    parts = urlsplit(url)
    if parts.scheme.lower() not in ("http", "https"):
        if not parts.scheme or "://" not in url:
            raise EndpointError(
                f"scrape_config.{key} is missing an http:// or https:// scheme: {url[:120]}"
            )
        raise EndpointError(f"scrape_config.{key} uses unsupported scheme '{parts.scheme}': {url[:120]}")
    host = parts.netloc.rsplit("@", 1)[-1]
    host = host.rsplit(":", 1)[0] if not host.endswith("]") else host
    if not host or not _HOST_RE.match(host) or "{" in parts.netloc:
        raise EndpointError(f"scrape_config.{key} has no valid hostname: {url[:120]}")
    if "." not in host and host != "localhost" and not host.startswith("["):
        raise EndpointError(f"scrape_config.{key} hostname '{host}' is not a domain name: {url[:120]}")
    return url


def validate_request_url(url: str) -> str:
    """Last check on a fully rendered URL, immediately before it is sent."""
    return validate_endpoint(url, key="request URL", allowed_fields=frozenset())
