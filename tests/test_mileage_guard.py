"""A garbage mileage must become None, not an INSERT that aborts the scrape."""
from dealer_scraper.normalize import normalize, sane_mileage


def test_garbage_mileage_dropped():
    assert sane_mileage(20134100000000000) is None
    assert sane_mileage("20134100000000000") is None


def test_normal_mileage_kept():
    assert sane_mileage(201341) == 201341
    assert sane_mileage("45,200 km") == 45200
    assert sane_mileage(0) == 0


def test_negative_and_empty():
    assert sane_mileage(-5) is None
    assert sane_mileage(None) is None
    assert sane_mileage("") is None
    assert sane_mileage("abc") is None


def test_normalize_uses_guard():
    v = normalize({"vin": "2HGFB2F72DH000024", "mileage": 20134100000000000})
    assert v.mileage is None


def main():
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("ok")


if __name__ == "__main__":
    main()
