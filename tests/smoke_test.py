"""End-to-end smoke test of the SOW inventory logic, against a real SQL engine.

Simulates the vehicle lifecycle across several daily runs for one dealer:
  Day 1: 10 cars.
  Day 2: 8 cars (S9,S10 removed), S1 price drop, S11 added.
  (backdate S9,S10 removal by 10 days)
  Day 3: S9,S10 auto-marked SOLD (removed > SOLD_AFTER_DAYS).
  Day 4: S9 reappears -> active again.

Asserts: dedup, removal (+removed_at), sold (+sold_at), reappearance, price
history, per-vehicle audit trail, and the daily snapshot — the SOW behaviour.

Run:  DATABASE_URL="sqlite:////tmp/smoke.db" python -m tests.smoke_test
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from decimal import Decimal

from sqlalchemy import select, func

from dealer_scraper.db import SessionLocal, engine
from dealer_scraper.models import (
    Base, Car, CarPriceHistory, Dealer, VehicleAuditLog, DailyInventorySnapshot,
)
from dealer_scraper.adapters.stub import StubAdapter
from dealer_scraper.engine import run_dealer
from . import require_disposable_db


def car(stock, price, **extra):
    base = {"external_id": stock, "vin": f"VIN{stock}", "make": "Honda",
            "model": "Civic", "year": 2020, "trim": "EX", "mileage": 10000,
            "body_type": "Sedan", "condition": "Used", "exterior_color": "Black",
            "interior_color": "Grey", "transmission": "Automatic", "fuel_type": "Gas",
            "drivetrain": "FWD", "price": price, "url": f"https://x/{stock}"}
    base.update(extra)
    return base


def run(dealer_id, items):
    with SessionLocal() as s:
        d = s.get(Dealer, dealer_id)
        return run_dealer(s, d, adapter=StubAdapter(items))


def main() -> None:
    require_disposable_db()   # drop_all() below — never let this hit a real DB
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)

    with SessionLocal() as s:
        d = Dealer(name="TestDealer", base_url="https://test.example",
                   city="Toronto", province="ON", adapter_mode="stub", scrape_config={})
        s.add(d); s.commit()
        dealer_id = d.id

    # ---- Day 1: 10 cars ----
    r = run(dealer_id, [car(f"S{i}", 20000 + i * 100) for i in range(1, 11)])
    assert r.status == "completed" and r.new_count == 10, (r.status, r.new_count)

    # ---- Day 2: S1..S8 (+price drop on S1) + S11; S9,S10 removed ----
    day2 = [car(f"S{i}", 20000 + i * 100) for i in range(1, 9)]
    day2[0]["price"] = 18500
    day2.append(car("S11", 25000))
    r = run(dealer_id, day2)
    assert r.new_count == 1 and r.removed_count == 2 and r.price_changed_count == 1, \
        (r.new_count, r.removed_count, r.price_changed_count)
    assert r.sold_count == 0, r.sold_count  # not sold yet (removed just now)

    # Backdate S9,S10 removal so they cross the SOLD_AFTER_DAYS threshold
    with SessionLocal() as s:
        for eid in ("S9", "S10"):
            c = s.execute(select(Car).where(Car.dealer_id == dealer_id, Car.external_id == eid)).scalar_one()
            c.removed_at = datetime.now(timezone.utc) - timedelta(days=10)
        s.commit()

    # ---- Day 3: same inventory -> S9,S10 auto-marked SOLD ----
    r = run(dealer_id, day2)
    assert r.sold_count == 2, r.sold_count

    with SessionLocal() as s:
        sold = s.execute(select(Car).where(Car.dealer_id == dealer_id, Car.status == "sold")).scalars().all()
        assert {c.external_id for c in sold} == {"S9", "S10"}
        assert all(c.sold_at is not None and c.removed_at is not None for c in sold)

        # price history: S1 had initial + drop
        s1 = s.execute(select(Car).where(Car.dealer_id == dealer_id, Car.external_id == "S1")).scalar_one()
        hist = s.execute(select(CarPriceHistory).where(CarPriceHistory.car_id == s1.id)).scalars().all()
        assert len(hist) == 2 and s1.price == Decimal("18500.00"), (len(hist), s1.price)

        # audit trail
        events = s.execute(select(VehicleAuditLog.event_type, func.count())
                           .group_by(VehicleAuditLog.event_type)).all()
        ev = dict(events)
        assert ev.get("created") == 11, ev          # S1..S11
        assert ev.get("removed") == 2, ev            # S9,S10
        assert ev.get("sold") == 2, ev               # S9,S10
        assert ev.get("price_changed") == 1, ev      # S1

        # daily snapshot exists
        snap = s.execute(select(DailyInventorySnapshot).where(
            DailyInventorySnapshot.dealer_id == dealer_id)).scalars().all()
        assert len(snap) == 1 and snap[0].sync_status == "success", snap
        assert snap[0].total_active == 9, snap[0].total_active  # S1..S8 + S11

        # dealer denormalized fields
        d = s.get(Dealer, dealer_id)
        assert d.inventory_count == 9 and d.last_synced_at is not None

    # ---- Day 4: S9 reappears -> active ----
    r = run(dealer_id, day2 + [car("S9", 19900)])
    assert r.reappeared_count == 1, r.reappeared_count
    with SessionLocal() as s:
        s9 = s.execute(select(Car).where(Car.dealer_id == dealer_id, Car.external_id == "S9")).scalar_one()
        assert s9.status == "active" and s9.removed_at is None and s9.sold_at is None
        assert s9.exterior_color == "Black"

    print("SMOKE TEST PASSED ✔  dedup · removals · sold · reappearance · price history · audit trail · snapshot")


if __name__ == "__main__":
    main()
