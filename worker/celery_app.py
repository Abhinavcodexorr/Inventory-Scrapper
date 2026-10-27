"""Celery application — broker/backend from REDIS_URL.

    celery -A worker.celery_app worker -l info      # runs scrapes
    celery -A worker.celery_app beat  -l info       # fires the daily sync (SOW §3)
"""
from __future__ import annotations

import threading
import time
from datetime import datetime

import redis
from celery import Celery
from celery.schedules import crontab
from redis.backoff import NoBackoff
from redis.retry import Retry

from dealer_scraper.config import settings

celery_app = Celery(
    "dealer_scraper",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["worker.tasks"],
)

def schedule_timezone() -> str:
    """The configured timezone, or UTC if it is unusable on this machine.

    Validated here rather than trusted, because a slim server image without the
    `tzdata` package cannot resolve "America/Toronto" and Celery would raise at
    startup. Falling back to UTC runs the sync at the wrong local hour, which is
    a nuisance; failing to boot means it never runs at all.
    """
    name = settings.daily_sync_timezone or "UTC"
    try:
        from zoneinfo import ZoneInfo

        ZoneInfo(name)
        return name
    except Exception:
        return "UTC"


def _daily_sync_schedule() -> dict:
    """SOW §3: run inventory synchronisation once a day, on a configurable schedule.

    Kept in Beat rather than a host crontab so the cadence ships with the app and
    is one config value (DAILY_SYNC_HOUR/MINUTE/TIMEZONE) rather than an ops task
    on every machine. Returns {} when disabled, which leaves Beat with nothing to
    fire — the manual "Sync now" path is unaffected either way.

    The local hour comes from the app's `timezone` setting (see schedule_timezone
    below), NOT from crontab's `nowfun`. Beat's PersistentScheduler pickles every
    entry to its schedule file, and a `nowfun` lambda closing over a ZoneInfo is
    a local object that cannot be pickled — beat then dies with
    "Can't pickle local object" on its first sync and systemd restarts it
    forever, so the daily sync silently never happens. Celery's own timezone
    handling also tracks DST, which a fixed offset would not.
    """
    if not settings.daily_sync_enabled:
        return {}

    return {
        "daily-inventory-sync": {
            "task": "worker.tasks.sync_all_dealers",
            "schedule": crontab(
                hour=settings.daily_sync_hour,
                minute=settings.daily_sync_minute,
            ),
            # A missed window (worker down at 03:15) should not fire a pile-up of
            # catch-up runs when it comes back — one late run is enough.
            "options": {"expires": 60 * 60 * 6},
        }
    }


celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    # Beat evaluates crontab entries in this zone, so DAILY_SYNC_HOUR means the
    # local hour the client expects. Messages stay UTC on the wire.
    timezone=schedule_timezone(),
    enable_utc=True,
    beat_schedule=_daily_sync_schedule(),
    task_track_started=True,
    worker_prefetch_multiplier=1,  # one heavy scrape at a time per child
    worker_concurrency=settings.celery_worker_concurrency,
    # Fail fast when the broker is unreachable. Without these, kombu retries the
    # connection ~100 times with backoff, so a .delay() from inside a request
    # handler hangs for minutes instead of erroring — the caller can't tell a
    # dead broker from a slow one. Callers are expected to catch
    # kombu.exceptions.OperationalError and surface it (the API returns 503).
    broker_transport_options={
        "socket_connect_timeout": 2,
        "socket_timeout": 2,
        "max_retries": 1,
    },
    result_backend_transport_options={
        "socket_connect_timeout": 2,
        "socket_timeout": 2,
        "max_retries": 1,
    },
    broker_connection_max_retries=2,
    task_publish_retry_policy={
        "max_retries": 2,
        "interval_start": 0,
        "interval_step": 0.2,
        "interval_max": 0.5,
    },
)


_PROBE_TTL = 10.0

# Worker presence changes far less often than broker reachability, and the probe
# is answered by the worker's control consumer — which a busy 'solo'-pool worker
# (the Windows default) CANNOT answer while it is running a scrape. A long TTL
# means a worker mid-scrape keeps counting as present instead of flapping to
# absent and sending the next sync in-process.
_WORKER_PROBE_TTL = 300.0


class _CachedProbe:
    """A yes/no probe whose answer is cached and refreshed off the request path.

    Once a value is cached, a stale entry is refreshed in the BACKGROUND and the
    previous answer returned immediately, so no request handler ever waits.
    """

    def __init__(self, fn):
        self._fn = fn
        self._lock = threading.Lock()
        self._cache: tuple[float, bool] | None = None

    def get(self, timeout: float, ttl: float) -> bool:
        cached = self._cache
        if ttl and cached is not None:
            if (time.monotonic() - cached[0]) < ttl:
                return cached[1]
            self._refresh_async(timeout)
            return cached[1]
        with self._lock:
            # Another thread may have refreshed while we waited for the lock.
            cached = self._cache
            if ttl and cached is not None and (time.monotonic() - cached[0]) < ttl:
                return cached[1]
            result = self._fn(timeout)
            self._cache = (time.monotonic(), result)
            return result

    def _refresh_async(self, timeout: float) -> None:
        """At most one refresh in flight at a time."""
        if not self._lock.acquire(blocking=False):
            return

        def run() -> None:
            try:
                self._cache = (time.monotonic(), self._fn(timeout))
            finally:
                self._lock.release()

        threading.Thread(target=run, daemon=True).start()


def _probe(timeout: float) -> bool:
    try:
        client = redis.Redis.from_url(
            settings.redis_url,
            socket_connect_timeout=timeout,
            socket_timeout=timeout,
            # redis-py retries a failed connect once by default. When the port is
            # firewalled (packets dropped rather than refused) that doubles the
            # wall time to 2x timeout on a request path. One attempt is enough for
            # a yes/no reachability answer.
            retry=Retry(NoBackoff(), 0),
            retry_on_timeout=False,
        )
        try:
            return bool(client.ping())
        finally:
            client.close()
    except Exception:
        return False


def broker_available(timeout: float = 0.5, ttl: float = _PROBE_TTL) -> bool:
    """Cheap, cached reachability probe for the Redis broker/result backend.

    Call this before .delay() from a request handler. The transport options above
    do not cover every retry loop inside Celery — notably the result backend's
    pubsub consumer (`on_task_call` -> `_reconnect_pubsub`) retries via
    `retry_over_time` using its own policy, which blocks for minutes when Redis is
    down. A direct PING is unambiguous.

    The answer is cached for `ttl` seconds so a burst of sync requests pays the
    probe at most once — with Redis down, an uncached probe costs the full
    `timeout` on every single call. Once a value is cached, a stale entry is
    refreshed in the BACKGROUND and the previous answer is returned immediately,
    so no request handler ever waits on the probe. Either way a stale answer is
    safe: a stale 'up' surfaces as a publish error that falls back to local
    execution, and a stale 'down' just runs one scrape in-process.

    Pass ttl=0 to force a fresh, synchronous probe.
    """
    return _broker_probe.get(timeout, ttl)


def _probe_workers(timeout: float) -> bool:
    """Is any Celery worker actually consuming the queue?

    A reachable broker does NOT mean the work will run: Redis up with no worker
    is the one silent-failure combination, where every sync is accepted and then
    queues forever. This is the check that distinguishes them.
    """
    try:
        return bool(celery_app.control.ping(timeout=timeout))
    except Exception:
        return False


def workers_available(timeout: float = 1.0, ttl: float = _WORKER_PROBE_TTL) -> bool:
    """Cached 'is a worker listening?' probe. See _WORKER_PROBE_TTL on why the
    cache is deliberately long-lived."""
    if not broker_available():
        return False
    return _worker_probe.get(timeout, ttl)


_broker_probe = _CachedProbe(_probe)
_worker_probe = _CachedProbe(_probe_workers)
