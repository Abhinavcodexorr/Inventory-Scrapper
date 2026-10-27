"""When a feed states a vehicle is sold, believe it.

`CLAUDE.md` records that "sold" can only ever be inferred — a vehicle that
disappears and stays gone for SOLD_AFTER_DAYS. That is true of Clutch and the
Convertus sites, which list only what is for sale.

It is not true of the Zop feed. It returns Instock / Sold / Unpublished, and
482 of Cardora's 645 rows are Sold. Filtering to Instock stored 158 vehicles
against a dealer page advertising 640, which reads as a broken scraper; storing
all 645 as "active" would have tripled live inventory instead. Neither is
right: the feed knows, so the feed decides.

Run:  python -m tests.test_feed_reported_status
"""
from __future__ import annotations

from dealer_scraper.normalize import map_source_status, normalize


def test_zop_vocabulary_maps_to_ours() -> None:
    assert map_source_status("Instock") == "active"
    assert map_source_status("Sold") == "sold"
    assert map_source_status("Unpublished") == "hidden"


def test_spelling_variants_land_on_the_same_state() -> None:
    """Platforms disagree on spacing and casing for the same concept."""
    for value in ("In Stock", "in-stock", "INSTOCK", "in_stock", " Available "):
        assert map_source_status(value) == "active", value


def test_in_transit_and_on_order_count_as_available() -> None:
    """They are listed and purchasable, so they belong in live inventory —
    the scrape_config asks the API for them explicitly."""
    assert map_source_status("In Transit") == "active"
    assert map_source_status("On Order") == "active"


def test_an_unknown_word_says_nothing_rather_than_guessing() -> None:
    """A source we don't recognise must leave the existing inference alone,
    not silently assert a state. Returning 'active' here would resurrect
    genuinely sold cars on every sync."""
    assert map_source_status("Layaway") is None
    assert map_source_status("") is None
    assert map_source_status(None) is None


def test_normalize_carries_the_status_through() -> None:
    v = normalize({"external_id": "A1", "make": "Audi", "source_status": "Sold"})
    assert v.source_status == "sold"


def test_a_feed_that_says_nothing_leaves_status_unset() -> None:
    """Clutch and Convertus publish no status field; those vehicles must keep
    the old behaviour, where absence from the listing is the only signal."""
    v = normalize({"external_id": "A1", "make": "Audi"})
    assert v.source_status is None


def test_status_is_not_part_of_the_content_hash() -> None:
    """Selling a car is a lifecycle event, not a listing edit. If status fed
    the hash, every sale would also register as "listing updated" noise in the
    changes feed."""
    sold = normalize({"external_id": "A1", "make": "Audi", "price": "100",
                      "source_status": "Sold"})
    live = normalize({"external_id": "A1", "make": "Audi", "price": "100",
                      "source_status": "Instock"})
    assert sold.content_hash() == live.content_hash()


def main() -> None:
    test_zop_vocabulary_maps_to_ours()
    test_spelling_variants_land_on_the_same_state()
    test_in_transit_and_on_order_count_as_available()
    test_an_unknown_word_says_nothing_rather_than_guessing()
    test_normalize_carries_the_status_through()
    test_a_feed_that_says_nothing_leaves_status_unset()
    test_status_is_not_part_of_the_content_hash()
    print("FEED STATUS TESTS PASSED — a stated sale is believed, an unknown word is not")


if __name__ == "__main__":
    main()
