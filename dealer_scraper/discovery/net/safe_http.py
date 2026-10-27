"""SSRF-hardened HTTP client for discovery's outbound fetches.

`base_url` and every URL a target site's own robots.txt/sitemap points us to
are attacker-influenced input that this server then fetches. Without this
module, an operator (or anyone who can reach the API) can point discovery at
`http://169.254.169.254/` and read cloud IAM credentials back through the
run-report endpoint, or at `http://localhost:8000/internal` to reach the API
process itself.

`validate_public_url` rejects anything that isn't a plain http(s) URL
resolving to a public, routable IP address — no loopback, private (RFC1918),
link-local, multicast, reserved, or unspecified address, and no non-standard
port. `SafeTransport` re-applies that check on EVERY hop of a redirect chain,
not just the first request, because a same-site 200 that then 302s to an
internal host is exactly the bypass a first-request-only check would miss.

DNS-rebinding note: there is an inherent TOCTOU gap between the
`socket.getaddrinfo()` call here and httpx's own subsequent connect — an
attacker with control of DNS and a very short TTL could theoretically swap
the resolved address between the two. Closing that fully requires connecting
to the pinned IP while keeping the original Host/SNI, which httpx does not
expose cleanly. Per-hop revalidation (this module) closes the practical
attack (redirect to an internal host) and is a deliberate, documented
trade-off — the durable fix for the rebinding gap is an egress allowlist at
the infra layer, not inside this process.
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

import httpx

from ...proxy import configured_proxy

_DEFAULT_UA = "InventoryBot/1.0 (+contact@brainspack.com) discovery"
_ALLOWED_SCHEMES = frozenset({"http", "https"})
_DEFAULT_ALLOWED_PORTS = frozenset({80, 443})
# Cloud metadata endpoints — these are technically "link-local" or otherwise
# already caught by the IP check below, but an explicit denylist survives
# even if a platform's metadata service ever moves to a routable-looking
# address, and it makes the intent legible without tracing IP-range math.
_BLOCKED_HOSTNAMES = frozenset({
    "169.254.169.254",
    "metadata.google.internal",
    "metadata.goog",
})


class UnsafeUrlError(ValueError):
    """Raised when a URL is rejected as an SSRF risk."""


class ResponseTooLargeError(ValueError):
    """Raised by read_capped() when a response body exceeds its byte cap."""


def _is_public_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address):
        mapped = ip.ipv4_mapped
        if mapped is not None:
            return _is_public_ip(mapped)
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def validate_public_url(
    url: str, *, allowed_ports: frozenset[int] = _DEFAULT_ALLOWED_PORTS,
) -> str:
    """Raise UnsafeUrlError unless `url` is http(s), carries no embedded
    credentials, uses an allowed port, and resolves ONLY to public IP
    addresses. Returns `url` unchanged on success (so it composes in a
    validator chain: `return validate_public_url(v)`).
    """
    parts = urlsplit(url)
    if parts.scheme.lower() not in _ALLOWED_SCHEMES:
        raise UnsafeUrlError(f"Unsupported URL scheme {parts.scheme!r} — only http/https are allowed.")
    if parts.username or parts.password:
        raise UnsafeUrlError("URLs with embedded credentials (user:pass@host) are not allowed.")

    host = parts.hostname
    if not host:
        raise UnsafeUrlError("URL has no host.")
    if host.lower() in _BLOCKED_HOSTNAMES:
        raise UnsafeUrlError(f"'{host}' is a blocked metadata/internal endpoint.")

    port = parts.port or (443 if parts.scheme.lower() == "https" else 80)
    if port not in allowed_ports:
        raise UnsafeUrlError(f"Port {port} is not allowed.")

    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        # Non-resolvable host — not an SSRF risk (at worst, a typo).
        # The eventual HTTP request will fail cleanly when connection is attempted.
        return url
    if not infos:
        return url

    for _family, _type, _proto, _canon, sockaddr in infos:
        try:
            ip = ipaddress.ip_address(sockaddr[0])
        except ValueError as exc:
            raise UnsafeUrlError(f"Unparseable resolved address for '{host}': {sockaddr[0]}") from exc
        if not _is_public_ip(ip):
            raise UnsafeUrlError(f"Host '{host}' resolves to a non-public address ({ip}).")

    return url


class SafeTransport(httpx.HTTPTransport):
    """httpx.HTTPTransport that re-validates the target on EVERY request it
    handles. httpx re-enters the transport for each hop of a redirect chain
    (it does not just follow Location headers blindly at a higher layer), so
    this closes the SSRF path a check on only the initial URL would miss.
    """

    def __init__(self, *, allowed_ports: frozenset[int] = _DEFAULT_ALLOWED_PORTS, **kwargs) -> None:
        super().__init__(**kwargs)
        self._allowed_ports = allowed_ports

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        validate_public_url(str(request.url), allowed_ports=self._allowed_ports)
        return super().handle_request(request)


def safe_client(
    *, timeout: float = 10.0, headers: dict[str, str] | None = None,
    follow_redirects: bool = True, max_redirects: int = 3,
) -> httpx.Client:
    """The one hardened httpx.Client discovery's own outbound fetches should
    use: SSRF validation on every hop, discovery's proxy (closing the leak
    where sitemap/pagination-probe traffic bypassed SCRAPER_PROXY and
    disclosed the server's real IP to every discovered host), and a bounded
    redirect chain.
    """
    merged_headers = {"User-Agent": _DEFAULT_UA}
    if headers:
        merged_headers.update(headers)
    # The proxy goes INTO SafeTransport, not to Client(proxy=...): httpx mounts
    # a client-level proxy as its own transport for "all://", which silently
    # replaced SafeTransport and skipped SSRF validation whenever a proxy was
    # configured. trust_env=False for the same reason — an HTTP(S)_PROXY in the
    # host environment would be mounted the same way.
    proxy = configured_proxy()
    return httpx.Client(
        transport=SafeTransport(proxy=proxy.httpx_proxy() if proxy else None),
        timeout=timeout,
        follow_redirects=follow_redirects,
        max_redirects=max_redirects,
        trust_env=False,
        headers=merged_headers,
    )


def read_capped(response: httpx.Response, max_bytes: int) -> bytes:
    """Read a response body up to max_bytes, ABORTING rather than silently
    truncating if the server sends more. Content-Length is not trustworthy —
    chunked or gzip-compressed responses commonly omit it — so this caps the
    actual bytes read regardless of what headers claim.
    """
    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_bytes():
        total += len(chunk)
        if total > max_bytes:
            raise ResponseTooLargeError(
                f"Response body exceeded the {max_bytes}-byte cap while reading {response.url}."
            )
        chunks.append(chunk)
    return b"".join(chunks)
