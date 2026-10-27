"""Politeness: robots.txt respect + per-host token bucket rate limiting.

Discovery probes can be aggressive; this module enforces rate limits and
respects robots.txt exclusions to avoid hammering production sites.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse

import httpx


@dataclass
class TokenBucket:
    """Simple token bucket for per-host rate limiting.

    Allows burst of `capacity` requests, then refills at `rate` per second.
    Used to prevent discovery from hammering a single host.
    """
    capacity: int
    rate: float  # tokens per second
    tokens: float = field(default=None)
    last_refill: float = field(default=None)

    def __post_init__(self):
        if self.tokens is None:
            self.tokens = float(self.capacity)
        if self.last_refill is None:
            self.last_refill = time.time()

    def acquire(self, count: int = 1) -> bool:
        """Try to acquire tokens. Returns True if successful."""
        self._refill()
        if self.tokens >= count:
            self.tokens -= count
            return True
        return False

    def wait_available(self, count: int = 1, timeout_seconds: float = 5.0) -> bool:
        """Wait for tokens to become available, up to timeout."""
        start = time.time()
        while time.time() - start < timeout_seconds:
            if self.acquire(count):
                return True
            time.sleep(0.1)
        return False

    def _refill(self):
        """Refill bucket based on elapsed time since last refill."""
        now = time.time()
        elapsed = now - self.last_refill
        self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
        self.last_refill = now


class RobotsChecker:
    """Minimal robots.txt parser and matcher.

    For discovery, we check if a path is disallowed for user agents
    ("*" matches all). We don't implement crawl-delay or request-rate,
    just presence/absence of paths.
    """

    def __init__(self, robots_txt: str | None = None):
        self.disallowed_paths: list[str] = []
        if robots_txt:
            self._parse_robots_txt(robots_txt)

    def _parse_robots_txt(self, robots_txt: str):
        """Parse robots.txt and extract disallowed paths for user-agent *."""
        lines = robots_txt.split("\n")
        current_agent = None
        in_section = False

        for line in lines:
            line = line.split("#")[0].strip()  # Remove comments
            if not line:
                continue

            if line.lower().startswith("user-agent:"):
                agent = line.split(":", 1)[1].strip()
                current_agent = agent
                in_section = (agent == "*")
                continue

            if in_section and line.lower().startswith("disallow:"):
                path = line.split(":", 1)[1].strip()
                if path:
                    self.disallowed_paths.append(path)

    def is_allowed(self, url: str) -> bool:
        """Check if a URL path is allowed by robots.txt."""
        parsed = urlparse(url)
        path = parsed.path or "/"

        for disallowed in self.disallowed_paths:
            if path.startswith(disallowed):
                return False

        return True


class PolitenessManager:
    """Coordinate per-host rate limiting and robots.txt checks."""

    def __init__(self, requests_per_second: float = 1.0):
        self.requests_per_second = requests_per_second
        self.buckets: dict[str, TokenBucket] = {}
        self.robots_cache: dict[str, RobotsChecker] = {}

    def _host_key(self, url: str) -> str:
        """Extract hostname from URL."""
        parsed = urlparse(url)
        return parsed.netloc.lower()

    def get_bucket(self, url: str) -> TokenBucket:
        """Get or create a token bucket for the URL's host."""
        host = self._host_key(url)
        if host not in self.buckets:
            self.buckets[host] = TokenBucket(
                capacity=5,
                rate=self.requests_per_second
            )
        return self.buckets[host]

    def is_url_allowed(self, url: str) -> bool:
        """Check if URL is allowed (robots.txt + rate limit)."""
        host = self._host_key(url)

        # Check rate limit
        bucket = self.get_bucket(url)
        if not bucket.acquire():
            return False

        # Check robots.txt (if cached)
        if host in self.robots_cache:
            if not self.robots_cache[host].is_allowed(url):
                return False

        return True

    def fetch_and_cache_robots_txt(self, base_url: str) -> bool:
        """Fetch robots.txt for a host and cache the checker.

        Returns True if successful (even if no rules), False if fetch failed.
        """
        host = self._host_key(base_url)
        if host in self.robots_cache:
            return True  # Already cached

        try:
            parsed = urlparse(base_url)
            robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
            # Through safe_client so robots.txt is fetched from the same IP the
            # rest of discovery uses. A bare httpx.get leaves from the droplet,
            # which both discloses the server's real address to every host we
            # look at and returns a WAF 403 that we then read as "no robots
            # rules", quietly disagreeing with what the proxied fetches see.
            from .safe_http import safe_client
            with safe_client(timeout=5.0) as robots_client:
                response = robots_client.get(robots_url)
            if response.status_code == 200:
                self.robots_cache[host] = RobotsChecker(response.text)
            else:
                self.robots_cache[host] = RobotsChecker()  # Empty (allow all)
            return True
        except Exception:
            # Fetch failed — assume allow for now
            self.robots_cache[host] = RobotsChecker()
            return False
