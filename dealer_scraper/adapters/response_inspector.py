"""What did the dealer's endpoint actually send back?

json_api used to treat "content-type is not JSON" as the only failure worth
naming, and "the list_path resolved to nothing" as an empty inventory. The
second is how a config copied from one Hillz dealer (22G: the list lives at
`pageProps.preFetchedData.vehiclesData`) silently scraped 0 vehicles from
another (Formula 7: `pageProps.vehiclesData`) — no error, just nothing.

This module classifies a response (JSON / HTML challenge / login page / other
HTML / error object) and, when a configured path misses, SUGGESTS where the
vehicles actually are. It never picks one for the caller: the largest array in
a payload is as often a colour list as an inventory.

Also runnable against a saved response, to help write a field_map:

    python -m dealer_scraper.adapters.response_inspector response.json [list_path]
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

#: Keys whose presence marks an object as a vehicle listing. Deliberately
#: loose — this ranks candidates for a human, it does not decide.
_VEHICLE_KEYS = (
    "vin", "vinnumber", "make", "model", "year", "modelyear", "trim", "price",
    "sellprice", "saleprice", "mileage", "odometer", "km", "stock", "stockno",
    "stocknumber", "bodystyle", "bodytype", "transmission", "drivetrain",
    "drivetype", "exteriorcolor", "fueltype", "vehicle",
)
_CHALLENGE_MARKERS = (
    "attention required! | cloudflare", "just a moment...", "cf-chl-", "cf_chl_",
    "verify you are human", "are you a human", "captcha", "access denied",
    "request unsuccessful. incapsula", "_incapsula_resource", "px-captcha",
    "datadome", "awswaf", "unusual traffic",
)
_LOGIN_MARKERS = ('type="password"', "type='password'", "<title>log in", "<title>sign in",
                  "<title>login")


class ResponseFormatError(RuntimeError):
    """The endpoint answered, but not with the data the config expects."""


class ListPathError(ResponseFormatError):
    """The configured list_path does not lead to an array of vehicles."""


@dataclass
class ArrayCandidate:
    path: str
    length: int
    vehicle_score: float           # fraction of _VEHICLE_KEYS-like keys in a sample item
    sample_keys: list[str] = field(default_factory=list)

    def describe(self) -> str:
        keys = ", ".join(self.sample_keys[:12])
        return f"'{self.path or '<root>'}' ({self.length} items; keys: {keys})"


def _norm(key: str) -> str:
    return re.sub(r"[^a-z0-9]", "", key.lower())


def dig(obj: Any, path: str) -> Any:
    """Follow a dotted path; supports list indices. None if any hop is missing."""
    if not path:
        return obj
    cur = obj
    for key in path.split("."):
        try:
            cur = cur[int(key)] if isinstance(cur, list) else cur[key]
        except (KeyError, IndexError, TypeError, ValueError):
            return None
    return cur


def path_exists(obj: Any, path: str) -> bool:
    """True if every hop of `path` exists, even when the final value is null."""
    if not path:
        return True
    cur = obj
    for key in path.split("."):
        if isinstance(cur, list):
            try:
                cur = cur[int(key)]
            except (IndexError, ValueError):
                return False
        elif isinstance(cur, dict) and key in cur:
            cur = cur[key]
        else:
            return False
    return True


def _flat_keys(item: dict, depth: int = 2, prefix: str = "") -> list[str]:
    out: list[str] = []
    for k, v in item.items():
        p = f"{prefix}.{k}" if prefix else str(k)
        out.append(p)
        if isinstance(v, dict) and depth > 0:
            out.extend(_flat_keys(v, depth - 1, p))
    return out


def vehicle_score(item: Any) -> float:
    """0..1: how much one object looks like a vehicle listing."""
    if not isinstance(item, dict) or not item:
        return 0.0
    leaves = {_norm(k.rsplit(".", 1)[-1]) for k in _flat_keys(item)}
    hits = sum(1 for k in _VEHICLE_KEYS if k in leaves)
    return min(1.0, hits / 6)


def find_vehicle_arrays(data: Any, *, max_depth: int = 6, limit: int = 5) -> list[ArrayCandidate]:
    """Arrays of objects anywhere in `data`, best vehicle-looking first."""
    found: list[ArrayCandidate] = []

    def walk(node: Any, path: str, depth: int) -> None:
        if depth > max_depth:
            return
        if isinstance(node, list):
            dicts = [x for x in node[:5] if isinstance(x, dict)]
            if dicts:
                score = sum(vehicle_score(x) for x in dicts) / len(dicts)
                found.append(ArrayCandidate(path, len(node), score, sorted(dicts[0].keys())))
                # Look inside the first element too (arrays of wrappers).
                walk(dicts[0], f"{path}.0" if path else "0", depth + 1)
            return
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, f"{path}.{k}" if path else str(k), depth + 1)

    walk(data, "", 0)
    found = [c for c in found if c.vehicle_score > 0]
    found.sort(key=lambda c: (c.vehicle_score, c.length), reverse=True)
    return found[:limit]


def error_object(data: Any) -> str | None:
    """A JSON body that is an API error rather than data, summarised."""
    if not isinstance(data, dict):
        return None
    keys = {k.lower() for k in data}
    if not keys & {"error", "errors", "message", "detail", "fault"}:
        return None
    if any(isinstance(v, list) and v and isinstance(v[0], dict) and vehicle_score(v[0]) > 0
           for v in data.values()):
        return None  # a data payload that merely carries a message field
    for k in ("error", "errors", "message", "detail", "fault"):
        for real in data:
            if real.lower() == k:
                return f"{real}: {json.dumps(data[real])[:200]}"
    return None


def html_title(text: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S)
    return " ".join(m.group(1).split())[:120] if m else ""


def classify_html(text: str) -> str:
    """'challenge' | 'login' | 'html'."""
    low = text[:20000].lower()
    if any(m in low for m in _CHALLENGE_MARKERS):
        return "challenge"
    if any(m in low for m in _LOGIN_MARKERS):
        return "login"
    return "html"


def looks_like_json(body: bytes | str) -> bool:
    head = (body[:64].decode("utf-8", "replace") if isinstance(body, bytes) else body[:64]).lstrip()
    return head.startswith(("{", "["))


def resolve_list(data: Any, list_path: str, *, where: str = "") -> list:
    """The configured array, or ListPathError explaining what IS there.

    A path that exists and holds [] is a legitimately empty inventory and is
    returned as such. A path that does not exist is a config error.
    """
    value = dig(data, list_path)
    if isinstance(value, list):
        return value
    err = error_object(data)
    where = f" {where}" if where else ""
    if err:
        raise ListPathError(f"The endpoint{where} returned an error object instead of data — {err}")
    if path_exists(data, list_path) and value is None:
        return []
    state = ("missing" if not path_exists(data, list_path)
             else f"a {type(value).__name__}, not a list")
    candidates = find_vehicle_arrays(data)
    hint = ("; candidate vehicle arrays: " + "; ".join(c.describe() for c in candidates)
            if candidates else "; no array of vehicle-like objects was found in the response")
    top = ", ".join(list(data.keys())[:15]) if isinstance(data, dict) else type(data).__name__
    raise ListPathError(
        f"list_path '{list_path}' is {state} in the response{where} "
        f"(top-level keys: {top}){hint}. Verify the path against a captured response "
        f"before changing the config."
    )


def main(argv: list[str] | None = None) -> int:
    import sys

    args = argv if argv is not None else sys.argv[1:]
    if not args:
        print(__doc__)
        return 2
    raw = open(args[0], "rb").read()
    if not looks_like_json(raw):
        kind = classify_html(raw.decode("utf-8", "replace"))
        print(f"Not JSON — looks like {kind} (title: {html_title(raw.decode('utf-8', 'replace'))!r})")
        return 1
    data = json.loads(raw)
    if len(args) > 1:
        try:
            items = resolve_list(data, args[1])
            print(f"'{args[1]}' -> {len(items)} items")
            if items and isinstance(items[0], dict):
                print("sample keys:", ", ".join(_flat_keys(items[0])))
            return 0
        except ListPathError as exc:
            print(exc)
            return 1
    for c in find_vehicle_arrays(data):
        print(f"{c.vehicle_score:.2f}  {c.describe()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
