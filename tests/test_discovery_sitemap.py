"""Unit tests for strategies/sitemap.py: robots.txt parsing, sitemap index
walking (incl. gzip), and end-to-end URL-shape clustering over sitemap URLs.

Uses httpx.MockTransport so this suite makes NO real network calls — fully
offline and deterministic.

Run:  python -m tests.test_discovery_sitemap
"""
from __future__ import annotations

import gzip

import httpx

from dealer_scraper.discovery.strategies.sitemap import (
    discover_via_sitemap,
    fetch_robots_txt,
    fetch_sitemap_urls,
)

ROBOTS_TXT = """User-agent: *
Disallow: /admin
Disallow: /cart
Sitemap: https://dealer.example/sitemap-index.xml

User-agent: BadBot
Disallow: /
"""

SITEMAP_INDEX = """<?xml version="1.0"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://dealer.example/sitemap-pages.xml</loc></sitemap>
  <sitemap><loc>https://dealer.example/sitemap-inventory.xml.gz</loc></sitemap>
</sitemapindex>
"""

PAGES_SITEMAP = """<?xml version="1.0"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://dealer.example/about</loc></url>
  <url><loc>https://dealer.example/contact</loc></url>
</urlset>
"""

INVENTORY_SITEMAP = "<?xml version=\"1.0\"?>\n<urlset xmlns=\"http://www.sitemaps.org/schemas/sitemap/0.9\">\n" + "".join(
    f"<url><loc>https://dealer.example/stock/2022-honda-civic-{i}/{5000 + i}</loc></url>"
    for i in range(8)
) + "\n</urlset>\n"

# Namespace-less sitemap — some real-world sitemaps omit the xmlns declaration.
NAMESPACE_LESS_SITEMAP = "<?xml version=\"1.0\"?>\n<urlset>\n" + "".join(
    f"<url><loc>https://dealer.example/vdp/{6000 + i}</loc></url>" for i in range(7)
) + "\n</urlset>\n"


def _handler(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if url.endswith("/robots.txt"):
        return httpx.Response(200, text=ROBOTS_TXT)
    if url.endswith("sitemap-index.xml"):
        return httpx.Response(200, content=SITEMAP_INDEX.encode())
    if url.endswith("sitemap-pages.xml"):
        return httpx.Response(200, content=PAGES_SITEMAP.encode())
    if url.endswith("sitemap-inventory.xml.gz"):
        return httpx.Response(200, content=gzip.compress(INVENTORY_SITEMAP.encode()))
    if url.endswith("sitemap.xml"):
        return httpx.Response(200, content=NAMESPACE_LESS_SITEMAP.encode())
    return httpx.Response(404)


def _client() -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(_handler))


def test_fetch_robots_txt_extracts_sitemaps_and_wildcard_disallow():
    result = fetch_robots_txt("https://dealer.example", client=_client())
    assert result["fetched"] is True
    assert result["sitemaps"] == ["https://dealer.example/sitemap-index.xml"]
    assert "/admin" in result["disallow"]
    assert "/cart" in result["disallow"]
    # The BadBot-only disallow ("/") must NOT leak into the wildcard group.
    assert "/" not in result["disallow"]


def test_fetch_robots_txt_missing_returns_not_fetched():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    result = fetch_robots_txt("https://nowhere.example", client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert result == {"sitemaps": [], "disallow": [], "fetched": False}


def test_fetch_sitemap_urls_walks_index_and_decompresses_gzip():
    urls = fetch_sitemap_urls("https://dealer.example/sitemap-index.xml", client=_client())
    assert len(urls) == 10  # 2 static pages + 8 gzipped inventory entries
    assert "https://dealer.example/about" in urls
    assert any("/stock/" in u for u in urls)


def test_fetch_sitemap_urls_handles_namespace_less_sitemap():
    urls = fetch_sitemap_urls("https://dealer.example/sitemap.xml", client=_client())
    assert len(urls) == 7
    assert all("/vdp/" in u for u in urls)


def test_discover_via_sitemap_finds_open_ended_detail_shape():
    result = discover_via_sitemap("https://dealer.example", client=_client())
    assert result.sitemaps_found == ["https://dealer.example/sitemap-index.xml"]
    assert result.urls_seen == 10
    assert result.best_cluster is not None
    assert result.best_cluster.shape == "/stock/*/*"
    assert result.best_cluster.count == 8
    assert len(result.detail_sample_urls) > 0
    assert all("/stock/" in u for u in result.detail_sample_urls)


def test_discover_via_sitemap_falls_back_to_default_sitemap_xml_path():
    # No "Sitemap:" line in robots.txt for this host -> falls back to /sitemap.xml
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/robots.txt"):
            return httpx.Response(200, text="User-agent: *\nDisallow:\n")
        if url.endswith("/sitemap.xml"):
            return httpx.Response(200, content=NAMESPACE_LESS_SITEMAP.encode())
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = discover_via_sitemap("https://dealer.example", client=client)
    assert result.sitemaps_found == ["https://dealer.example/sitemap.xml"]
    assert result.best_cluster is not None
    assert result.best_cluster.count == 7


def main() -> None:
    test_fetch_robots_txt_extracts_sitemaps_and_wildcard_disallow()
    test_fetch_robots_txt_missing_returns_not_fetched()
    test_fetch_sitemap_urls_walks_index_and_decompresses_gzip()
    test_fetch_sitemap_urls_handles_namespace_less_sitemap()
    test_discover_via_sitemap_finds_open_ended_detail_shape()
    test_discover_via_sitemap_falls_back_to_default_sitemap_xml_path()
    print("DISCOVERY SITEMAP TEST PASSED")


if __name__ == "__main__":
    main()
