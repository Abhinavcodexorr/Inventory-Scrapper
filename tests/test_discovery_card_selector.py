"""Choosing the card container that holds a vehicle's details.

`extract: "dom"` reads year/make/model/price out of the element wrapping each
detail link. Discovery never emitted a `card_selector`, so the scraper fell back
to climbing a fixed number of ancestors and taking the first chatty one. On
cardora.ca that was a badge reading "SOLD": 528 vehicles collected, every field
NULL, and the run reported success.

These cover the selection logic only — the DOM walk itself is exercised against
live pages, since fixtures of a rendered SPA rot quickly.

Run:  python -m tests.test_discovery_card_selector
"""
from __future__ import annotations

from dealer_scraper.discovery.browser.cards import choose_card_selector


def _card(selectors: list[str], vehicles: int = 1) -> dict:
    return {"depth": 3, "vehicles": vehicles, "selectors": selectors, "textLen": 300}


# The real cardora.ca / grcars.ca shape.
_REAL = ["div.vehicle.list-view.new-vehicle.publish", "div.vehicle",
         "div.list-view", "div.new-vehicle", "div.publish"]


def test_picks_the_class_shared_by_every_card() -> None:
    assert choose_card_selector([_card(_REAL) for _ in range(20)]) == "div.vehicle"


def test_per_item_state_classes_lose_to_the_shared_one() -> None:
    """The sampled cards come off the top of the listing and are often all the
    same kind, so a compound selector absorbs state ("new-vehicle") and then
    matches none of the used vehicles further down."""
    mixed = [_card(["div.vehicle.new-vehicle", "div.vehicle", "div.new-vehicle"])
             for _ in range(10)]
    mixed += [_card(["div.vehicle.used-vehicle", "div.vehicle", "div.used-vehicle"])
              for _ in range(10)]
    assert choose_card_selector(mixed) == "div.vehicle"


def test_a_container_holding_many_vehicles_is_rejected() -> None:
    """Overshooting into the results grid would hand every vehicle the whole
    page's text, which parses into one wrong answer repeated N times."""
    grid = [_card(["ol.results", "div.grid"], vehicles=48) for _ in range(20)]
    assert choose_card_selector(grid) is None


def test_multiple_links_to_the_SAME_vehicle_are_fine() -> None:
    """A card routinely links one vehicle three times (image, title, details);
    that must not look like a grid."""
    assert choose_card_selector([_card(_REAL, vehicles=1) for _ in range(8)]) == "div.vehicle"


def test_too_few_samples_gives_no_answer() -> None:
    """A guess is worse than the scraper's own fallback: a wrong selector yields
    confidently empty rows."""
    assert choose_card_selector([]) is None
    assert choose_card_selector([_card(_REAL)]) is None
    assert choose_card_selector([_card(_REAL), _card(_REAL)]) is None


def test_inconsistent_markup_gives_no_answer() -> None:
    """No repeating card shape means no card_selector, rather than whichever
    class happened to appear first."""
    noisy = [_card([f"div.item-{i}"]) for i in range(12)]
    assert choose_card_selector(noisy) is None


def test_cards_without_usable_classes_give_no_answer() -> None:
    assert choose_card_selector([_card([]) for _ in range(12)]) is None


def test_a_minority_shape_does_not_win() -> None:
    """Two thirds of cards share div.card; a handful of oddities must not."""
    samples = [_card(["div.card"]) for _ in range(14)]
    samples += [_card(["div.promo"]) for _ in range(4)]
    assert choose_card_selector(samples) == "div.card"


def main() -> None:
    test_picks_the_class_shared_by_every_card()
    test_per_item_state_classes_lose_to_the_shared_one()
    test_a_container_holding_many_vehicles_is_rejected()
    test_multiple_links_to_the_SAME_vehicle_are_fine()
    test_too_few_samples_gives_no_answer()
    test_inconsistent_markup_gives_no_answer()
    test_cards_without_usable_classes_give_no_answer()
    test_a_minority_shape_does_not_win()
    print("PASSED")


if __name__ == "__main__":
    main()
