"""Client-requested fields: "Last updated date" and "Discount price".

The distinction that matters commercially:

  last_seen_at  moves on EVERY sync — it proves the data is fresh, and reads
                "today" for every active car forever.
  updated_at    moves ONLY when the listing's content actually changed — it is
                what tells you a listing is stale or newly repriced.

Getting these two confused would make "Last updated" useless on a comparison
screen, so the difference is asserted directly.

Run:  DATABASE_URL="sqlite:///./.upd_test.db" python -m tests.test_updated_at_and_discount
"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select

from dealer_scraper.adapters.browser import parse_card_text
from dealer_scraper.adapters.stub import StubAdapter
from dealer_scraper.db import SessionLocal, engine
from dealer_scraper.engine import run_dealer
from dealer_scraper.models import Base, Car, Dealer
from dealer_scraper.normalize import normalize

from . import require_disposable_db


def _car(stock: str, price, **extra) -> dict:
    base = {"external_id": stock, "vin": f"VIN{stock}", "make": "Honda",
            "model": "Civic", "year": 2020, "trim": "EX", "mileage": 10000,
            "price": price, "url": f"https://x/{stock}"}
    base.update(extra)
    return base


def _fresh_dealer() -> int:
    require_disposable_db()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with SessionLocal() as s:
        d = Dealer(name="D", base_url="https://d.example",
                   adapter_mode="stub", scrape_config={})
        s.add(d)
        s.commit()
        return d.id


def _run(dealer_id: int, items: list[dict]):
    with SessionLocal() as s:
        return run_dealer(s, s.get(Dealer, dealer_id), adapter=StubAdapter(items))


def _get(dealer_id: int, stock: str) -> Car:
    with SessionLocal() as s:
        return s.execute(
            select(Car).where(Car.dealer_id == dealer_id, Car.external_id == stock)
        ).scalar_one()


# --------------------------------------------------------------- updated_at
def test_updated_at_is_set_when_a_vehicle_is_first_created() -> None:
    dealer_id = _fresh_dealer()
    _run(dealer_id, [_car("S1", 20000)])
    car = _get(dealer_id, "S1")
    assert car.updated_at is not None, "never leave the UI with a blank Last updated"


def test_unchanged_listing_bumps_last_seen_but_not_updated_at() -> None:
    """The core distinction — a daily re-sighting is not an update."""
    dealer_id = _fresh_dealer()
    _run(dealer_id, [_car("S1", 20000)])
    first = _get(dealer_id, "S1")
    original_updated, original_seen = first.updated_at, first.last_seen_at

    _run(dealer_id, [_car("S1", 20000)])           # identical listing
    after = _get(dealer_id, "S1")

    assert after.updated_at == original_updated, "unchanged listing must not look updated"
    assert after.last_seen_at >= original_seen, "but we did check it again"


def test_price_change_moves_updated_at() -> None:
    dealer_id = _fresh_dealer()
    _run(dealer_id, [_car("S1", 20000)])
    before = _get(dealer_id, "S1").updated_at

    _run(dealer_id, [_car("S1", 18500)])
    after = _get(dealer_id, "S1")

    assert after.updated_at > before, "a repriced listing IS updated"
    assert after.price == Decimal("18500.00")


# ----------------------------------------------------------- discount price
def test_discount_is_captured_when_a_listing_is_on_sale() -> None:
    dealer_id = _fresh_dealer()
    _run(dealer_id, [_car("S1", 49799, original_price=54600)])
    car = _get(dealer_id, "S1")
    assert car.price == Decimal("49799.00")
    assert car.original_price == Decimal("54600.00")


def test_no_discount_leaves_original_price_null() -> None:
    """`original_price is not None` must mean "on sale" — so a site echoing the
    same figure twice, or a lower one, must not invent a discount."""
    dealer_id = _fresh_dealer()
    _run(dealer_id, [_car("S1", 20000, original_price=20000),
                     _car("S2", 20000, msrp=19000)])
    assert _get(dealer_id, "S1").original_price is None, "equal price is not a discount"
    assert _get(dealer_id, "S2").original_price is None, "lower price is not a discount"


def test_discount_appearing_counts_as_a_listing_change() -> None:
    """Asking price unchanged, but the car went on sale — that is a real change."""
    dealer_id = _fresh_dealer()
    _run(dealer_id, [_car("S1", 20000)])
    before = _get(dealer_id, "S1").updated_at

    run = _run(dealer_id, [_car("S1", 20000, original_price=24000)])
    after = _get(dealer_id, "S1")

    assert run.updated_count == 1, "a new discount must register as an update"
    assert after.updated_at > before
    assert after.original_price == Decimal("24000.00")


def test_alternate_labels_map_to_original_price() -> None:
    for key in ("msrp", "retail_price", "was_price", "list_price"):
        v = normalize({"external_id": "X", "price": 30000, key: 35000})
        assert v.original_price == Decimal("35000"), key


def test_card_text_keeps_the_struck_through_price() -> None:
    """Two amounts on a card: the lower is asking, the higher is pre-discount.
    Previously the higher one was parsed and discarded."""
    parsed = parse_card_text(
        "Sale 2024 Chrysler Pacifica Touring-L • 30,690 km $44,990 $48,690"
    )
    assert parsed["price"] == "44990"
    assert parsed.get("original_price") == "48690"


def main() -> None:
    test_updated_at_is_set_when_a_vehicle_is_first_created()
    test_unchanged_listing_bumps_last_seen_but_not_updated_at()
    test_price_change_moves_updated_at()
    test_discount_is_captured_when_a_listing_is_on_sale()
    test_no_discount_leaves_original_price_null()
    test_discount_appearing_counts_as_a_listing_change()
    test_alternate_labels_map_to_original_price()
    test_card_text_keeps_the_struck_through_price()
    print("UPDATED_AT + DISCOUNT TESTS PASSED — last-checked vs last-updated, discount capture")


if __name__ == "__main__":
    main()
