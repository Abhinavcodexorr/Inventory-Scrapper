"""Close scrape runs stuck at 'running' (worker killed). Safe to run any time.

    python -m scripts.reap_runs
"""
from dealer_scraper.db import SessionLocal
from dealer_scraper.engine import reap_abandoned_runs

if __name__ == "__main__":
    with SessionLocal() as session:
        print(f"closed {reap_abandoned_runs(session)} abandoned run(s)")
