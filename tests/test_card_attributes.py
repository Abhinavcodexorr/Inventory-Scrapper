"""Read vehicle fields from card data-* attributes, not just rendered text.

The client declined detail-page scraping, so VIN / colour / transmission were
documented as permanently NULL. That is only true of the card's visible TEXT.
Convertus (and DealerOn, and Dealer.com) publish the whole vehicle as data-*
attributes on the card element for their own analytics layer — VIN, odometer,
colour, transmission, stock number, price, all of it.

The first Tabangi sync also showed why text parsing is fragile even for the
fields it does cover: the card reads "Kilometres: 53,862", and `_MILEAGE`
wants the number BEFORE the unit, so mileage came back NULL on every row.

Run:  python -m tests.test_card_attributes
"""
from __future__ import annotations

from dealer_scraper.adapters.browser import _MILEAGE, parse_card_attrs, parse_card_text

# Verbatim from a tabangimotors.com .vehicle-card element, camelCased the way
# the DOM `dataset` API delivers it.
TABANGI_ATTRS = {
    "vehicleId": "70833715",
    "vehicleVin": "4JGFB4FB1TB533371",
    "vehicleYear": "2026",
    "vehicleMake": "Mercedes-Benz",
    "vehicleModel": "GLE",
    "vehicleTrim": "350 4MATIC  No Accident AMG Burmester 360CAM HUD Vented Seats",
    "vehicleOdo": "53862",
    "vehicleColour": "White",
    "vehicleTransmission": "9-Speed Automatic",
    "vehicleFueltype": "Gas/Electric Hybrid",
    "vehicleBodyStyle": "SUV",
    "vehicleStock": "20654",
    "vehicleInternetPrice": "64995",
    "vehicleMsrp": "66995",
    "vehicleCondition": "Used",
    "vehiclePos": "501",
}

CARD_TEXT = ("2026 Mercedes-Benz GLE 350 4MATIC No Accident AMG Burmester "
             "Located in Mississauga Kilometres: 53,862 Stock #: 20654 "
             "VIN: 4JGFB4FB1TB533371")


def test_the_mileage_regex_really_does_miss_this_card() -> None:
    """Pins the defect the attributes work around, so nobody "simplifies" this
    back to text-only parsing later."""
    assert _MILEAGE.search("30,690 km") is not None
    assert _MILEAGE.search("Kilometres: 53,862") is None


def test_attributes_supply_the_fields_text_cannot() -> None:
    got = parse_card_attrs(TABANGI_ATTRS)
    assert got["vin"] == "4JGFB4FB1TB533371"
    assert got["exterior_color"] == "White"
    assert got["transmission"] == "9-Speed Automatic"
    assert got["mileage"] == "53862"
    assert got["stock_number"] == "20654"


def test_attributes_supply_the_ordinary_fields_too() -> None:
    got = parse_card_attrs(TABANGI_ATTRS)
    assert got["year"] == "2026"
    assert got["make"] == "Mercedes-Benz"
    assert got["model"] == "GLE"
    assert got["price"] == "64995"
    assert got["original_price"] == "66995"


def test_unknown_attributes_are_ignored() -> None:
    """A card carries analytics noise (position, list name, store id). None of
    it should reach a vehicle field."""
    got = parse_card_attrs(TABANGI_ATTRS)
    assert "vehiclePos" not in got and "pos" not in got
    assert set(got) <= {
        "vin", "year", "make", "model", "trim", "mileage", "exterior_color",
        "interior_color", "transmission", "fuel_type", "body_type",
        "drivetrain", "stock_number", "price", "original_price",
    }


def test_empty_and_placeholder_values_are_skipped() -> None:
    """An absent attribute must not overwrite something the text found."""
    got = parse_card_attrs({"vehicleColour": "", "vehiclePrice": "0",
                            "vehicleVin": "  ", "vehicleMake": "Audi"})
    assert got == {"make": "Audi"}


def test_no_attributes_is_not_an_error() -> None:
    assert parse_card_attrs({}) == {}
    assert parse_card_attrs(None) == {}


def test_attributes_win_over_text_but_only_where_present() -> None:
    """The merge rule in _to_vehicle: attributes are more trustworthy, but a
    platform that publishes only two of them must not blank the rest."""
    raw = parse_card_text(CARD_TEXT)
    raw.update(parse_card_attrs({"vehicleColour": "White"}))
    assert raw["exterior_color"] == "White"
    assert raw.get("make") == parse_card_text(CARD_TEXT).get("make")


def main() -> None:
    test_the_mileage_regex_really_does_miss_this_card()
    test_attributes_supply_the_fields_text_cannot()
    test_attributes_supply_the_ordinary_fields_too()
    test_unknown_attributes_are_ignored()
    test_empty_and_placeholder_values_are_skipped()
    test_no_attributes_is_not_an_error()
    test_attributes_win_over_text_but_only_where_present()
    print("CARD ATTRIBUTE TESTS PASSED — VIN/colour/transmission now come from the listing")


if __name__ == "__main__":
    main()
