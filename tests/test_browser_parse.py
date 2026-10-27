"""Unit test for the browser adapter's card parser (no browser needed).

Uses real card text captured live from clutch.ca, in the space-separated form
the adapter's in-page collector produces.

Run:  python -m tests.test_browser_parse
"""
from __future__ import annotations

from dealer_scraper.adapters.registry import build_adapter
from dealer_scraper.adapters.browser import parse_card_text

# As produced by the in-page text-node collector (spaces between spans):
SAMPLE = "Compare Sale favorite 2024 Chrysler Pacifica Touring-L • 30,690 km $44,990 $48,690 info $305 /biweekly $0 down Excl. HST & Licensing"


def main() -> None:
    d = parse_card_text(SAMPLE)
    assert d["year"] == "2024", d
    assert d["make"] == "Chrysler", d
    assert d["model"] == "Pacifica", d
    assert d["trim"] == "Touring-L", d
    assert d["mileage"] == "30690", d
    assert d["price"] == "44990", d  # sale price (first $ amount)

    # adapter builds with no Playwright installed (lazy import), and maps a full record
    a = build_adapter("browser", {
        "list_url": "https://www.clutch.ca/cars", "base_url": "https://www.clutch.ca",
        "id_regex": r"/vehicles/(\d+)", "card_anchor": "a[href*='/vehicles/']",
    })
    v = a._to_vehicle({"href": "/vehicles/99152", "text": SAMPLE})
    assert v.external_id == "99152", v.external_id
    assert v.url == "https://www.clutch.ca/vehicles/99152", v.url
    assert str(v.price) == "44990" and v.year == 2024 and v.make == "Chrysler", v

    test_sold_badge_marks_card_sold()
    test_relative_page_url_is_made_absolute()
    test_name_before_year_read_from_link()
    print("BROWSER PARSE TEST PASSED ✔  year/make/model/trim/mileage/price + record mapping")


def test_sold_badge_marks_card_sold() -> None:
    # Captured live from cardora.ca's listing, which mixes its sold archive in.
    d = parse_card_text("Sold 2019 Buick Envision PREMIUM 78,245 KM Stock #: R4671A")
    assert d["source_status"] == "Sold" and d["year"] == "2019" and d["make"] == "Buick", d

    a = build_adapter("browser", {"list_url": "https://cardora.ca/inventory",
                                  "base_url": "https://cardora.ca",
                                  "id_regex": r"/inventory/(\d+)-"})
    v = a._to_vehicle({"href": "/inventory/2445-2019-buick-envision",
                       "text": "Sold 2019 Buick Envision PREMIUM 78,245 KM"})
    assert v.source_status == "sold", v

    # A for-sale card, and one merely mentioning the word, stay unstated.
    assert "source_status" not in parse_card_text(SAMPLE)
    assert "source_status" not in parse_card_text("2021 Ford Mustang GT — Sold by Cardora $38,990")



def test_relative_page_url_is_made_absolute() -> None:
    """approvalgenie.ca: a path-only page_url failed every load as an invalid URL."""
    from dealer_scraper.adapters.browser import BrowserAdapter
    a = BrowserAdapter.__new__(BrowserAdapter)
    a.base_url = "https://www.approvalgenie.ca"
    a.cfg = {"list_url": "https://www.approvalgenie.ca/used-car-inventory",
             "page_url": "/used-car-inventory/?results=24&page={page}"}
    assert a._page_url(3) == "https://www.approvalgenie.ca/used-car-inventory/?results=24&page=3"
    a.cfg["page_url"] = "https://other.example/cars?page={page}"
    assert a._page_url(2) == "https://other.example/cars?page=2"



def test_name_before_year_read_from_link() -> None:
    """approvalgenie.ca cards print the name before the year; the link confirms it."""
    from dealer_scraper.adapters.browser import name_from_link
    text = "$1 $0.02/month \uff0a Mercedes-Benz GLC 300 $1 ($0.02/month \uff0a ) 2021 61,020 km Gasoline"
    href = "/used-car-inventory/Ab12Cd34/Mercedes-Benz-GLC-300-2-0-GLC300-4M/"
    assert name_from_link(text, href) == {"make": "Mercedes-Benz", "model": "GLC 300"}
    assert name_from_link("BMW 3 Series 2019", "/inv/x/BMW-3-Series-2-0-330i/") == {"make": "BMW", "model": "3 Series"}
    assert name_from_link("Great deal 2019 98,000 km", "/inv/x/Kia-Soul-EX/") == {}
    card = "$1 $0.02/month \uff0a Nissan Kicks $1 ($0.02/month \uff0a ) 2024 75,594 km"
    assert name_from_link(card, "/inv/x/Nissan-Kicks-1-6-SV/") == {"make": "Nissan", "model": "Kicks"}
    assert name_from_link(card, "/inv/x/Nissan-Kicks/") == {"make": "Nissan", "model": "Kicks"}


if __name__ == "__main__":
    main()
