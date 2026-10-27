"""HillzDealer sites: read the inventory from the site's own Next.js page data.

Every HillzDealer site is a Next.js app whose /cars page data is served at
/_next/data/<buildId>/cars.json. Two templates exist:

* older (cccmotors.ca): `pageProps.preFetchedData.vehiclesData` carries the
  whole inventory in one response;
* newer (formula7motors.ca, motorpalacecanada.ca): `pageProps.vehiclesData`
  carries 10 full vehicles, and its first entry's `fullIds` lists {id, slug}
  for EVERY vehicle. The rest are read one by one from each slug's own page
  data, which is what the json_api `detail` block does.

The newer page is server-rendered, so it never fetches that data in the
browser: traffic sniffing saw nothing, discovery fell back to reading the
grid, and the sync stopped at the 10 cards on screen (10 of 541).
"""
from __future__ import annotations

import json
import logging
import re
from urllib.parse import urlsplit

import httpx

from ..net.safe_http import UnsafeUrlError, safe_client, validate_public_url

log = logging.getLogger("scraper.discovery.hillz")

MARKER_RE = re.compile(r"hillzdealer\.com|hillzcdn\.ca", re.I)
_NEXT_DATA_RE = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)

#: Where the newer template keeps its 10 listed vehicles and its full index.
LIST_PATH = "pageProps.vehiclesData"
INDEX_PATH = "pageProps.vehiclesData.0.fullIds"
TOTAL_PATH = "pageProps.vehiclesData.0.fullSearchCount"


def is_hillz_site(html: str) -> bool:
    return bool(html and MARKER_RE.search(html))


def build_id(html: str) -> str | None:
    m = _NEXT_DATA_RE.search(html or "")
    if not m:
        return None
    try:
        value = json.loads(m.group(1)).get("buildId")
    except ValueError:
        return None
    return value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]+", value) else None


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def probe_feed(base_url: str, html: str, client: httpx.Client | None = None) -> list[dict]:
    """Fetch this dealer's /cars page data, shaped like discovery's captured
    JSON hits so the normal json_api path scores it and maps its fields.
    Returns [] when the build id is missing or the response lists no vehicles."""
    bid = build_id(html)
    if not bid:
        return []
    url = f"{_origin(base_url)}/_next/data/{bid}/cars.json"
    own_client = client is None
    client = client or safe_client(timeout=20.0, headers={"Accept": "application/json"})
    try:
        validate_public_url(url)
        resp = client.get(url)
        data = resp.json() if resp.status_code == 200 else None
    except (httpx.HTTPError, UnsafeUrlError, ValueError) as exc:
        log.debug("HillzDealer page data %s unusable: %s", url, exc)
        return []
    finally:
        if own_client:
            client.close()
    props = data.get("pageProps") if isinstance(data, dict) else None
    if not isinstance(props, dict):
        return []
    listed = props.get("vehiclesData") or (props.get("preFetchedData") or {}).get("vehiclesData")
    if not isinstance(listed, list) or not listed:
        return []
    return [{"url": url, "json": data, "headers": {}, "method": "GET", "post_body": None}]


def prefer_listing(json_best: dict | None) -> dict | None:
    """Point a picked response at the newer template's main list. The same page
    data also carries a featured-vehicles widget (`vehiclesData2.data`, 37 slim
    records on motorpalacecanada.ca) that outscores the 10-vehicle main list on
    size alone, and has no index to reach the other 504 vehicles."""
    if not json_best or json_best.get("list_path") == LIST_PATH:
        return json_best
    root = json_best.get("root")
    listed = (root.get("pageProps") or {}).get("vehiclesData") if isinstance(root, dict) else None
    if detail_block(root, LIST_PATH, json_best.get("url", "")) is None:
        return json_best
    return {**json_best, "list_path": LIST_PATH, "array": listed}


def detail_block(root: object, list_path: str, endpoint: str) -> dict | None:
    """The `detail` config for the newer template, or None when the response
    already holds every vehicle. Only used when the index lists MORE vehicles
    than the response carries in full, and every index entry has the id and
    slug the detail URL is built from."""
    if list_path != LIST_PATH or "/_next/data/" not in endpoint:
        return None
    listed = (root or {}).get("pageProps", {}).get("vehiclesData") if isinstance(root, dict) else None
    if not isinstance(listed, list) or not listed or not isinstance(listed[0], dict):
        return None
    index = listed[0].get("fullIds")
    if not isinstance(index, list) or len(index) <= len(listed):
        return None
    if not all(isinstance(e, dict) and e.get("id") and str(e.get("slug", "")).startswith("/")
               for e in index):
        return None
    return {
        "index_path": INDEX_PATH,
        "key": "id",
        "url": f"{_origin(endpoint)}/_next/data/{{build_id}}{{slug}}.json",
        "item_path": "pageProps.data",
    }
