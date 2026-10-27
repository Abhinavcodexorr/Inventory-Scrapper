"""Single entry point the API uses to start a dealer scrape.

Two execution backends, chosen at call time so the app works with or without
infrastructure:

  * Celery  — used whenever the Redis broker answers a PING. Survives an API
              restart, scales across machines, gives per-host locking via Redis.
  * Thread  — in-process fallback. Same pipeline (engine.run_dealer), same
              ScrapeRun rows, but bounded by this process's lifetime.

Either way the HTTP request returns immediately: nothing here blocks on the
scrape itself. Both backends funnel into dealer_scraper.engine.run_dealer — the
scrape logic is never duplicated.
"""
from __future__ import annotations

import logging
import threading
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlparse

from kombu.exceptions import OperationalError
from redis.exceptions import ConnectionError as RedisConnectionError

from dealer_scraper.cancel import (
    clear_cancel,
    clear_discovery_cancel,
    request_cancel,
    request_discovery_cancel,
)
from dealer_scraper.config import settings
from dealer_scraper.db import SessionLocal
from dealer_scraper.engine import run_dealer
from dealer_scraper.models import Dealer
from worker.celery_app import broker_available, workers_available

log = logging.getLogger("worker.dispatch")

# Celery-compatible states so the API response shape is identical for both backends.
PENDING, STARTED, SUCCESS, FAILURE = "PENDING", "STARTED", "SUCCESS", "FAILURE"
REVOKED = "REVOKED"          # Celery's name for "cancelled before/while running"


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class LocalJob:
    job_id: str
    dealer_id: int
    status: str = PENDING
    result: dict | None = None
    queued_at: datetime = field(default_factory=_now)
    started_at: datetime | None = None
    finished_at: datetime | None = None

    def as_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "dealer_id": self.dealer_id,
            "status": self.status,
            "result": self.result,
            "queued_at": self.queued_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


# ------------------------------------------------------------- local backend
_state_lock = threading.Lock()
_jobs: dict[str, LocalJob] = {}
_dealer_job: dict[int, str] = {}          # dealer_id -> job_id, only while in flight
_futures: dict[str, Future] = {}          # job_id -> future, so a QUEUED job can be pulled
_host_locks: dict[str, threading.Lock] = {}

_executor: ThreadPoolExecutor | None = None
_MAX_JOB_HISTORY = 200


def _host_key(base_url: str) -> str:
    netloc = urlparse(base_url or "").netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return netloc or "unknown"


def _host_lock(base_url: str) -> threading.Lock:
    """Mirror of the Celery task's Redis lock: never scrape one host twice at once."""
    key = _host_key(base_url)
    with _state_lock:
        lock = _host_locks.get(key)
        if lock is None:
            lock = _host_locks[key] = threading.Lock()
        return lock


@contextmanager
def _exclusive_host(base_url: str):
    """Hold one host exclusively across BOTH backends.

    A thread lock only excludes other threads in THIS process. When Redis is up a
    Celery worker may be scraping the same host from another process — e.g. a
    queued job from before the backend switched — so take the very same Redis
    lock worker.tasks uses and the two can never overlap.
    """
    with ExitStack() as stack:
        held = False
        if broker_available():
            try:
                from worker.tasks import _host_lock as redis_host_lock

                stack.enter_context(redis_host_lock(base_url))
                held = True
            except TimeoutError:
                raise            # somebody else holds the host; do NOT scrape anyway
            except Exception as exc:
                log.warning("Redis host lock unusable for %s (%s); using in-process lock",
                            base_url, exc)
        if not held:
            stack.enter_context(_host_lock(base_url))
        yield


def _get_executor() -> ThreadPoolExecutor:
    global _executor
    if _executor is None:
        with _state_lock:
            if _executor is None:
                _executor = ThreadPoolExecutor(
                    max_workers=max(1, settings.celery_worker_concurrency),
                    thread_name_prefix="scrape",
                )
    return _executor


def _prune_jobs() -> None:
    """Keep the in-memory job map bounded; drop the oldest finished jobs."""
    if len(_jobs) <= _MAX_JOB_HISTORY:
        return
    finished = sorted(
        (j for j in _jobs.values() if j.finished_at is not None),
        key=lambda j: j.finished_at,
    )
    for job in finished[: len(_jobs) - _MAX_JOB_HISTORY]:
        _jobs.pop(job.job_id, None)


def _run_local(job_id: str, dealer_id: int, base_url: str) -> None:
    with _state_lock:
        job = _jobs.get(job_id)
        if job is None:
            return
        job.status = STARTED
        job.started_at = _now()

    result: dict
    try:
        # Serialize per host exactly like the Celery path does.
        with _exclusive_host(base_url):
            with SessionLocal() as session:
                dealer = session.get(Dealer, dealer_id)
                if dealer is None:
                    result = {"status": "failed", "dealer_id": dealer_id,
                              "error": "Dealer not found"}
                else:
                    run = run_dealer(session, dealer)
                    result = {
                        "status": run.status,
                        "dealer_id": dealer_id,
                        "run_id": run.id,
                        "scraped_count": run.scraped_count,
                        "error": run.error,
                    }
        status = SUCCESS
    except Exception as exc:  # never let a worker thread die silently
        log.exception("Local scrape failed for dealer %s", dealer_id)
        result = {"status": "failed", "dealer_id": dealer_id, "error": str(exc)[:1000]}
        status = FAILURE

    # A run the engine stopped on request is REVOKED, not SUCCESS — the caller
    # asked for this and the UI must not report it as a completed sync.
    if result.get("status") == "cancelled":
        status = REVOKED

    with _state_lock:
        job = _jobs.get(job_id)
        if job is not None:
            job.status = status
            job.result = result
            job.finished_at = _now()
        if _dealer_job.get(dealer_id) == job_id:
            _dealer_job.pop(dealer_id, None)
        _futures.pop(job_id, None)
        _prune_jobs()
    log.info("Local scrape finished dealer=%s job=%s -> %s", dealer_id, job_id, result.get("status"))


def _submit_local(dealer_id: int, base_url: str) -> tuple[str, str]:
    """Queue a scrape on the in-process pool. Returns (status, job_id)."""
    with _state_lock:
        running = _dealer_job.get(dealer_id)
        if running is not None:
            return "running", running
        job_id = f"local-{uuid.uuid4()}"
        _jobs[job_id] = LocalJob(job_id=job_id, dealer_id=dealer_id)
        _dealer_job[dealer_id] = job_id

    future = _get_executor().submit(_run_local, job_id, dealer_id, base_url)
    with _state_lock:
        _futures[job_id] = future
    log.info("Dispatched local scrape dealer=%s job=%s", dealer_id, job_id)
    return "queued", job_id


def local_job(job_id: str) -> LocalJob | None:
    with _state_lock:
        return _jobs.get(job_id)


def local_job_for_dealer(dealer_id: int) -> LocalJob | None:
    with _state_lock:
        job_id = _dealer_job.get(dealer_id)
        return _jobs.get(job_id) if job_id else None


def is_local_job(job_id: str) -> bool:
    return job_id.startswith("local-")


# ------------------------------------------------------------------ dispatch
def choose_backend() -> str:
    """Which backend should run this sync: 'local', 'celery', or 'no_worker'.

    Driven by SYNC_BACKEND (see dealer_scraper.config). The default 'auto' asks
    two questions, not one: a reachable broker is not enough, because Redis up
    with no worker consuming is precisely the combination where a sync is
    accepted and then never runs.
    """
    mode = (settings.sync_backend or "auto").strip().lower()
    if mode == "local":
        return "local"
    if mode == "celery":
        return "celery" if workers_available() else "no_worker"
    if not broker_available():
        return "local"
    return "celery" if workers_available() else "local"


def dispatch_sync(dealer_id: int, base_url: str) -> dict:
    """Start a scrape for one dealer without blocking the caller.

    Returns {"status": "queued"|"running", "job_id": str, "mode": "celery"|"local"}.
    "running" means a scrape for this dealer is already in flight — we return the
    existing job rather than starting a duplicate.
    """
    # A stop signal belongs to the run it was aimed at; a NEW sync always starts
    # uncancelled, even if the previous run died before clearing its own flag.
    clear_cancel(dealer_id)

    backend = choose_backend()
    if backend == "no_worker":
        # SYNC_BACKEND=celery and nothing is consuming. Say so instead of
        # returning "queued" for a job that would never run.
        log.error("SYNC_BACKEND=celery but no worker is listening; refusing to queue dealer %s",
                  dealer_id)
        return {"status": "no_worker", "job_id": "", "mode": "celery"}

    if backend == "celery":
        try:
            from worker.tasks import scrape_dealer  # imported lazily: only needed for Celery

            async_result = scrape_dealer.delay(dealer_id)
            log.info("Enqueued Celery scrape dealer=%s task=%s", dealer_id, async_result.id)
            return {"status": "queued", "job_id": async_result.id, "mode": "celery"}
        except (OperationalError, RedisConnectionError, OSError) as exc:
            # Broker died between the probe and the publish — fall through to local.
            log.warning(
                "Broker unreachable while enqueuing dealer %s (%s); running in-process",
                dealer_id, exc,
            )

    status, job_id = _submit_local(dealer_id, base_url)
    return {"status": status, "job_id": job_id, "mode": "local"}


def cancel_sync(dealer_id: int, job_id: str | None = None, in_flight: bool = False) -> dict:
    """Ask this dealer's in-flight scrape to stop, whichever backend runs it.

    Three things have to happen, because a job can be in three states:
      * QUEUED locally  — pull the future before a worker thread picks it up;
      * QUEUED on Celery — revoke it so no worker ever starts it (needs the
        task id, which only the caller has: we do not persist Celery ids);
      * RUNNING anywhere — set the cooperative flag and let the engine stop at
        its next checkpoint. Never terminate: that strands the run at 'running'.

    in_flight is what the CALLER knows that this process cannot: a Celery worker
    elsewhere may own a run. Nothing in flight means no flag is raised at all —
    a flag outlives this call by design, and one left behind with no run to stop
    would abort the dealer's NEXT scrape (run_daily calls the engine directly and
    never passes through dispatch_sync, so nothing would clear it first).

    Returns {"status": "cancelling"|"not_running", "job_id", "mode"}.
    """
    with _state_lock:
        local_id = _dealer_job.get(dealer_id)
        job = _jobs.get(local_id) if local_id else None
        future = _futures.get(local_id) if local_id else None

    if job is None and not in_flight and not job_id:
        return {"status": "not_running", "job_id": "", "mode": "celery"}

    request_cancel(dealer_id)

    if job is not None:
        # cancel() only succeeds while the job is still queued; once a thread has
        # picked it up the cooperative flag is what stops it.
        if future is not None and future.cancel():
            with _state_lock:
                job.status = REVOKED
                job.result = {"status": "cancelled", "dealer_id": dealer_id,
                              "error": "Cancelled before it started"}
                job.finished_at = _now()
                _dealer_job.pop(dealer_id, None)
                _futures.pop(job.job_id, None)
            clear_cancel(dealer_id)   # it never ran; do not leave a flag behind
            log.info("Cancelled queued local scrape dealer=%s job=%s", dealer_id, job.job_id)
        else:
            log.info("Requested stop of running local scrape dealer=%s job=%s", dealer_id, job.job_id)
        return {"status": "cancelling", "job_id": job.job_id, "mode": "local"}

    if job_id and not is_local_job(job_id) and not job_id.startswith("run-"):
        try:
            from worker.tasks import scrape_dealer

            scrape_dealer.AsyncResult(job_id).revoke()   # no terminate=True
            log.info("Revoked Celery scrape dealer=%s task=%s", dealer_id, job_id)
        except Exception as exc:
            log.warning("Could not revoke Celery task %s: %s", job_id, exc)
        return {"status": "cancelling", "job_id": job_id, "mode": "celery"}

    # A Celery run the caller can see but whose task id we do not have: the flag
    # alone stops it at its next checkpoint.
    return {"status": "cancelling", "job_id": job_id or "", "mode": "celery"}


# ============================================================== discovery ===
# Siblings of the scrape dispatch machinery above, keyed by DiscoveryRun.id
# instead of dealer_id. The DB row (DiscoveryRun) is the cross-backend source
# of truth for status/progress/result — these job records only exist so a
# LOCAL run can be looked up/cancelled before its first DB write lands.


@dataclass
class DiscoveryLocalJob:
    job_id: str
    run_id: int
    status: str = PENDING
    result: dict | None = None
    queued_at: datetime = field(default_factory=_now)
    started_at: datetime | None = None
    finished_at: datetime | None = None

    def as_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "run_id": self.run_id,
            "status": self.status,
            "result": self.result,
            "queued_at": self.queued_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


_discovery_jobs: dict[str, DiscoveryLocalJob] = {}
_run_job: dict[int, str] = {}              # run_id -> job_id, only while in flight
_discovery_futures: dict[str, Future] = {}
_MAX_DISCOVERY_JOB_HISTORY = 200


@contextmanager
def _exclusive_host_for_discovery(base_url: str):
    """Like _exclusive_host, but bounded by discovery_host_lock_timeout instead
    of the (much longer) scrape_host_lock_timeout — discovery yields quickly to
    an in-progress scrape of the same host rather than queueing behind up to an
    hour of it, on either backend."""
    timeout = settings.discovery_host_lock_timeout
    if broker_available():
        try:
            from worker.tasks import _host_lock as redis_host_lock

            with redis_host_lock(base_url, timeout=timeout,
                                  ttl=settings.discovery_host_lock_ttl):
                yield
            return
        except TimeoutError:
            raise
        except Exception as exc:
            log.warning("Redis host lock unusable for %s (%s); using in-process lock",
                        base_url, exc)

    lock = _host_lock(base_url)
    if not lock.acquire(timeout=timeout):
        raise TimeoutError(
            f"Timed out waiting {timeout}s for the host lock on {_host_key(base_url)} "
            f"(a scrape or another discovery run owns it)"
        )
    try:
        yield
    finally:
        lock.release()


def _prune_discovery_jobs() -> None:
    if len(_discovery_jobs) <= _MAX_DISCOVERY_JOB_HISTORY:
        return
    finished = sorted(
        (j for j in _discovery_jobs.values() if j.finished_at is not None),
        key=lambda j: j.finished_at,
    )
    for job in finished[: len(_discovery_jobs) - _MAX_DISCOVERY_JOB_HISTORY]:
        _discovery_jobs.pop(job.job_id, None)


def _run_discovery_local(job_id: str, run_id: int, base_url: str) -> None:
    with _state_lock:
        job = _discovery_jobs.get(job_id)
        if job is None:
            return
        job.status = STARTED
        job.started_at = _now()

    result: dict
    try:
        with _exclusive_host_for_discovery(base_url):
            from dealer_scraper.discovery import run_discovery

            result = run_discovery(run_id)
        status = SUCCESS
    except TimeoutError as exc:
        # The host lock is held. run_discovery() never ran, so nothing has moved
        # the row off `queued` — finalize it here or the UI polls it forever.
        from dealer_scraper.discovery.persistence import fail_unfinished_run
        from worker.tasks import _HOST_BUSY_MESSAGE

        log.warning("Local discovery for run %s could not take the host lock: %s", run_id, exc)
        fail_unfinished_run(run_id, _HOST_BUSY_MESSAGE.format(exc=exc))
        result = {"status": "failed", "run_id": run_id, "error": f"host busy: {exc}"}
        status = FAILURE
    except Exception as exc:  # never let a worker thread die silently
        from dealer_scraper.discovery.persistence import fail_unfinished_run

        log.exception("Local discovery failed for run %s", run_id)
        fail_unfinished_run(run_id, str(exc)[:1000])
        result = {"status": "failed", "run_id": run_id, "error": str(exc)[:1000]}
        status = FAILURE

    if result.get("status") == "cancelled":
        status = REVOKED

    with _state_lock:
        job = _discovery_jobs.get(job_id)
        if job is not None:
            job.status = status
            job.result = result
            job.finished_at = _now()
        if _run_job.get(run_id) == job_id:
            _run_job.pop(run_id, None)
        _discovery_futures.pop(job_id, None)
        _prune_discovery_jobs()
    log.info("Local discovery finished run=%s job=%s -> %s", run_id, job_id, result.get("status"))


def _submit_discovery_local(run_id: int, base_url: str) -> tuple[str, str]:
    """Queue a discovery run on the in-process pool. Returns (status, job_id)."""
    with _state_lock:
        running = _run_job.get(run_id)
        if running is not None:
            return "running", running
        job_id = f"dlocal-{uuid.uuid4()}"
        _discovery_jobs[job_id] = DiscoveryLocalJob(job_id=job_id, run_id=run_id)
        _run_job[run_id] = job_id

    future = _get_executor().submit(_run_discovery_local, job_id, run_id, base_url)
    with _state_lock:
        _discovery_futures[job_id] = future
    log.info("Dispatched local discovery run=%s job=%s", run_id, job_id)
    return "queued", job_id


def discovery_local_job(job_id: str) -> DiscoveryLocalJob | None:
    with _state_lock:
        return _discovery_jobs.get(job_id)


def discovery_local_job_for_run(run_id: int) -> DiscoveryLocalJob | None:
    with _state_lock:
        job_id = _run_job.get(run_id)
        return _discovery_jobs.get(job_id) if job_id else None


def is_discovery_local_job(job_id: str) -> bool:
    return job_id.startswith("dlocal-")


def dispatch_discovery(run_id: int, base_url: str) -> dict:
    """Start a discovery run without blocking the caller.

    Mirrors dispatch_sync exactly (same backend choice, same broker-down
    fallback to local), but keyed by DiscoveryRun.id instead of dealer_id.
    Returns {"status": "queued"|"running", "job_id": str, "mode": "celery"|"local"}.
    """
    clear_discovery_cancel(run_id)

    backend = choose_backend()
    if backend == "no_worker":
        log.error("SYNC_BACKEND=celery but no worker is listening; refusing to queue "
                  "discovery run %s", run_id)
        return {"status": "no_worker", "job_id": "", "mode": "celery"}

    if backend == "celery":
        try:
            from worker.tasks import discover_site  # imported lazily: only needed for Celery

            async_result = discover_site.delay(run_id)
            log.info("Enqueued Celery discovery run=%s task=%s", run_id, async_result.id)
            return {"status": "queued", "job_id": async_result.id, "mode": "celery"}
        except (OperationalError, RedisConnectionError, OSError) as exc:
            log.warning(
                "Broker unreachable while enqueuing discovery run %s (%s); running in-process",
                run_id, exc,
            )

    status, job_id = _submit_discovery_local(run_id, base_url)
    return {"status": status, "job_id": job_id, "mode": "local"}


def cancel_discovery(run_id: int, job_id: str | None = None, in_flight: bool = False) -> dict:
    """Ask an in-flight discovery run to stop, whichever backend runs it. Mirrors
    cancel_sync exactly — see its docstring for the three-state rationale.
    """
    with _state_lock:
        local_id = _run_job.get(run_id)
        job = _discovery_jobs.get(local_id) if local_id else None
        future = _discovery_futures.get(local_id) if local_id else None

    if job is None and not in_flight and not job_id:
        return {"status": "not_running", "job_id": "", "mode": "celery"}

    request_discovery_cancel(run_id)

    if job is not None:
        if future is not None and future.cancel():
            with _state_lock:
                job.status = REVOKED
                job.result = {"status": "cancelled", "run_id": run_id,
                              "error": "Cancelled before it started"}
                job.finished_at = _now()
                _run_job.pop(run_id, None)
                _discovery_futures.pop(job.job_id, None)
            clear_discovery_cancel(run_id)   # it never ran; do not leave a flag behind
            log.info("Cancelled queued local discovery run=%s job=%s", run_id, job.job_id)
        else:
            log.info("Requested stop of running local discovery run=%s job=%s", run_id, job.job_id)
        return {"status": "cancelling", "job_id": job.job_id, "mode": "local"}

    if job_id and not is_discovery_local_job(job_id) and not job_id.startswith("run-"):
        try:
            from worker.tasks import discover_site

            discover_site.AsyncResult(job_id).revoke()   # no terminate=True
            log.info("Revoked Celery discovery run=%s task=%s", run_id, job_id)
        except Exception as exc:
            log.warning("Could not revoke Celery discovery task %s: %s", job_id, exc)
        return {"status": "cancelling", "job_id": job_id, "mode": "celery"}

    return {"status": "cancelling", "job_id": job_id or "", "mode": "celery"}
