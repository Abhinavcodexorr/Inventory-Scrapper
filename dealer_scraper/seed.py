"""Insert / update the clutch.ca dealer row.

    python -m dealer_scraper.seed

clutch.ca is a JS-rendered site behind AWS WAF bot protection, so it uses the
"browser" adapter (Playwright) rather than the plain json_api adapter. The
selectors below were confirmed against the live site (detail links are
/vehicles/<id>; cards render year/make/model/trim/mileage/price).

Listing-level fields only. VIN, colors, transmission, drivetrain, etc. live on
each vehicle's detail page and require the detail-scrape pass (Phase 2).

Requires:  pip install playwright  &&  playwright install chromium
"""
from __future__ import annotations

from sqlalchemy import select

from .db import SessionLocal
from .models import Dealer

CLUTCH_CONFIG = {
    "list_url": "https://www.clutch.ca/cars",
    "base_url": "https://www.clutch.ca",
    "wait_selector": "a[href*='/vehicles/']",
    "card_anchor": "a[href*='/vehicles/']",
    "id_regex": r"/vehicles/(\d+)",
    "extract": "jsonld",           # parse schema.org/Vehicle JSON-LD (clean) instead of card text
    "paginate": "url",             # Clutch paginates via ?page=N, 32 cars/page (~99 pages)
    "page_url": "https://www.clutch.ca/cars?page={page}",
    "max_pages": 120,              # safety cap (3,161 / 32 ~= 99 pages)
    "settle_ms": 2500,             # give each page's grid time to fetch behind the WAF
    "headless": False,             # visible browser passes Clutch's AWS WAF challenge; set True later if it works headless
    "debug_screenshot": "scrape_debug.png",
    "nav_timeout": 45000,
    # "total_selector": "",        # optional: element showing the "3,915 results" count
}


def main() -> None:
    with SessionLocal() as session:
        dealer = session.execute(
            select(Dealer).where(Dealer.base_url == "https://www.clutch.ca")
        ).scalar_one_or_none()
        if dealer is None:
            dealer = Dealer(name="Clutch", base_url="https://www.clutch.ca",
                            city="Toronto", province="ON")
            session.add(dealer)
        dealer.website_provider = "Clutch"
        dealer.adapter_mode = "browser"
        dealer.scrape_config = CLUTCH_CONFIG
        dealer.active = True
        session.commit()
        print(f"Seeded dealer #{dealer.id}: {dealer.name} (adapter=browser)")


if __name__ == "__main__":
    main()
