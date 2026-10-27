"""Regression tests for the cross-site scrape_config guard.

Background: a dealer created by copying another dealer's provider template kept
the ORIGINAL dealer's URLs. Syncing it scraped that other site and filed its
vehicles under the new dealer — silent data corruption, invisible until someone
read the data. (Observed live: Cardora held 256 vehicles whose URLs were all
www.clutch.ca.)

The guard makes that impossible: every URL in scrape_config must belong to the
dealer's own website.

Run:  python -m tests.test_config_guard
"""
from __future__ import annotations

from dealer_scraper.adapters.registry import validate_scrape_config

CLUTCH_CFG = {
    "list_url": "https://www.clutch.ca/cars",
    "base_url": "https://www.clutch.ca",
    "page_url": "https://www.clutch.ca/cars?page={page}",
    "card_anchor": "a[href*='/vehicles/']",
    "id_regex": r"/vehicles/(\d+)",
}


def _rejects(cfg: dict, dealer_url: str, mode: str = "browser") -> str:
    try:
        validate_scrape_config(mode, cfg, dealer_url)
    except ValueError as exc:
        return str(exc)
    raise AssertionError(f"expected rejection for {dealer_url} with {cfg.get('list_url')}")


def _accepts(cfg: dict, dealer_url: str, mode: str = "browser") -> None:
    validate_scrape_config(mode, cfg, dealer_url)


def main() -> None:
    # 1. THE BUG: another dealer's template must be rejected.
    msg = _rejects(CLUTCH_CFG, "https://cardora.ca")
    assert "clutch.ca" in msg and "cardora.ca" in msg, msg
    print("  cross-site config rejected, message names both sites OK")

    # 2. The legitimate case still works.
    _accepts(CLUTCH_CFG, "https://www.clutch.ca")
    _accepts(CLUTCH_CFG, "https://clutch.ca")          # bare vs www
    print("  dealer's own config accepted (www and bare host) OK")

    # 3. Subdomains are the same dealer, in either direction.
    _accepts({"list_url": "https://inventory.cardora.ca/cars", "card_anchor": "a",
              "id_regex": "x"}, "https://cardora.ca")
    _accepts({"list_url": "https://cardora.ca/cars", "card_anchor": "a",
              "id_regex": "x"}, "https://www.inventory.cardora.ca")
    print("  subdomains treated as the same site OK")

    # 4. Every URL-bearing key is checked, not just list_url.
    sneaky = dict(CLUTCH_CFG)
    sneaky["list_url"] = "https://cardora.ca/cars"      # looks right...
    _rejects(sneaky, "https://cardora.ca")               # ...but page_url still points at clutch
    print("  page_url / base_url also guarded OK")

    # 5. json_api's "endpoint" is deliberately NOT guarded: most real dealers
    # source inventory JSON from a third-party syndication platform (DealerOn,
    # HomeNet, ...) hosted on ITS OWN domain — that is the normal shape, not a
    # copy-paste mistake, so it must be accepted even though the host differs.
    _accepts({"endpoint": "https://inventory.dealeron-platform.com/v1/vehicles?page={page}",
              "list_path": "d", "field_map": {"external_id": "id"}},
             "https://cardora.ca", mode="json_api")
    print("  json_api endpoint on a third-party platform domain is allowed OK")

    # 6. ...but a copied provider template still carries the ORIGINAL dealer's
    # url_base (used to absolutise a relative VDP url) along with it —
    # that mismatch is still caught.
    _rejects({"endpoint": "https://inventory.dealeron-platform.com/v1/vehicles?page={page}",
              "list_path": "d", "field_map": {"external_id": "id"},
              "url_base": "https://www.clutch.ca"},
             "https://cardora.ca", mode="json_api")
    print("  json_api url_base still guarded OK")

    # 7. No dealer URL supplied -> legacy callers keep working (no host check).
    validate_scrape_config("browser", CLUTCH_CFG)
    print("  legacy call without dealer_base_url still allowed OK")

    # 8. A config with no URLs at all must not blow up.
    validate_scrape_config("browser", {"list_url": "https://cardora.ca/x",
                                       "card_anchor": "a", "id_regex": "y"},
                           "https://cardora.ca")
    print("  minimal config OK")

    print("CONFIG GUARD TESTS PASSED — cross-site scrape_config can no longer be saved")


if __name__ == "__main__":
    main()
