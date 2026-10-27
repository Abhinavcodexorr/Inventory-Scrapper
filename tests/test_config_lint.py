"""Configs that can only produce wrong or truncated data are refused up front.

Every case here is a config that was live and reported success:

* B Town / Good Fellows / AA Canada declare a `total_path` and `page_size` but
  their endpoint has no `{page}`. Every request returns page 1.
* Car-On was cloned from Tabangi and kept Tabangi's Convertus company id
  (cp=3337) inside a URL-encoded query parameter. The outer host really was
  caronauto.ca, so the hostname guard passed it; 542 vehicles were published
  under the wrong dealer.

Run:  python -m tests.test_config_lint
"""
from __future__ import annotations

from dealer_scraper.adapters.registry import validate_scrape_config
from dealer_scraper.config_lint import find_identity_conflict, identity_keys, structural_problems

_CONVERTUS = (
    "https://vms.prod.convertus.rocks/api/filtering/?cp={cp}&ln=en&pg={{page}}&pc={{page_size}}"
    "&dc=true&sc=used&st=year%252Cdesc&dp={cp}&view=grid"
)


def _convertus_cfg(host: str, cp: int) -> dict:
    """A Convertus config as WordPress proxy plugins express it: the real API
    address is URL-encoded inside an `endpoint=` query parameter."""
    from urllib.parse import quote
    inner = _CONVERTUS.format(cp=cp).replace("{{", "{").replace("}}", "}")
    return {
        "api_url": (f"https://{host}/wp-content/plugins/convertus-vms/include/php/"
                    f"ajax-vehicles.php?endpoint={quote(inner, safe='{}')}&action=vms_data"),
        "base_url": f"https://{host}/",
    }


TABANGI = _convertus_cfg("www.tabangimotors.com", 3337)
CAR_ON_CLONED = _convertus_cfg("www.caronauto.ca", 3337)
CAR_ON_CORRECT = _convertus_cfg("www.caronauto.ca", 4421)


def test_a_feed_with_a_total_but_no_page_placeholder_is_rejected() -> None:
    cfg = {"endpoint": "https://x.test/inventory/search?dsp_limit=12", "list_path": "items",
           "field_map": {"external_id": "id"}, "page_size": 12, "total_path": "pagination.total"}
    problems = structural_problems("json_api", cfg)
    assert problems and "{page}" in problems[0], problems
    try:
        validate_scrape_config("json_api", cfg, "https://x.test")
    except ValueError as exc:
        assert "page 1" in str(exc)
    else:
        raise AssertionError("validate_scrape_config accepted an unpaginatable feed")


def test_a_paginated_feed_and_a_genuine_single_page_feed_are_accepted() -> None:
    paged = {"endpoint": "https://x.test/api?page={page}&limit={page_size}", "list_path": "items",
             "field_map": {"external_id": "id"}, "total_path": "total"}
    single = {"endpoint": "https://x.test/api/inventory?paginate=false", "list_path": "items",
              "field_map": {"external_id": "id"}}
    assert structural_problems("json_api", paged) == []
    assert structural_problems("json_api", single) == []


def test_a_post_feed_pages_through_its_body_not_its_url() -> None:
    cfg = {"endpoint": "https://x.test/api", "method": "POST", "total_path": "total",
           "body_template": {"page": "{page}"}, "list_path": "items",
           "field_map": {"external_id": "id"}}
    assert structural_problems("json_api", cfg) == []


def test_the_encoded_company_id_is_visible_to_the_identity_check() -> None:
    keys = identity_keys(CAR_ON_CLONED)
    assert ("id", "vms.prod.convertus.rocks", "/api/filtering", "cp", "3337") in keys, keys


def test_a_cloned_template_is_caught_even_though_its_outer_host_is_correct() -> None:
    """The Car-On case. The hostname guard compares only caronauto.ca."""
    others = [(2, "Tabangi Motors", TABANGI)]
    message = find_identity_conflict(CAR_ON_CLONED, others)
    assert message is not None
    assert "cp=3337" in message and "Tabangi Motors" in message, message


def test_the_same_template_with_its_own_company_id_is_accepted() -> None:
    assert find_identity_conflict(CAR_ON_CORRECT, [(2, "Tabangi Motors", TABANGI)]) is None


def test_paging_parameters_do_not_make_two_dealers_look_different() -> None:
    """Only differing pg/pc values must not hide a shared identity."""
    other = _convertus_cfg("www.caronauto.ca", 3337)
    other["api_url"] = other["api_url"].replace("pc%3D", "pc%3D50")
    assert find_identity_conflict(other, [(2, "Tabangi Motors", TABANGI)]) is not None


def test_a_dealer_is_not_in_conflict_with_itself() -> None:
    """Callers exclude the dealer being edited; an empty list must be clean."""
    assert find_identity_conflict(TABANGI, []) is None


def main() -> None:
    test_a_feed_with_a_total_but_no_page_placeholder_is_rejected()
    test_a_paginated_feed_and_a_genuine_single_page_feed_are_accepted()
    test_a_post_feed_pages_through_its_body_not_its_url()
    test_the_encoded_company_id_is_visible_to_the_identity_check()
    test_a_cloned_template_is_caught_even_though_its_outer_host_is_correct()
    test_the_same_template_with_its_own_company_id_is_accepted()
    test_paging_parameters_do_not_make_two_dealers_look_different()
    test_a_dealer_is_not_in_conflict_with_itself()
    print("CONFIG LINT TESTS PASSED — a cloned or unpaginatable config is refused at the door")


if __name__ == "__main__":
    main()
