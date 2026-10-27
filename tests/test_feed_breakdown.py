"""The dashboard can show a dealer's feed the way the client counts it.

Eco Cars' feed: 1,090 rows — 952 Archive, 59 Instock, 45 Sold, 34 Unpublished.
Its website lists 104 (Instock + Sold). The client wants InStock / Sold / Archive
/ Unpublished / Total side by side so every dealer's count can be matched by hand,
which means Archive and Unpublished must stay distinguishable — both map to the
engine's single "hidden" state, so the feed's own word is counted per run.

Run:  DATABASE_URL="sqlite:///./sync_test.db" python -m tests.test_feed_breakdown
"""
from __future__ import annotations

from api.routers.dashboard import per_dealer
from dealer_scraper.adapters.stub import StubAdapter
from dealer_scraper.db import SessionLocal, engine
from dealer_scraper.engine import _feed_counter, run_dealer
from dealer_scraper.models import Base, Dealer
from dealer_scraper.normalize import normalize
from . import require_disposable_db


def _rows(**by_status: int) -> list[dict]:
    items: list[dict] = []
    for status, n in by_status.items():
        for i in range(n):
            items.append({"external_id": f"{status}-{i}", "vin": f"1HGCM82633A{len(items):06d}",
                          "make": "Honda", "model": "Civic", "year": 2020, "price": "20000",
                          "source_status": status})
    return items


def _dealer(name: str) -> int:
    with SessionLocal() as s:
        d = Dealer(name=name, base_url=f"https://{name}.example", adapter_mode="stub", scrape_config={})
        s.add(d)
        s.commit()
        return d.id


def _run(dealer_id: int, items: list[dict]):
    with SessionLocal() as s:
        return run_dealer(s, s.get(Dealer, dealer_id), adapter=StubAdapter(items))


def _stats_for(dealer_id: int):
    with SessionLocal() as s:
        return next(x for x in per_dealer(db=s) if x.dealer_id == dealer_id)


def test_the_feeds_own_words_are_kept_apart() -> None:
    """Archive and Unpublished both map to "hidden"; the count must not merge them."""
    assert normalize({"external_id": "A", "source_status": "Archive"}).source_status_raw == "Archive"
    assert normalize({"external_id": "A", "source_status": "Archive"}).source_status == "hidden"
    assert normalize({"external_id": "A", "source_status": "Unpublished"}).source_status == "hidden"


def test_a_run_records_every_status_the_feed_used() -> None:
    did = _dealer("eco")
    run = _run(did, _rows(Archive=95, Instock=6, Sold=4, Unpublished=3))
    counters = run.stats["counters"]
    assert (counters["feed_archive"], counters["feed_instock"],
            counters["feed_sold"], counters["feed_unpublished"]) == (95, 6, 4, 3), counters
    assert run.scraped_count == 108


def test_the_dashboard_exposes_the_breakdown_that_reconciles_with_the_site() -> None:
    did = _dealer("eco2")
    _run(did, _rows(Archive=95, Instock=6, Sold=4, Unpublished=3))
    stats = _stats_for(did)
    assert stats.feed_statuses == {"archive": 95, "instock": 6, "sold": 4, "unpublished": 3}
    assert stats.feed_total == 108
    listed_on_site = stats.feed_statuses["instock"] + stats.feed_statuses["sold"]
    assert listed_on_site == 10 and sum(stats.feed_statuses.values()) == stats.feed_total


def test_a_dealer_whose_site_states_no_status_has_no_breakdown() -> None:
    did = _dealer("plain")
    plain = [{"external_id": f"P{i}", "make": "Honda", "model": "Civic", "year": 2020} for i in range(5)]
    _run(did, plain)
    stats = _stats_for(did)
    assert stats.feed_statuses is None and stats.feed_total is None


def test_a_feed_with_free_text_statuses_cannot_create_unbounded_counters() -> None:
    seen: set[str] = set()
    names = {_feed_counter(f"status number {i}", seen) for i in range(200)}
    assert len(names) <= 13, len(names)
    assert "feed_other" in names


def main() -> None:
    require_disposable_db()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    test_the_feeds_own_words_are_kept_apart()
    test_a_run_records_every_status_the_feed_used()
    test_the_dashboard_exposes_the_breakdown_that_reconciles_with_the_site()
    test_a_dealer_whose_site_states_no_status_has_no_breakdown()
    test_a_feed_with_free_text_statuses_cannot_create_unbounded_counters()
    print("FEED BREAKDOWN TESTS PASSED — Archive and Unpublished are counted apart")


if __name__ == "__main__":
    main()
