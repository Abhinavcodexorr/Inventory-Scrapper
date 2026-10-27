"""Live-browser tests for the Phase 2 browser/ modules: intelligent waits,
progressive scroll, anti-bot classification, and session/snapshot capture —
run against a REAL headless Chromium (not a mock), using synthetic HTML via
page.set_content() so results are deterministic and no third-party site is
touched.

Requires Playwright + a downloaded Chromium build:
    pip install playwright && python -m playwright install chromium

Run:  python -m tests.test_discovery_browser
"""
from __future__ import annotations

from dealer_scraper.discovery.analyzers.dom_repetition import LinkRecord, find_best_listing_cluster
from dealer_scraper.discovery.analyzers.visual_structure import BoundingBox, analyze_grid
from dealer_scraper.discovery.browser.antibot import classify
from dealer_scraper.discovery.browser.scroll import progressive_scroll
from dealer_scraper.discovery.browser.session import SessionManager, capture_card_boxes, capture_links
from dealer_scraper.discovery.browser.waits import wait_for_stable
from dealer_scraper.discovery.heuristics import HeuristicsConfig

_FAST_HEURISTICS = HeuristicsConfig(
    dom_stability_poll_ms=80, dom_stability_quiet_rounds=2,
    dom_stability_max_ms=1500, networkidle_timeout_ms=500,
)


def _goto_blank_origin(page) -> None:
    """Navigate to a real origin before injecting synthetic HTML — the
    capture JS does `new URL(href, location.origin)`, which throws on
    about:blank's 'null' origin. Matches what coordinator.py always does in
    practice (it only ever evaluates these snippets after a real goto())."""
    page.goto("https://example.com", timeout=15000)


def test_wait_for_stable_settles_fast_on_static_page(playwright, browser):
    page = browser.new_page()
    _goto_blank_origin(page)
    page.set_content("<html><body><h1>static</h1></body></html>")
    result = wait_for_stable(page, _FAST_HEURISTICS)
    assert result["elapsed_ms"] < _FAST_HEURISTICS.dom_stability_max_ms
    page.close()


def test_wait_for_stable_waits_until_mutations_actually_stop(playwright, browser):
    page = browser.new_page()
    _goto_blank_origin(page)
    page.set_content("""
      <html><body><div id="x"></div>
      <script>
        let n = 0;
        const iv = setInterval(() => {
          document.getElementById("x").innerHTML += "<p>item " + (n++) + "</p>";
          if (n > 8) clearInterval(iv);
        }, 100);
      </script>
      </body></html>
    """)
    result = wait_for_stable(page, _FAST_HEURISTICS)
    added = page.eval_on_selector("#x", "el => el.children.length")
    assert added == 9, "must wait for the mutation timer to actually finish, not bail early"
    assert result["elapsed_ms"] < _FAST_HEURISTICS.dom_stability_max_ms
    page.close()


def test_progressive_scroll_collects_growing_content_then_stops(playwright, browser):
    page = browser.new_page(viewport={"width": 1200, "height": 800})
    _goto_blank_origin(page)
    page.set_content("""
      <html><body>
      <div id="list"></div>
      <script>
        let batch = 0;
        function addBatch() {
          if (batch >= 5) return;
          const el = document.getElementById("list");
          for (let i = 0; i < 6; i++) {
            const a = document.createElement("a");
            a.href = "/inventory/car-" + (batch * 6 + i) + "/" + (1000 + batch * 6 + i);
            a.textContent = "car";
            a.style.display = "block";
            a.style.height = "200px";
            el.appendChild(a);
          }
          batch++;
        }
        addBatch();
        window.addEventListener("scroll", () => {
          if ((window.scrollY + window.innerHeight) >= (document.body.scrollHeight - 300)) addBatch();
        });
      </script>
      </body></html>
    """)

    def collect():
        return set(page.eval_on_selector_all(
            "a[href^='/inventory/']", "els => els.map(e => e.getAttribute('href'))"))

    result = progressive_scroll(page, collect, max_rounds=25, stagnant_rounds_to_stop=3,
                                 scroll_amount=600, heuristics=_FAST_HEURISTICS)
    assert len(result["final_set"]) == 30
    assert result["rounds"] < 25, "must stop once growth stalls, not exhaust max_rounds"
    page.close()


def test_progressive_scroll_stops_almost_immediately_on_static_page(playwright, browser):
    page = browser.new_page()
    _goto_blank_origin(page)
    page.set_content('<html><body><a href="/inventory/only-one/1">x</a></body></html>')

    def collect():
        return set(page.eval_on_selector_all(
            "a[href^='/inventory/']", "els => els.map(e => e.getAttribute('href'))"))

    result = progressive_scroll(page, collect, max_rounds=25, stagnant_rounds_to_stop=2,
                                 heuristics=_FAST_HEURISTICS)
    assert result["rounds"] <= 3
    assert len(result["final_set"]) == 1
    page.close()


def test_antibot_classify_does_not_false_positive_on_a_real_normal_page(playwright, browser):
    page = browser.new_page()
    response = page.goto("https://example.com", timeout=15000)
    body = page.text_content("body") or ""
    verdict = classify(status_code=response.status, headers=response.headers, body_text=body)
    assert verdict.blocked is False
    page.close()


def test_session_manager_capture_pipeline_end_to_end(playwright):
    dealer_html = (
        "<html><body>"
        '<nav><a href="/vehicules-occasion">Vehicules d occasion</a> <a href="/about">About</a></nav>'
        '<div style="display:grid;grid-template-columns:repeat(3,300px);gap:10px">'
        + "".join(
            f'<a href="/stock/2022-honda-civic-{i}/{5000 + i}" '
            f'style="display:block;width:280px;height:380px">'
            f'<img src="/img/{i}.jpg">'
            f"<div>2022 Honda Civic Touring — Stock #A{i:04d} — 12,{i:03d} km — $24,{i:03d}</div></a>"
            for i in range(9)
        )
        + "</div></body></html>"
    )

    sm = SessionManager(playwright, headless=True)
    try:
        page = sm.new_page()
        page.goto("https://example.com", timeout=15000)
        page.set_content(dealer_html)
        wait_for_stable(page, _FAST_HEURISTICS)

        raw_links = capture_links(page)
        records = [LinkRecord(path=link["path"], text=link["text"]) for link in raw_links]
        best = find_best_listing_cluster(records)
        assert best is not None
        assert best.shape == "/stock/*/*"
        assert best.count == 9

        raw_boxes = capture_card_boxes(page, best.paths)
        boxes = [BoundingBox(**b) for b in raw_boxes]
        grid = analyze_grid(boxes)
        assert grid.is_grid is True
        assert grid.row_count == 3 and grid.col_count == 3

        # Retry escalation: a fresh context under a different profile, same
        # underlying browser process (no relaunch).
        first_context_id = id(page.context)
        page2 = sm.new_context(attempt=1)
        new_page = sm.new_page()
        assert id(new_page.context) != first_context_id
        ua = new_page.evaluate("() => navigator.userAgent")
        assert "Windows" in ua  # profile_for_attempt(1) == chrome-windows
    finally:
        sm.close()


def main() -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            test_wait_for_stable_settles_fast_on_static_page(p, browser)
            test_wait_for_stable_waits_until_mutations_actually_stop(p, browser)
            test_progressive_scroll_collects_growing_content_then_stops(p, browser)
            test_progressive_scroll_stops_almost_immediately_on_static_page(p, browser)
            test_antibot_classify_does_not_false_positive_on_a_real_normal_page(p, browser)
        finally:
            browser.close()
        test_session_manager_capture_pipeline_end_to_end(p)
    print("DISCOVERY BROWSER (LIVE) TEST PASSED")


if __name__ == "__main__":
    main()
