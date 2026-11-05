"""A hidden nav link matching wait_selector must not fail the scrape.

grandeleasing.com's closed "Shop Vehicles" dropdown link is the first match for
a[href*='/vehicles/']. Waiting for it to be visible failed every attempt, with
36 vehicle cards on screen. Runs a real Chromium, because the bug is in how
Playwright treats the first match; a fake page would only repeat the assumption.

Run:  python -m tests.test_browser_hidden_nav_link
"""
from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from dealer_scraper.adapters.browser import BrowserAdapter

PAGE = b"""<!doctype html><title>Vehicles Archive</title><body>
<nav><ul style="display:none"><li><a href="/vehicles/">Shop Vehicles</a></li></ul></nav>
<div class="card"><a href="/vehicles/2022-ford-f-150/">2022 Ford F-150 $40,000 10,000 km</a></div>
<div class="card"><a href="/vehicles/2021-bmw-x3/">2021 BMW X3 $35,000 20,000 km</a></div>
</body>"""


class _Site(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(PAGE)

    def log_message(self, *args) -> None:
        pass


def test_hidden_first_match_does_not_fail_the_wait() -> None:
    pytest.importorskip("playwright")
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Site)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    selector = "a[href*='/vehicles/']"
    adapter = BrowserAdapter({
        "list_url": f"{base}/vehicles/", "base_url": base, "card_anchor": selector,
        "wait_selector": selector, "id_regex": r"/vehicles/([^/]+)/", "headless": True,
        "use_proxy": False, "settle_ms": 500, "nav_timeout": 9000, "warmup_ms": 200,
        "paginate": "none",
    })
    try:
        cars = sorted((v.url, v.make) for v in adapter.iter_vehicles())
    finally:
        server.shutdown()
    # Each link wraps its whole card, so each vehicle keeps its own details.
    assert cars == [(f"{base}/vehicles/2021-bmw-x3/", "BMW"),
                    (f"{base}/vehicles/2022-ford-f-150/", "Ford")]


def main() -> None:
    test_hidden_first_match_does_not_fail_the_wait()
    print("OK")


if __name__ == "__main__":
    main()
