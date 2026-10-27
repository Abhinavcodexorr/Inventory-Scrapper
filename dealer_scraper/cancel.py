"""Cooperative cancellation for in-flight scrapes and discovery runs.

A full-site scrape (or a deep discovery run) can run for a long time, so "stop"
has to reach it wherever it is executing. Two signal channels, mirroring the
two dispatch backends:

  * a process-local flag — the API's thread pool runs the job in this very
    process, and an in-memory set is instant and never needs infrastructure;
  * a Redis flag — a Celery worker is a different process (often a different
    machine), so the signal has to leave this one.

request_cancel() writes BOTH and is_cancelled() reads both, because neither the
API request asking to stop nor the engine doing the work can know which backend
picked the job up.

This is cooperative, not a kill. The engine checks between vehicles (discovery
checks between strategies/phases) and stops at a clean boundary, so a cancelled
run keeps everything it had already done and never leaves a half-written row —
unlike killing the worker, which strands the run at 'running' until the
stale-run timeout expires.
"""
from __future__ import annotations

import logging
import threading

from .config import settings

log = logging.getLogger("scraper.cancel")

_lock = threading.Lock()

_client = None          # lazily built; constructing one does not connect
_client_built = False


class ScrapeCancelled(Exception):
    """Raised inside the scrape loop when a stop has been requested."""


def _redis():
    """Best-effort Redis handle. Never raises: without Redis the local flag still
    works, which is exactly the single-process case that does not need Redis."""
    global _client, _client_built
    if not _client_built:
        _client_built = True
        try:
            import redis

            _client = redis.Redis.from_url(
                settings.redis_url,
                socket_timeout=2,
                socket_connect_timeout=2,
                decode_responses=True,
            )
        except Exception as exc:            # redis not installed / bad URL
            log.warning("Cancellation will be process-local only: %s", exc)
            _client = None
    return _client


class _CancelChannel:
    """One cooperative-cancel signal channel, keyed by an arbitrary id (dealer_id
    for scrapes, discovery run_id for discovery runs). Each channel gets its own
    Redis key prefix and process-local set so a scrape cancel can never be
    mistaken for a discovery cancel even if the ids collide numerically."""

    def __init__(self, key_prefix: str, default_ttl: int) -> None:
        self._key_prefix = key_prefix
        self._default_ttl = default_ttl
        self._cancelled: set[int] = set()

    def _key(self, item_id: int) -> str:
        return f"{self._key_prefix}{item_id}"

    def request(self, item_id: int, ttl: int | None = None) -> None:
        """Ask the in-flight job for `item_id` to stop at its next checkpoint."""
        with _lock:
            self._cancelled.add(item_id)
        client = _redis()
        if client is not None:
            try:
                # Expires on its own so a signal for a job that already died can
                # never linger and kill an unrelated run days later.
                client.set(self._key(item_id), "1", ex=ttl or self._default_ttl)
            except Exception as exc:
                log.warning("Could not publish cancel for %s to Redis: %s", self._key(item_id), exc)

    def is_cancelled(self, item_id: int) -> bool:
        with _lock:
            if item_id in self._cancelled:
                return True
        client = _redis()
        if client is None:
            return False
        try:
            return bool(client.exists(self._key(item_id)))
        except Exception:
            return False        # Redis down mid-job must not abort the job

    def clear(self, item_id: int) -> None:
        """Drop the signal — called when the targeted run ends and before a new
        job starts, so a stop can never carry over into the next run."""
        with _lock:
            self._cancelled.discard(item_id)
        client = _redis()
        if client is not None:
            try:
                client.delete(self._key(item_id))
            except Exception:
                pass


_scrape_channel = _CancelChannel("scrape:cancel:", settings.sync_stale_after_seconds)
_discovery_channel = _CancelChannel("discovery:cancel:", settings.sync_stale_after_seconds)


# ------------------------------------------------------------------- scrapes
def request_cancel(dealer_id: int) -> None:
    _scrape_channel.request(dealer_id)


def is_cancelled(dealer_id: int) -> bool:
    return _scrape_channel.is_cancelled(dealer_id)


def clear_cancel(dealer_id: int) -> None:
    _scrape_channel.clear(dealer_id)


# ----------------------------------------------------------------- discovery
# Note: the exception a cancelled discovery run raises is
# dealer_scraper.discovery.context.DiscoveryCancelled, not defined here — the
# coordinator's own try/except must catch the exact class its callers raise,
# so this module only owns the signal channel (request/is/clear), never the
# exception type.


def request_discovery_cancel(run_id: int) -> None:
    _discovery_channel.request(run_id, ttl=settings.discovery_host_lock_timeout * 4)


def is_discovery_cancelled(run_id: int) -> bool:
    return _discovery_channel.is_cancelled(run_id)


def clear_discovery_cancel(run_id: int) -> None:
    _discovery_channel.clear(run_id)
