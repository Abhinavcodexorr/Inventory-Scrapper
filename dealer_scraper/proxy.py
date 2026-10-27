"""One place that turns the configured proxy into what each client needs.

Every outbound path (json_api's httpx client, discovery's safe_client, the
Playwright adapters, scripts/proxy_check.py) used to read SCRAPER_PROXY and
interpret it on its own. Two of them handed the full `user:pass@host` URL to
Playwright as `server`, which Playwright does not authenticate from — so the
same credentials worked in one adapter and came back as a 407 in another.

Credentials can come from the URL (percent-encoded: `p%40ss` for `p@ss`) or
from SCRAPER_PROXY_USERNAME / SCRAPER_PROXY_PASSWORD, which take precedence and
need no encoding. Nothing here is provider-specific: session or country
parameters a provider embeds in the username pass through untouched.

Secrets never leave this module in printable form — use `redacted` / `redact()`
for anything that reaches a log, an exception message or a run's error column.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import unquote, urlsplit

_SCHEMES = frozenset({"http", "https", "socks5", "socks5h"})


class ProxyConfigError(ValueError):
    """The proxy setting cannot be parsed into a usable proxy. The message never
    contains the password."""


class ProxyAuthError(RuntimeError):
    """The proxy answered 407: it rejected our credentials (or this machine's IP,
    for providers that authenticate by allowlist). Retrying cannot fix it, so
    callers must not retry — and must not report it as the target site blocking us.
    """


@dataclass(frozen=True)
class ProxySettings:
    scheme: str
    host: str
    port: int | None
    username: str | None = None
    password: str | None = field(default=None, repr=False)

    @property
    def server(self) -> str:
        """scheme://host[:port], no credentials."""
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"{self.scheme}://{host}" + (f":{self.port}" if self.port else "")

    @property
    def redacted(self) -> str:
        """Safe to log: shows whether credentials are present, never their value."""
        if self.username is None and self.password is None:
            return self.server
        return self.server.replace("://", "://***:***@", 1)

    def httpx_proxy(self):
        """httpx.Proxy with credentials passed explicitly rather than re-parsed
        out of a URL, so reserved characters in them cannot be misread."""
        import httpx

        auth = (self.username or "", self.password or "") if self.username is not None else None
        return httpx.Proxy(url=self.server, auth=auth)

    def playwright(self) -> dict[str, str]:
        out = {"server": self.server}
        if self.username is not None:
            out["username"] = self.username
        if self.password is not None:
            out["password"] = self.password
        return out

    def redact(self, text: str) -> str:
        """Scrub this proxy's password (raw and percent-encoded) out of `text`."""
        if not text or not self.password:
            return text
        from urllib.parse import quote

        for secret in {self.password, quote(self.password, safe="")}:
            text = text.replace(secret, "***")
        return text


def resolve_proxy(
    proxy_url: str | None,
    username: str | None = None,
    password: str | None = None,
) -> ProxySettings | None:
    """Parse a proxy URL (plus optional separate credentials) into ProxySettings.

    Returns None when no proxy is configured. Raises ProxyConfigError — with a
    message that never repeats the password — when one is configured but cannot
    be what the operator meant.
    """
    if not proxy_url or not proxy_url.strip():
        return None
    raw = proxy_url.strip()
    if "://" not in raw:
        raise ProxyConfigError(
            "SCRAPER_PROXY has no scheme. Expected scheme://[user:pass@]host:port, "
            "e.g. http://gate.example.com:7000 (check your provider's documentation "
            "for http vs socks5)."
        )
    parts = urlsplit(raw)
    scheme = parts.scheme.lower()
    if scheme not in _SCHEMES:
        raise ProxyConfigError(
            f"SCRAPER_PROXY scheme '{scheme}' is not supported; use one of "
            f"{', '.join(sorted(_SCHEMES))}."
        )
    # A raw '/', '?' or '#' inside the password ends the authority early, so the
    # parsed host is really a fragment of the credentials. Detect that instead of
    # dialing a nonsense host (or worse, echoing the password as a "hostname").
    netloc = parts.netloc
    if "@" in raw.split("://", 1)[1] and "@" not in netloc:
        raise ProxyConfigError(
            "SCRAPER_PROXY credentials contain a reserved character ('/', '?' or '#') "
            "that is not percent-encoded, so the URL cannot be parsed. Percent-encode "
            "the password (e.g. '/' -> %2F, '#' -> %23) or set it separately in "
            "SCRAPER_PROXY_PASSWORD."
        )
    try:
        port = parts.port
    except ValueError:
        raise ProxyConfigError(
            "SCRAPER_PROXY has an invalid port. If the password contains ':' or '@', "
            "percent-encode it or set it in SCRAPER_PROXY_PASSWORD."
        ) from None
    host = parts.hostname
    if not host:
        raise ProxyConfigError("SCRAPER_PROXY has no hostname.")

    user = unquote(parts.username) if parts.username is not None else None
    pw = unquote(parts.password) if parts.password is not None else None
    if username:
        user = username
    if password:
        pw = password
    if pw is not None and user is None:
        raise ProxyConfigError("A proxy password is set but no username.")
    return ProxySettings(scheme=scheme, host=host, port=port, username=user, password=pw)


def configured_proxy() -> ProxySettings | None:
    """The application-level proxy from settings (SCRAPER_PROXY[_USERNAME|_PASSWORD])."""
    from .config import settings

    password = settings.scraper_proxy_password
    return resolve_proxy(
        settings.scraper_proxy,
        settings.scraper_proxy_username,
        password.get_secret_value() if password is not None else None,
    )


def redact_url(url: str | None) -> str:
    """Mask the password in any URL (proxy or database) for display."""
    if not url:
        return ""
    try:
        parts = urlsplit(url)
        if parts.password is None:
            return url
        userinfo, _, hostport = parts.netloc.rpartition("@")
        user = userinfo.split(":", 1)[0]
        return parts._replace(netloc=f"{user}:***@{hostport}").geturl()
    except ValueError:
        return "<unparseable url>"


def is_proxy_auth_failure(exc: BaseException) -> bool:
    """True for httpx's CONNECT-tunnel 407 (`ProxyError: 407 Proxy Authentication
    Required`) — the shape this error takes for every https target."""
    import httpx

    return isinstance(exc, httpx.ProxyError) and "407" in str(exc)
