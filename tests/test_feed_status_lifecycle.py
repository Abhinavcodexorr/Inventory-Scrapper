"""A status the feed states must stay stated, run after run.

Cardora's feed: 698 rows — 520 Sold, 172 Instock, 6 Unpublished. The dealer's
own site lists 692 (everything but Unpublished). Our dashboard said 677 active
and every run reported `sold 510`, because:

* run 1 stored Sold rows as sold (correct);
* run 2 saw an existing car with status "sold" and called it a REAPPEARANCE,
  flipping it to active;
* run 3 saw feed=sold vs car=active, flipped it to sold (counting a sale), then
  the same reappearance branch flipped it straight back.

GR Cars: 135 rows incl. 5 "Archive". The word was not in the vocabulary, so the
rows were kept as active (site 126, tool 131).

Run:  DATABASE_URL="sqlite:///./sync_test.db" python -m tests.test_feed_status_lifecycle
"""
from __future__ import annotations

from sqlalchemy import select

from dealer_scraper.adapters.stub import StubAdapter
from dealer_scraper.db import SessionLocal, engine
from dealer_scraper.engine import run_dealer
from dealer_scraper.models import Base, Car, Dealer, VehicleAuditLog
from dealer_scraper.normalize import map_source_status
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


def _run(dealer_id: int, items: list[dict]):
    with SessionLocal() as s:
        return run_dealer(s, s.get(Dealer, dealer_id), adapter=StubAdapter(items))


def _status(dealer_id: int) -> dict[str, str]:
    with SessionLocal() as s:
        return {c.external_id: c.status for c in s.execute(
            select(Car).where(Car.dealer_id == dealer_id)).scalars()}


def test_a_sold_vehicle_stays_sold_on_every_later_run() -> None:
    did = _dealer("staysold")
    feed = [_row("A", "Instock"), _row("B", "Sold")]
    for _ in range(4):
        _run(did, feed)
        assert _status(did) == {"A": "active", "B": "sold"}, _status(did)


def test_repeat_runs_do_not_count_phantom_sales_or_reactivations() -> None:
    did = _dealer("nochurn")
    feed = [_row("A", "Instock"), _row("B", "Sold")]
    _run(did, feed)
    second = _run(did, feed)
    third = _run(did, feed)
    for run in (second, third):
        assert run.sold_count == 0, f"re-counted a sale that already happened: {run.sold_count}"
        assert run.reappeared_count == 0, f"reactivated {run.reappeared_count} cars with no news"
    with SessionLocal() as s:
        reactivated = s.execute(select(VehicleAuditLog).where(
            VehicleAuditLog.dealer_id == did, VehicleAuditLog.event_type == "reactivated")).scalars().all()
    assert reactivated == [], "a stable feed must write no 'reactivated' audit rows"


def test_a_sale_is_counted_once_when_the_feed_first_reports_it() -> None:
    did = _dealer("onesale")
    _run(did, [_row("A", "Instock")])
    run = _run(did, [_row("A", "Sold")])
    assert run.sold_count == 1 and _status(did) == {"A": "sold"}
    assert _run(did, [_row("A", "Sold")]).sold_count == 0


def test_a_sold_vehicle_the_feed_relists_becomes_active_again() -> None:
    did = _dealer("relisted")
    _run(did, [_row("A", "Sold")])
    run = _run(did, [_row("A", "Instock")])
    assert _status(did) == {"A": "active"} and run.reappeared_count == 1


def test_archive_is_a_hidden_state_not_live_inventory() -> None:
    assert map_source_status("Archive") == "hidden"
    assert map_source_status("Unpublished") == "hidden"
    did = _dealer("archive")
    _run(did, [_row("A", "Instock"), _row("B", "Instock"), _row("C", "Instock")])
    _run(did, [_row("A", "Instock"), _row("B", "Instock"), _row("C", "Archive")])
    assert _status(did)["C"] == "removed" and _status(did)["A"] == "active"


def test_a_feed_without_a_status_still_infers_reappearance() -> None:
    """Clutch and Convertus publish no status; the old inference must survive."""
    did = _dealer("inferred")
    plain = lambda stock: {k: v for k, v in _row(stock, "x").items() if k != "source_status"}  # noqa: E731
    _run(did, [plain("A"), plain("B")])
    _run(did, [plain("A")])
    assert _status(did)["B"] == "removed"
    _run(did, [plain("A"), plain("B")])
    assert _status(did)["B"] == "active"


def test_hidden_rows_are_counted_in_the_runs_stats() -> None:
    """The UI explains "1,090 scraped, 104 listed" from this counter."""
    did = _dealer("hiddencount")
    run = _run(did, [_row("A", "Instock"), _row("B", "Sold"),
                     _row("C", "Archive"), _row("D", "Archive"), _row("E", "Unpublished")])
    assert run.scraped_count == 5
    assert run.stats["counters"]["hidden"] == 3, run.stats
    assert len(_status(did)) == 2, "hidden rows must not be stored"


def main() -> None:
    require_disposable_db()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    test_a_sold_vehicle_stays_sold_on_every_later_run()
    test_repeat_runs_do_not_count_phantom_sales_or_reactivations()
    test_a_sale_is_counted_once_when_the_feed_first_reports_it()
    test_a_sold_vehicle_the_feed_relists_becomes_active_again()
    test_archive_is_a_hidden_state_not_live_inventory()
    test_a_feed_without_a_status_still_infers_reappearance()
    test_hidden_rows_are_counted_in_the_runs_stats()
    print("FEED STATUS LIFECYCLE TESTS PASSED — a stated status stays stated")


if __name__ == "__main__":
    main()
