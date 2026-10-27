"""Tests for stopping an in-flight sync (cooperative cancellation).

Covers: a running scrape stops and keeps its partial work, the stop flag never
leaks into the next run, a stale stop cannot kill a freshly dispatched sync, and
cancelling with nothing in flight is reported honestly.

Run:  DATABASE_URL="sqlite:///./cancel_test.db" python -m tests.test_cancel
"""
from __future__ import annotations

import time
from sqlalchemy import select
from dealer_scraper.db import SessionLocal, engine
from dealer_scraper.models import Base, Car, Dealer
from dealer_scraper.cancel import clear_cancel, is_cancelled, request_cancel

from . import require_disposable_db

require_disposable_db()
Base.metadata.drop_all(engine); Base.metadata.create_all(engine)

def mk_dealer(name, delay):
    items = [{"external_id": f"S{i}", "vin": f"VIN{i:05d}", "make": "Honda", "model": "Civic",
              "year": 2020, "price": "20000", "url": f"https://x/{i}"} for i in range(1, 41)]
    with SessionLocal() as s:
        d = Dealer(name=name, base_url=f"https://{name}.example", adapter_mode="stub",
                   scrape_config={"items": items, "delay_seconds": delay})
        s.add(d); s.commit()
        return d.id

# ---- 1. cancel a RUNNING scrape ------------------------------------------
from worker import dispatch
did = mk_dealer("cancelme", 0.25)          # 40 cars * 0.25s = 10s of work
dispatch.broker_available = lambda: False  # force the local backend
out = dispatch.dispatch_sync(did, "https://cancelme.example")
assert out["mode"] == "local", out
time.sleep(3.0)                            # let it get well into the run
res = dispatch.cancel_sync(did, out["job_id"])
assert res["status"] == "cancelling", res

for _ in range(80):
    job = dispatch.local_job(out["job_id"])
    if job and job.finished_at: break
    time.sleep(0.25)
assert job.finished_at, "job never finished after cancel"
assert job.status == "REVOKED", job.status
assert job.result["status"] == "cancelled", job.result
with SessionLocal() as s:
    run = s.execute(select(Dealer).where(Dealer.id == did)).scalar_one()
    n = len(list(s.execute(select(Car).where(Car.dealer_id == did)).scalars()))
assert run.last_sync_status == "cancelled", run.last_sync_status
assert 0 < n < 40, f"expected a partial scrape, got {n}"
print(f"  running scrape stops on request, keeps its {n} scraped cars, run='cancelled' OK")

# ---- 2. flag never leaks into the next run --------------------------------
assert not is_cancelled(did), "cancel flag leaked past the run it targeted"
out2 = dispatch.dispatch_sync(did, "https://cancelme.example")
for _ in range(160):
    job2 = dispatch.local_job(out2["job_id"])
    if job2 and job2.finished_at: break
    time.sleep(0.25)
assert job2.status == "SUCCESS", (job2.status, job2.result)
assert job2.result["status"] in ("completed", "incomplete"), job2.result
with SessionLocal() as s:
    n2 = len(list(s.execute(select(Car).where(Car.dealer_id == did)).scalars()))
assert n2 == 40, n2
print(f"  next sync after a cancel runs clean to {n2} cars OK")

# ---- 3. cancel while still QUEUED ----------------------------------------
d2 = mk_dealer("queued", 0.05)
request_cancel(d2)                          # stop arrives before the job starts
out3 = dispatch.dispatch_sync(d2, "https://queued.example")   # clears, as a new sync should
for _ in range(160):
    j3 = dispatch.local_job(out3["job_id"])
    if j3 and j3.finished_at: break
    time.sleep(0.25)
assert j3.result["status"] in ("completed", "incomplete"), j3.result
print("  a stale stop does NOT kill a newly dispatched sync OK")

# ---- 4. nothing running -> not_running, and NO flag left behind ----------
clear_cancel(d2)
res4 = dispatch.cancel_sync(d2, None)
assert res4["status"] == "not_running", res4
# A flag raised here would have nothing to stop and would abort this dealer's
# next run — run_daily calls the engine directly and never clears it first.
assert not is_cancelled(d2), "cancel with nothing in flight left a stale stop flag"
print("  cancel with nothing in flight reports not_running, raises no flag OK")

for _d in (did, d2):
    clear_cancel(_d)
print("CANCEL TESTS PASSED")
