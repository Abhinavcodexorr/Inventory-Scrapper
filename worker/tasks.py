"""Celery tasks wrapping dealer_scraper.engine.run_dealer.

Per-host concurrency: never scrape two dealers on the same hostname at once
(Redis lock keyed by URL netloc). Different hosts may run in parallel.
"""
from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from urllib.parse import urlparse

import redis
from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy import select

from dealer_scraper.config import settings
from dealer_scraper.db import SessionLocal
from dealer_scraper.engine import run_dealer
from dealer_scraper.models import Dealer
from worker.celery_app import celery_app

log = logging.getLogger("worker.tasks")

_LOCK_PREFIX = "scrape_host:"


def _host_key(base_url: str) -> str:
    netloc = urlparse(base_url).netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return f"{_LOCK_PREFIX}{netloc or 'unknown'}"


def _redis_client() -> redis.Redis:
    return redis.Redis.from_url(settings.redis_url, decode_responses=True)


#: Operator-facing text for a host that is already locked. Shared so the local
#: and Celery backends explain a busy host identically.
_HOST_BUSY_MESSAGE = (
    "Another scrape or detection run for this website was still holding the "
    "per-website lock, so this run never started. If no run is actually in "
    "progress, the lock was left behind by a run that was killed — it clears "
    "itself automatically. Details: {exc}"
)


@contextmanager
def _host_lock(base_url: str, timeout: int | None = None, ttl: int | None = None):
    """Block until the per-host lock is acquired, then release on exit.

    `timeout` defaults to scrape_host_lock_timeout (used by scrape_dealer);
    discover_site passes the much shorter discovery_host_lock_timeout so a
    discovery run yields quickly to an in-progress scrape of the same host
    instead of queueing behind up to an hour of it.

    `ttl` is how long the lock survives if the holder dies without releasing it
    — the only backstop there is, since a killed process runs no `finally`.
    Discovery passes a TTL sized to its own budget rather than inheriting the
    scrape TTL: a discovery run killed mid-flight used to leave the host locked
    for two hours, silently failing every retry for that dealer in the meantime.
    """
    key = _host_key(base_url)
    client = _redis_client()
    token = f"{time.time_ns()}"
    wait_seconds = timeout if timeout is not None else settings.scrape_host_lock_timeout
    lock_ttl = ttl if ttl is not None else settings.scrape_host_lock_ttl
    deadline = time.monotonic() + wait_seconds
    acquired = False
    try:
        while time.monotonic() < deadline:
            acquired = bool(
                client.set(key, token, nx=True, ex=lock_ttl)
            )
            if acquired:
                log.info("Acquired host lock %s", key)
                break
            log.info("Waiting for host lock %s …", key)
            time.sleep(5)
        if not acquired:
            raise TimeoutError(
                f"Timed out waiting for scrape lock on host key {key} "
                f"after {wait_seconds}s"
            )
        yield
    finally:
        if acquired:
            # Only delete if we still own the lock (avoid releasing another worker's lock).
            current = client.get(key)
            if current == token:
                client.delete(key)
                log.info("Released host lock %s", key)


@celery_app.task(
    name="worker.tasks.scrape_dealer",
    bind=True,
    max_retries=0,
    soft_time_limit=60 * 90,
    time_limit=60 * 100,
)
def scrape_dealer(self, dealer_id: int) -> dict:
    """Scrape one dealer end-to-end. Returns a small status summary for the result backend."""
    log.info("scrape_dealer start dealer_id=%s task_id=%s", dealer_id, self.request.id)
    with SessionLocal() as session:
        dealer = session.get(Dealer, dealer_id)
        if dealer is None:
            log.error("Dealer %s not found", dealer_id)
            return {"status": "failed", "dealer_id": dealer_id, "error": "Dealer not found"}

        base_url = dealer.base_url
        try:
            with _host_lock(base_url):
                # Re-load after waiting so we see fresh state.
                session.expire_all()
                dealer = session.get(Dealer, dealer_id)
                if dealer is None:
                    return {
                        "status": "failed",
                        "dealer_id": dealer_id,
                        "error": "Dealer not found after lock",
                    }
                run = run_dealer(session, dealer)
                return {
                    "status": run.status,
                    "dealer_id": dealer_id,
                    "run_id": run.id,
                    "scraped_count": run.scraped_count,
                    "error": run.error,
                }
        except SoftTimeLimitExceeded:
            log.exception("Soft time limit exceeded for dealer %s", dealer_id)
            return {"status": "failed", "dealer_id": dealer_id, "error": "soft_time_limit"}
        except TimeoutError as exc:
            log.error("%s", exc)
            return {"status": "failed", "dealer_id": dealer_id, "error": str(exc)}
        except Exception as exc:
            log.exception("Unexpected failure scraping dealer %s", dealer_id)
            return {"status": "failed", "dealer_id": dealer_id, "error": str(exc)[:1000]}


@celery_app.task(
    name="worker.tasks.sync_all_dealers",
    bind=True,
    max_retries=0,
    # Fan-out only: it enqueues children and returns. The long work happens in
    # scrape_dealer, so this needs a short limit, not a scrape-length one.
    soft_time_limit=60 * 5,
    time_limit=60 * 6,
)
def sync_all_dealers(self, dealer_ids: list[int] | None = None) -> dict:
    """SOW §3 — the daily synchronisation entrypoint, run by Celery Beat.

    Enqueues one scrape_dealer task per ACTIVE dealer and returns immediately.
    Fanning out (rather than looping run_dealer here) means one dealer failing
    or hanging cannot stop the rest, and the per-host locks in scrape_dealer
    still serialise dealers that share a hostname.

    Dealers are staggered so a large fleet doesn't hit every site — or our own
    proxy pool — in the same instant.

    `dealer_ids` is for manual "sync everything now" from the API; Beat calls it
    with no argument to mean "every active dealer".
    """
    with SessionLocal() as session:
        stmt = select(Dealer.id).where(Dealer.active.is_(True))
        if dealer_ids:
            stmt = stmt.where(Dealer.id.in_(dealer_ids))
        ids = list(session.execute(stmt.order_by(Dealer.id)).scalars())

    if not ids:
        log.warning("Daily sync: no active dealers to scrape")
        return {"status": "completed", "dispatched": 0, "dealer_ids": []}

    stagger = max(0, settings.daily_sync_stagger_seconds)
    for position, dealer_id in enumerate(ids):
        scrape_dealer.apply_async(args=[dealer_id], countdown=position * stagger)

    log.info(
        "Daily sync dispatched %d dealer(s) (stagger %ds, task_id=%s)",
        len(ids), stagger, self.request.id,
    )
    return {"status": "completed", "dispatched": len(ids), "dealer_ids": ids}


@celery_app.task(
    name="worker.tasks.discover_site",
    bind=True,
    max_retries=0,
    soft_time_limit=60 * 12,
    time_limit=60 * 15,
)
def discover_site(self, run_id: int) -> dict:
    """Run discovery for one DiscoveryRun row end-to-end. Returns a small status
    summary for the result backend — the full DiscoveryReport lives on the row
    itself (dealer_scraper.discovery.persistence.run_discovery), never in the
    Celery result payload.
    """
    from dealer_scraper.discovery import run_discovery
    from dealer_scraper.discovery.persistence import fail_unfinished_run
    from dealer_scraper.models import DiscoveryRun

    log.info("discover_site start run_id=%s task_id=%s", run_id, self.request.id)
    with SessionLocal() as session:
        run = session.get(DiscoveryRun, run_id)
        if run is None:
            log.error("Discovery run %s not found", run_id)
            return {"status": "failed", "run_id": run_id, "error": "Discovery run not found"}
        base_url = run.base_url

    try:
        # A much shorter wait than the scrape lock: discovery yields to an
        # in-progress scrape of the same host rather than queueing behind it.
        with _host_lock(base_url, timeout=settings.discovery_host_lock_timeout,
                        ttl=settings.discovery_host_lock_ttl):
            return run_discovery(run_id)
    except SoftTimeLimitExceeded:
        log.exception("Soft time limit exceeded for discovery run %s", run_id)
        fail_unfinished_run(run_id, "Discovery exceeded its time limit and was stopped.")
        return {"status": "failed", "run_id": run_id, "error": "soft_time_limit"}
    except TimeoutError as exc:
        log.warning("%s", exc)
        fail_unfinished_run(run_id, _HOST_BUSY_MESSAGE.format(exc=exc))
        return {"status": "failed", "run_id": run_id, "error": f"host busy: {exc}"}
    except Exception as exc:
        log.exception("Unexpected failure discovering %s", base_url)
        fail_unfinished_run(run_id, str(exc)[:1000])
        return {"status": "failed", "run_id": run_id, "error": str(exc)[:1000]}
