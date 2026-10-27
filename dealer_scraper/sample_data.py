"""Insert realistic sample dealers + inventory for local UI/API testing.

    python -m dealer_scraper.sample_data
    python -m dealer_scraper.sample_data --reset   # wipe previous sample rows first

Does not touch real scrape dealers (e.g. Clutch) unless they share a sample base_url.
Safe to re-run: upserts by sample base_url / external_id.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import delete, select

from .db import SessionLocal
from .models import (
    Car,
    CarPriceHistory,
    DailyInventorySnapshot,
    Dealer,
    ScrapeRun,
    VehicleAuditLog,
)

NOW = datetime.now(timezone.utc)
TODAY = date.today()

# Marker used to find/clear sample rows on --reset
SAMPLE_PROVIDER = "SampleData"

DEALERS = [
    {
        "name": "North York Motors",
        "base_url": "https://sample.northyorkmotors.example",
        "address": "1234 Yonge St",
        "city": "Toronto",
        "province": "ON",
        "postal_code": "M2N 5N1",
        "phone": "416-555-0101",
        "website_provider": SAMPLE_PROVIDER,
        "adapter_mode": "stub",
        "scrape_config": {"note": "sample data — not a live scrape target"},
        "active": True,
    },
    {
        "name": "Pacific Auto Group",
        "base_url": "https://sample.pacificauto.example",
        "address": "880 Burrard St",
        "city": "Vancouver",
        "province": "BC",
        "postal_code": "V6Z 1X9",
        "phone": "604-555-0142",
        "website_provider": SAMPLE_PROVIDER,
        "adapter_mode": "stub",
        "scrape_config": {"note": "sample data — not a live scrape target"},
        "active": True,
    },
    {
        "name": "Prairie Drive (Inactive)",
        "base_url": "https://sample.prairiedrive.example",
        "address": "500 8 Ave SW",
        "city": "Calgary",
        "province": "AB",
        "postal_code": "T2P 1G1",
        "phone": "403-555-0199",
        "website_provider": SAMPLE_PROVIDER,
        "adapter_mode": "stub",
        "scrape_config": {"note": "sample data — inactive dealer"},
        "active": False,
    },
]

# (external_id, year, make, model, trim, mileage, body, condition, ext, int,
#  trans, fuel, drive, price, status, days_first_seen, days_last_seen,
#  days_removed, days_sold)
# status: active | removed | sold
VEHICLES_BY_DEALER = {
    "https://sample.northyorkmotors.example": [
        ("NY-1001", 2022, "Honda", "Civic", "EX", 28500, "Sedan", "Used",
         "Crystal Black", "Black", "Automatic", "Gas", "FWD", "24990", "active", 40, 0, None, None),
        ("NY-1002", 2021, "Toyota", "RAV4", "XLE", 41200, "SUV", "Used",
         "Magnetic Grey", "Ash", "Automatic", "Gas", "AWD", "32995", "active", 35, 0, None, None),
        ("NY-1003", 2023, "Mazda", "CX-5", "GT", 15800, "SUV", "Used",
         "Soul Red", "Black", "Automatic", "Gas", "AWD", "36900", "active", 28, 0, None, None),
        ("NY-1004", 2020, "Ford", "F-150", "XLT", 67000, "Truck", "Used",
         "Oxford White", "Medium Earth Gray", "Automatic", "Gas", "4WD", "41900", "active", 50, 0, None, None),
        ("NY-1005", 2019, "BMW", "3 Series", "330i", 52000, "Sedan", "Used",
         "Alpine White", "Cognac", "Automatic", "Gas", "RWD", "28900", "active", 22, 0, None, None),
        ("NY-1006", 2024, "Hyundai", "Tucson", "Preferred", 8200, "SUV", "Used",
         "Amazon Grey", "Grey", "Automatic", "Hybrid", "AWD", "35995", "active", 5, 0, None, None),
        ("NY-1007", 2018, "Volkswagen", "Golf", "Comfortline", 89000, "Hatchback", "Used",
         "Pure White", "Titan Black", "Manual", "Gas", "FWD", "16490", "removed", 60, 8, 8, None),
        ("NY-1008", 2017, "Nissan", "Altima", "SV", 105000, "Sedan", "Used",
         "Gun Metallic", "Charcoal", "Automatic", "Gas", "FWD", "12995", "sold", 90, 20, 20, 12),
        ("NY-1009", 2022, "Kia", "Sportage", "EX", 31000, "SUV", "Used",
         "Gravity Grey", "Black", "Automatic", "Gas", "AWD", "27990", "active", 12, 0, None, None),
        ("NY-1010", 2021, "Chevrolet", "Equinox", "LT", 48000, "SUV", "Used",
         "Summit White", "Jet Black", "Automatic", "Gas", "AWD", "23900", "sold", 70, 25, 25, 18),
    ],
    "https://sample.pacificauto.example": [
        ("PA-2001", 2023, "Toyota", "Camry", "SE", 19000, "Sedan", "Used",
         "Celestial Silver", "Black", "Automatic", "Gas", "FWD", "31490", "active", 30, 0, None, None),
        ("PA-2002", 2022, "Subaru", "Outback", "Limited", 36000, "Wagon", "Used",
         "Crystal White", "Java Brown", "Automatic", "Gas", "AWD", "34995", "active", 25, 0, None, None),
        ("PA-2003", 2020, "Jeep", "Wrangler", "Sport S", 54000, "SUV", "Used",
         "Firecracker Red", "Black", "Automatic", "Gas", "4WD", "39900", "active", 45, 0, None, None),
        ("PA-2004", 2024, "Tesla", "Model 3", "RWD", 6100, "Sedan", "Used",
         "Pearl White", "Black", "Automatic", "Electric", "RWD", "44990", "active", 3, 0, None, None),
        ("PA-2005", 2019, "Audi", "Q5", "Progressiv", 61000, "SUV", "Used",
         "Mythos Black", "Okapi Brown", "Automatic", "Gas", "AWD", "32900", "removed", 55, 4, 4, None),
        ("PA-2006", 2018, "Honda", "CR-V", "Touring", 78000, "SUV", "Used",
         "Modern Steel", "Black", "Automatic", "Gas", "AWD", "24900", "sold", 80, 30, 30, 22),
        ("PA-2007", 2021, "Ford", "Escape", "SEL", 43000, "SUV", "Used",
         "Agate Black", "Ebony", "Automatic", "Hybrid", "AWD", "26990", "active", 18, 0, None, None),
        ("PA-2008", 2023, "GMC", "Sierra 1500", "Elevation", 22000, "Truck", "Used",
         "Downpour Metallic", "Jet Black", "Automatic", "Gas", "4WD", "52900", "active", 10, 0, None, None),
    ],
    "https://sample.prairiedrive.example": [
        ("PD-3001", 2020, "Ram", "1500", "Big Horn", 72000, "Truck", "Used",
         "Bright White", "Black", "Automatic", "Gas", "4WD", "38900", "active", 100, 40, None, None),
        ("PD-3002", 2019, "Dodge", "Grand Caravan", "SXT", 95000, "Van", "Used",
         "Billet Silver", "Black", "Automatic", "Gas", "FWD", "17990", "sold", 120, 60, 60, 50),
    ],
}

# Vehicles that get a multi-point price history (external_id -> [(days_ago, price), ...])
PRICE_DROPS = {
    "NY-1001": [(40, "26990"), (20, "25990"), (5, "24990")],
    "NY-1005": [(22, "30900"), (10, "28900")],
    "PA-2001": [(30, "32990"), (14, "31990"), (2, "31490")],
    "PA-2004": [(3, "46990"), (1, "44990")],
}


def _dt(days_ago: int | None) -> datetime | None:
    if days_ago is None:
        return None
    return NOW - timedelta(days=days_ago)


def _upsert_dealer(session, spec: dict) -> Dealer:
    dealer = session.execute(
        select(Dealer).where(Dealer.base_url == spec["base_url"])
    ).scalar_one_or_none()
    if dealer is None:
        dealer = Dealer(base_url=spec["base_url"])
        session.add(dealer)
    for key, value in spec.items():
        setattr(dealer, key, value)
    dealer.last_synced_at = NOW - timedelta(hours=2)
    return dealer


def _clear_sample(session) -> int:
    dealers = list(
        session.execute(
            select(Dealer).where(Dealer.website_provider == SAMPLE_PROVIDER)
        ).scalars()
    )
    if not dealers:
        return 0
    ids = [d.id for d in dealers]
    car_ids = list(
        session.execute(select(Car.id).where(Car.dealer_id.in_(ids))).scalars()
    )
    if car_ids:
        session.execute(delete(CarPriceHistory).where(CarPriceHistory.car_id.in_(car_ids)))
        session.execute(delete(VehicleAuditLog).where(VehicleAuditLog.car_id.in_(car_ids)))
    session.execute(delete(DailyInventorySnapshot).where(DailyInventorySnapshot.dealer_id.in_(ids)))
    session.execute(delete(ScrapeRun).where(ScrapeRun.dealer_id.in_(ids)))
    session.execute(delete(Car).where(Car.dealer_id.in_(ids)))
    session.execute(delete(Dealer).where(Dealer.id.in_(ids)))
    return len(ids)


def _seed_vehicle(session, dealer: Dealer, row: tuple) -> Car:
    (ext, year, make, model, trim, miles, body, cond, ext_c, int_c,
     trans, fuel, drive, price, status, first, last, removed, sold) = row

    car = session.execute(
        select(Car).where(Car.dealer_id == dealer.id, Car.external_id == ext)
    ).scalar_one_or_none()
    if car is None:
        car = Car(dealer_id=dealer.id, external_id=ext)
        session.add(car)

    car.vin = f"1HGCM{abs(hash(ext)) % 10_000_000:07d}"[:17]
    car.year = year
    car.make = make
    car.model = model
    car.trim = trim
    car.mileage = miles
    car.body_type = body
    car.condition = cond
    car.exterior_color = ext_c
    car.interior_color = int_c
    car.transmission = trans
    car.fuel_type = fuel
    car.drivetrain = drive
    car.price = Decimal(price)
    car.currency = "CAD"
    car.url = f"{dealer.base_url}/vehicles/{ext}"
    car.status = status
    car.first_seen_at = _dt(first)
    car.last_seen_at = _dt(last)
    car.removed_at = _dt(removed)
    car.sold_at = _dt(sold)
    car.content_hash = f"sample-{ext}"
    return car


def _stub_config(cars: list[Car]) -> dict:
    """Feed the 'stub' adapter the dealer's own ACTIVE listings.

    Clicking Sync on a sample dealer then exercises the real pipeline
    (adapter -> engine -> upsert -> audit -> snapshot) and re-discovers exactly
    the inventory that is already there — no network, no spurious removals.
    Built from the persisted Car rows so the identity keys (VIN / URL / stock #)
    match byte-for-byte on the next run.
    """
    return {
        "note": "sample data — replays this dealer's active listings, no live scrape",
        "items": [
            {
                "external_id": c.external_id,
                "vin": c.vin,
                "make": c.make,
                "model": c.model,
                "year": c.year,
                "trim": c.trim,
                "mileage": c.mileage,
                "body_type": c.body_type,
                "condition": c.condition,
                "exterior_color": c.exterior_color,
                "interior_color": c.interior_color,
                "transmission": c.transmission,
                "fuel_type": c.fuel_type,
                "drivetrain": c.drivetrain,
                "price": str(c.price) if c.price is not None else None,
                "currency": c.currency,
                "url": c.url,
            }
            for c in cars
            if c.status == "active"
        ],
    }


def _seed_price_history(session, car: Car, points: list[tuple[int, str]]) -> None:
    session.execute(delete(CarPriceHistory).where(CarPriceHistory.car_id == car.id))
    for days_ago, price in points:
        session.add(CarPriceHistory(
            car_id=car.id,
            price=Decimal(price),
            currency="CAD",
            recorded_at=_dt(days_ago),
        ))


def _seed_audit(session, car: Car, dealer_id: int) -> None:
    session.execute(delete(VehicleAuditLog).where(VehicleAuditLog.car_id == car.id))
    events: list[tuple[str, str | None, str | None, int, str]] = [
        ("created", None, "active", 0, "First seen in sample inventory"),
    ]
    # offset from first_seen
    base = (NOW - car.first_seen_at).days if car.first_seen_at else 30

    if car.external_id in PRICE_DROPS and len(PRICE_DROPS[car.external_id]) > 1:
        events.append(("price_changed", "active", "active", max(base - 10, 1),
                        "Asking price updated"))

    if car.status == "removed":
        days = (NOW - car.removed_at).days if car.removed_at else 4
        events.append(("removed", "active", "removed", days, "Missing from latest scrape"))
    elif car.status == "sold":
        rem = (NOW - car.removed_at).days if car.removed_at else 20
        sold = (NOW - car.sold_at).days if car.sold_at else 12
        events.append(("removed", "active", "removed", rem, "Missing from latest scrape"))
        events.append(("sold", "removed", "sold", sold, "Auto-sold after absence threshold"))

    for event_type, old_s, new_s, days_ago, note in events:
        session.add(VehicleAuditLog(
            car_id=car.id,
            dealer_id=dealer_id,
            event_type=event_type,
            old_status=old_s,
            new_status=new_s,
            note=note,
            occurred_at=_dt(days_ago) if days_ago else car.first_seen_at or NOW,
        ))


def _seed_runs_and_snapshots(session, dealer: Dealer, active_count: int) -> None:
    # Snapshots carry an FK to scrape_runs.id, so they must go first.
    session.execute(
        delete(DailyInventorySnapshot).where(DailyInventorySnapshot.dealer_id == dealer.id)
    )
    session.execute(delete(ScrapeRun).where(ScrapeRun.dealer_id == dealer.id))

    # Trend: gentle climb then a small dip over 14 days
    for i in range(14, -1, -1):
        day = TODAY - timedelta(days=i)
        # Invent plausible daily deltas
        base = max(active_count - 3, 1)
        total = base + (14 - i) // 3
        if i == 0:
            total = active_count
        added = 1 if i in (0, 3, 7, 12) else 0
        removed = 1 if i in (1, 8) else 0
        sold = 1 if i in (5, 10) else 0
        price_changed = 1 if i in (2, 6, 11) else 0
        sync = "failed" if (not dealer.active and i == 0) else "success"

        started = datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc) + timedelta(hours=3)
        finished = started + timedelta(minutes=4 + (i % 3))
        run = ScrapeRun(
            dealer_id=dealer.id,
            started_at=started,
            finished_at=finished,
            status="failed" if sync == "failed" else "completed",
            reported_total=total,
            scraped_count=total,
            new_count=added,
            updated_count=max(total - added, 0),
            price_changed_count=price_changed,
            removed_count=removed,
            reappeared_count=0,
            sold_count=sold,
            error="Dealer inactive — sample failed run" if sync == "failed" else None,
        )
        session.add(run)
        session.flush()

        session.add(DailyInventorySnapshot(
            dealer_id=dealer.id,
            snapshot_date=day,
            total_active=total,
            added=added,
            removed=removed,
            sold=sold,
            price_changed=price_changed,
            sync_status=sync,
            run_id=run.id,
        ))

    dealer.inventory_count = active_count
    dealer.last_synced_at = NOW - timedelta(hours=2)


def seed(*, reset: bool = False) -> None:
    with SessionLocal() as session:
        if reset:
            n = _clear_sample(session)
            session.commit()
            print(f"Cleared {n} sample dealer(s)")

        cars_by_ext: dict[str, Car] = {}
        for spec in DEALERS:
            dealer = _upsert_dealer(session, spec)
            session.flush()

            rows = VEHICLES_BY_DEALER[spec["base_url"]]
            seeded: list[Car] = []
            for row in rows:
                car = _seed_vehicle(session, dealer, row)
                session.flush()
                cars_by_ext[car.external_id] = car
                seeded.append(car)
                _seed_audit(session, car, dealer.id)

            # Make this dealer syncable through the normal pipeline.
            dealer.scrape_config = _stub_config(seeded)

            active_count = sum(1 for r in rows if r[14] == "active")
            _seed_runs_and_snapshots(session, dealer, active_count)
            print(
                f"Seeded dealer #{dealer.id}: {dealer.name} "
                f"({len(rows)} vehicles, {active_count} active)"
            )

        for ext, points in PRICE_DROPS.items():
            car = cars_by_ext.get(ext)
            if car is not None:
                _seed_price_history(session, car, points)

        session.commit()
        print("Sample data ready.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Load sample dealers/vehicles for local testing")
    parser.add_argument(
        "--reset",
        action="store_true",
        help=f"Delete existing dealers with website_provider={SAMPLE_PROVIDER!r} before seeding",
    )
    args = parser.parse_args()
    seed(reset=args.reset)


if __name__ == "__main__":
    main()
