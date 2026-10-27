"""API pagination detection: diff two observed calls (GET query string or POST
body) to find the page/offset cursor, with an active httpx fallback probe when
only one call was ever observed. Ported from dealer_scraper/discovery.py
(pre-refactor).
"""
from __future__ import annotations

import json
from urllib.parse import parse_qsl, quote, urlparse, urlsplit, urlunsplit

import httpx

from ...adapters.json_api import _dig
from ..heuristics import DEFAULT_HEURISTICS, HeuristicsConfig
from .fieldmap import _flatten
from .safe_http import safe_client


def _endpoint_key(url: str) -> tuple[str, str, str]:
    p = urlparse(url)
    return (p.scheme, p.netloc, p.path)


def _numeric(v) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _detect_pagination(urls: list[str], heuristics: HeuristicsConfig = DEFAULT_HEURISTICS) -> dict | None:
    """Given several request URLs that all hit the same API path, work out which
    query parameter is the page/offset cursor by diffing two distinct calls.

    Returns {"template_url", "param", "start", "step", "size_param", "size_value"}
    or None if no confident single-parameter diff could be found.
    """
    entries: list[tuple[str, dict]] = []
    seen_q = set()
    for u in urls:
        q = tuple(sorted(parse_qsl(urlparse(u).query)))
        if q in seen_q:
            continue
        seen_q.add(q)
        entries.append((u, dict(q)))
        if len(entries) >= 2:
            break
    if len(entries) < 2:
        return None

    (u1, q1), (u2, q2) = entries[0], entries[1]
    diffs = [k for k in (set(q1) | set(q2)) if q1.get(k) != q2.get(k)]
    if len(diffs) != 1:
        return None
    key = diffs[0]
    v1, v2 = _numeric(q1.get(key)), _numeric(q2.get(key))
    if v1 is None and v2 is None:
        return None
    if v1 is None or v2 is None:
        # The param is implicit (absent — server defaults it) on one request and
        # explicit on the other. Common for a site's own first/default fetch:
        # infer the default from a well-known naming convention rather than
        # discarding evidence that a second, later page WAS observed.
        if heuristics.zero_based_param_re.match(key):
            implied = 0.0
        elif heuristics.one_based_param_re.match(key):
            implied = 1.0
        else:
            return None  # unrecognised name — do not guess its default
        if v1 is None:
            v1 = implied
        else:
            v2 = implied
    if v1 == v2:
        return None
    if max(v1, v2) >= 1_000_000:
        return None  # looks like a timestamp / cache-buster, not a page cursor

    # Template from whichever URL actually HAS the param explicitly — the other
    # side's value may be an inferred default with no real query key to replace.
    template_url, other_q = (u1, q1) if key in q1 else (u2, q2)
    size_param = next(
        (k for k in other_q if k != key and heuristics.size_param_re.match(k) and _numeric(other_q[k]) is not None),
        None,
    )
    return {
        "template_url": template_url,
        "param": key,
        "start": min(v1, v2),
        "step": abs(v2 - v1),
        "size_param": size_param,
        "size_value": _numeric(other_q.get(size_param)) if size_param else None,
    }


def _template_endpoint(url: str, replacements: dict[str, str]) -> str:
    """Rebuild `url`'s querystring, substituting `replacements[key]` (already a
    literal placeholder like "{page}") for the given query keys."""
    parts = urlsplit(url)
    pieces = []
    for k, v in parse_qsl(parts.query, keep_blank_values=True):
        if k in replacements:
            pieces.append(f"{quote(k, safe='')}={replacements[k]}")
        else:
            pieces.append(f"{quote(k, safe='')}={quote(v, safe='')}")
    new_query = "&".join(pieces)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, new_query, ""))


def _with_query_param(url: str, key: str, value: str) -> str:
    """Set `key=value` on `url`'s querystring, adding it if not already present."""
    parts = urlsplit(url)
    pairs = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != key]
    pairs.append((key, value))
    new_query = "&".join(f"{quote(k, safe='')}={quote(v, safe='')}" for k, v in pairs)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, new_query, ""))


def _probe_pagination_via_http(url: str, list_path: str, first_array: list,
                                extra_headers: dict | None = None,
                                heuristics: HeuristicsConfig = DEFAULT_HEURISTICS) -> dict | None:
    """Best-effort fallback: directly ask the API for 'page 2' under a few
    common param names and see if genuinely different items come back. Never
    raises — any failure just means pagination stays unconfirmed.

    extra_headers carries anything the real browser request needed (auth
    token, referer, ...) — without it this probe would 401/403 against any
    API that isn't fully anonymous, same as the adapter would at scrape time.
    """
    if not first_array:
        return None
    first_key = json.dumps(first_array[0], sort_keys=True, default=str)
    headers = {"Accept": "application/json",
               "User-Agent": "InventoryBot/1.0 (+contact@brainspack.com) discovery-probe"}
    headers.update(extra_headers or {})
    for param in heuristics.page_param_candidates:
        trial_url = _with_query_param(url, param, "2")
        try:
            # safe_client, not bare httpx: this probe otherwise leaves from the
            # droplet's own IP, skipping SCRAPER_PROXY and the SSRF guard. On a
            # WAF-protected host it then gets a 403 that is indistinguishable
            # here from "page 2 has no vehicles" — so discovery silently
            # concludes the site cannot paginate and falls back to scroll.
            with safe_client(timeout=10, headers=headers) as probe_client:
                resp = probe_client.get(trial_url)
            if resp.status_code != 200 or "json" not in resp.headers.get("content-type", "").lower():
                continue
            body = resp.json()
        except Exception:
            continue
        items = _dig(body, list_path)
        if not isinstance(items, list) or not items:
            continue
        if json.dumps(items[0], sort_keys=True, default=str) != first_key:
            return {
                "template_url": _template_endpoint(trial_url, {param: "{page}"}),
                "param": param, "start": 1.0, "step": 1.0,
                "size_param": None, "size_value": None,
            }
    return None


def _dedup_dicts(items: list) -> list[dict]:
    seen, out = set(), []
    for it in items:
        if not isinstance(it, dict):
            continue
        sig = json.dumps(it, sort_keys=True, default=str)
        if sig in seen:
            continue
        seen.add(sig)
        out.append(it)
        if len(out) >= 2:
            break
    return out


def _detect_body_pagination(bodies: list[dict]) -> dict | None:
    """Diff two POST JSON bodies sent to the same endpoint to find the
    pagination field(s): a single field that increments by 1 (page-style), or
    a start/end pair that both slide by the same step (a sliding window, e.g.
    {"startIndex": 1, "endIndex": 24} -> {"startIndex": 25, "endIndex": 48}).

    Returns {"kind": "single"|"pair", "key"/"start_key"+"end_key", "start", "step"}
    or None — deliberately conservative, same spirit as _detect_pagination.
    """
    uniq = _dedup_dicts(bodies)
    if len(uniq) < 2:
        return None
    flat1, flat2 = _flatten(uniq[0], depth=3), _flatten(uniq[1], depth=3)
    changed = []
    for k in sorted(set(flat1) & set(flat2)):
        v1, v2 = flat1[k], flat2[k]
        if (isinstance(v1, (int, float)) and isinstance(v2, (int, float))
                and not isinstance(v1, bool) and not isinstance(v2, bool) and v1 != v2):
            changed.append((k, v1, v2))
    if not changed or len(changed) > 2:
        return None  # 0 -> nothing to paginate on; >2 -> too ambiguous to trust

    if len(changed) == 1:
        key, v1, v2 = changed[0]
        step = v2 - v1
        if step == 0 or max(v1, v2) >= 1_000_000:
            return None
        return {"kind": "single", "key": key, "start": min(v1, v2), "step": abs(step)}

    (k1, a1, a2), (k2, b1, b2) = changed
    step1, step2 = a2 - a1, b2 - b1
    if step1 != step2 or step1 == 0 or max(a1, a2, b1, b2) >= 1_000_000:
        return None  # must move together by the same amount to be a start/end pair
    start_key, end_key = (k1, k2) if a1 <= b1 else (k2, k1)
    start_val = min(a1, a2) if start_key == k1 else min(b1, b2)
    return {"kind": "pair", "start_key": start_key, "end_key": end_key,
            "start": start_val, "step": abs(step1)}


def _template_body(body: dict, replacements: dict[str, str]) -> dict:
    """Rebuild `body`, substituting dotted-path keys in `replacements` with a
    literal placeholder token (e.g. "{page}")."""
    def walk(node, prefix):
        if isinstance(node, dict):
            out = {}
            for k, v in node.items():
                path = f"{prefix}.{k}" if prefix else k
                out[k] = replacements[path] if path in replacements else walk(v, path)
            return out
        return node
    return walk(body, "")


def _probe_body_pagination_via_http(url: str, list_path: str, first_array: list,
                                     body_template: dict, extra_headers: dict | None = None
                                     ) -> dict | None:
    """POST-body counterpart to _probe_pagination_via_http: try bumping the
    body's numeric field(s) by page_size-ish amounts and see if different
    items come back. Tries single fields first (page/pageNo-style), then
    field PAIRS moved together (start/end sliding-window-style) — bumping
    only "startIndex" while leaving "endIndex" behind produces an invalid
    (start > end) range that a single-field probe alone would never catch.
    Best-effort; never raises.
    """
    if not first_array or not isinstance(body_template, dict):
        return None
    first_key = json.dumps(first_array[0], sort_keys=True, default=str)
    headers = {"Accept": "application/json",
               "User-Agent": "InventoryBot/1.0 (+contact@brainspack.com) discovery-probe"}
    headers.update(extra_headers or {})
    page_size_guess = max(len(first_array), 1)
    numeric_keys = [k for k, v in body_template.items()
                    if isinstance(v, (int, float)) and not isinstance(v, bool)]

    def _try(trial_body: dict) -> bool:
        try:
            # Same reasoning as the GET probe above — proxied and SSRF-guarded.
            with safe_client(timeout=10, headers=headers) as probe_client:
                resp = probe_client.post(url, json=trial_body)
            if resp.status_code != 200 or "json" not in resp.headers.get("content-type", "").lower():
                return False
            body = resp.json()
        except Exception:
            return False
        items = _dig(body, list_path)
        if not isinstance(items, list) or not items:
            return False
        return json.dumps(items[0], sort_keys=True, default=str) != first_key

    for key in numeric_keys:
        trial_body = dict(body_template)
        trial_body[key] = body_template[key] + page_size_guess
        if _try(trial_body):
            return {"kind": "single", "key": key,
                    "start": body_template[key], "step": page_size_guess}

    for i, k1 in enumerate(numeric_keys):
        for k2 in numeric_keys[i + 1:]:
            trial_body = dict(body_template)
            trial_body[k1] = body_template[k1] + page_size_guess
            trial_body[k2] = body_template[k2] + page_size_guess
            if _try(trial_body):
                start_key, end_key = (k1, k2) if body_template[k1] <= body_template[k2] else (k2, k1)
                return {"kind": "pair", "start_key": start_key, "end_key": end_key,
                        "start": min(body_template[k1], body_template[k2]),
                        "step": page_size_guess}
    return None


def _detect_cursor_pagination(responses: list[dict], heuristics: HeuristicsConfig = DEFAULT_HEURISTICS) -> dict | None:
    """Detect cursor/token-based pagination from response structure.

    Looks for patterns like:
    - next_cursor / cursor / continuation_token fields
    - hasMore / has_next_page boolean fields
    - paginationToken / pageToken string fields

    Returns {"kind": "cursor", "token_key": "field.path", "has_more_key": "..."}
    or None if no cursor pattern detected.
    """
    if len(responses) < 1:
        return None

    first = responses[0]
    if not isinstance(first, dict):
        return None

    flattened = _flatten(first, depth=3)
    cursor_candidates = []
    has_more_candidates = []

    cursor_patterns = ("cursor", "token", "continuation", "nextpage", "pagination")

    for path, value in flattened.items():
        leaf = path.split(".")[-1].lower()

        # Look for cursor/token fields (string values)
        if any(x in leaf for x in cursor_patterns):
            if isinstance(value, str) and len(value) > 2:
                cursor_candidates.append(path)

        # Look for has_more fields (boolean values)
        if any(x in leaf for x in ("hasmore", "has_next", "nextpage", "isnext")):
            if isinstance(value, bool) or value in (0, 1):
                has_more_candidates.append(path)

    if not cursor_candidates:
        return None

    return {
        "kind": "cursor",
        "token_key": cursor_candidates[0],
        "has_more_key": has_more_candidates[0] if has_more_candidates else None,
    }
