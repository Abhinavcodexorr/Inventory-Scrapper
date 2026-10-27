"""Daily entrypoint. Cron runs this once a day:

    python -m dealer_scraper.run_daily

By default it enqueues one Celery task per ACTIVE dealer (requires Redis + worker).
Pass --inline to scrape in-process (no Redis), up to --workers sites at a time.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from .config import settings
from .db import SessionLocal
from .engine import run_dealer
from .models import Dealer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("scraper.run_daily")

#: How many sites the inline run scrapes at the same time, picked from the
#: dealer count: one slot per SITES_PER_WORKER sites, never more than MAX_WORKERS
#: (100 sites -> 20 at once, 250+ sites -> 50 at once). A browser-mode site
#: costs ~700 MB of RAM, so lower MAX_WORKERS or pass --workers on a small server.
SITES_PER_WORKER = 5
MAX_WORKERS = 50


def auto_workers(site_count: int) -> int:
    return max(1, min(MAX_WORKERS, site_count // SITES_PER_WORKER))


def _enqueue(dealer_ids: list[int]) -> int:
    from worker.tasks import scrape_dealer

    for dealer_id in dealer_ids:
        async_result = scrape_dealer.delay(dealer_id)
        log.info("Enqueued dealer #%s → task %s", dealer_id, async_result.id)
    return 0


def _scrape_one(session_factory, dealer_id: int) -> int:
    """Scrape one dealer in its own session; returns 1 if it failed, else 0."""
    try:
        with session_factory() as session:
            dealer = session.get(Dealer, dealer_id)
            if dealer is None:
                return 0
            log.info("Scraping dealer #%s (%s) [inline]", dealer.id, dealer.name)
            run = run_dealer(session, dealer)
            dur = ""
            if run.started_at and run.finished_at:
                dur = f" | took {(run.finished_at - run.started_at).total_seconds():.0f}s"
            log.info(
                "  dealer #%s -> %s | scraped=%s new=%s updated=%s price_changed=%s "
                "removed=%s reappeared=%s sold=%s%s%s",
                dealer.id, run.status, run.scraped_count, run.new_count, run.updated_count,
                run.price_changed_count, run.removed_count, run.reappeared_count,
                run.sold_count, dur, (f" ERROR={run.error}" if run.error else ""),
            )
            return 1 if run.status in ("failed", "incomplete") else 0
    except Exception:  # one site crashing must not stop the others
        log.exception("Inline scrape crashed for dealer #%s", dealer_id)
        return 1


def _run_inline(dealer_ids: list[int], workers: int) -> int:
    workers = max(1, min(workers, len(dealer_ids)))
    # Each thread needs its own DB connection; the shared engine's default pool
    # (5 + 10 overflow) would make most of them queue and time out.
    engine = create_engine(
        settings.database_url, pool_pre_ping=True, future=True,
        pool_size=workers, max_overflow=5,
    )
    session_factory = sessionmaker(
        bind=engine, autoflush=False, expire_on_commit=False, future=True,
    )
    try:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="scrape") as pool:
            failures = sum(pool.map(lambda d: _scrape_one(session_factory, d), dealer_ids))
    finally:
        engine.dispose()
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Daily dealer inventory scrape")
    parser.add_argument(
        "--inline",
        action="store_true",
        help="Run scrapes in-process instead of enqueueing Celery tasks",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help="With --inline: how many sites to scrape at once "
             f"(default: 1 per {SITES_PER_WORKER} sites, max {MAX_WORKERS})",
    )
    args = parser.parse_args(argv)

    with SessionLocal() as session:
        dealers = list(
            session.execute(select(Dealer).where(Dealer.active.is_(True))).scalars()
        )
        if not dealers:
            log.warning("No active dealers configured — nothing to do.")
            return 0
        dealer_ids = [d.id for d in dealers]

    workers = args.workers or auto_workers(len(dealer_ids))

    log.info(
        "Starting daily run for %d dealer(s) mode=%s",
        len(dealer_ids),
        f"inline x{workers}" if args.inline else "queue",
    )
    total_start = time.monotonic()
    exit_code = _run_inline(dealer_ids, workers) if args.inline else _enqueue(dealer_ids)
    log.info(
        "Daily dispatcher finished in %.0fs (exit=%s).",
        time.monotonic() - total_start,
        exit_code,
    )
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
