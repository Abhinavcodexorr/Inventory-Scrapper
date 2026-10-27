"""The dealer's inventory count and daily snapshot describe THIS run, not the last.

The session is built with autoflush=False, so a status change made during a run
(sold, removed) is still pending when `_active_count` selects. The count then
described the database as it stood before the run:

* Gedi Route: inventory_count 128 while 46 were really active (77 had just
  become sold and 5 removed in that very run).
* Tabangi: snapshot 602 vs 576 active.

The dashboard's per-dealer total reads this value, so it never matched the
Inventory page, which counts rows.

Run:  DATABASE_URL="sqlite:///./sync_test.db" python -m tests.test_inventory_count_current
"""
from __future__ import annotations

from sqlalchemy import select

from dealer_scraper.adapters.stub import StubAdapter
from dealer_scraper.db import SessionLocal, engine
from dealer_scraper.engine import run_dealer
from dealer_scraper.models import Base, Car, DailyInventorySnapshot, Dealer
from . import require_disposable_db


def _row(stock: str, status: str) -> dict:
    return {"external_id": stock, "make": "Honda", "model": "Civic", "year": 2020,
            "price": "20000", "source_status": status}


def _dealer(name: str) -> int:
    with SessionLocal() as s:
        d = Dealer(name=name, base_url=f"https://{name}.example", adapter_mode="stub", scrape_config={})
        s.add(d)
        s.commit()
        return d.id


def _run(dealer_id: int, items: list[dict]) -> None:
    with SessionLocal() as s:
        run_dealer(s, s.get(Dealer, dealer_id), adapter=StubAdapter(items))


def _truth(dealer_id: int) -> tuple[int, int, int]:
    """(real active rows, dealer.inventory_count, latest snapshot.total_active)"""
    with SessionLocal() as s:
        active = len(s.execute(select(Car).where(
            Car.dealer_id == dealer_id, Car.status == "active")).scalars().all())
        count = s.get(Dealer, dealer_id).inventory_count
        snap = s.execute(select(DailyInventorySnapshot).where(
            DailyInventorySnapshot.dealer_id == dealer_id)
            .order_by(DailyInventorySnapshot.id.desc())).scalars().first()
        return active, count, snap.total_active


def test_a_run_that_sells_vehicles_reports_the_count_after_the_sales() -> None:
    did = _dealer("sales")
    _run(did, [_row(c, "Instock") for c in "ABCDE"])
    _run(did, [_row("A", "Instock"), _row("B", "Sold"), _row("C", "Sold"),
               _row("D", "Instock"), _row("E", "Instock")])
    assert _truth(did) == (3, 3, 3), _truth(did)


def test_a_run_that_removes_vehicles_reports_the_count_after_the_removals() -> None:
    did = _dealer("removals")
    _run(did, [_row(c, "Instock") for c in "ABCDEFGHIJ"])
    _run(did, [_row(c, "Instock") for c in "ABCDEFGHI"])
    assert _truth(did) == (9, 9, 9), _truth(did)


def main() -> None:
    require_disposable_db()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    test_a_run_that_sells_vehicles_reports_the_count_after_the_sales()
    test_a_run_that_removes_vehicles_reports_the_count_after_the_removals()
    print("INVENTORY COUNT TESTS PASSED — the count describes this run, not the last one")


if __name__ == "__main__":
    main()
