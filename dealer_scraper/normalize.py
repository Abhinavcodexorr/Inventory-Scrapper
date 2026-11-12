"""Normalized vehicle model + content hashing.

RawVehicle is what an adapter yields (a plain dict already field-mapped).
NormalizedVehicle is the cleaned, typed record the engine persists.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation


@dataclass
class NormalizedVehicle:
    external_id: str | None      # dealer stock #; absent on factory-ordered units
    vin: str | None = None
    make: str | None = None
    model: str | None = None
    year: int | None = None
    trim: str | None = None
    mileage: int | None = None
    body_type: str | None = None
    condition: str | None = None
    exterior_color: str | None = None
    interior_color: str | None = None
    transmission: str | None = None
    fuel_type: str | None = None
    drivetrain: str | None = None
    price: Decimal | None = None
    # Pre-discount / struck-through figure when a listing advertises a sale.
    original_price: Decimal | None = None
    currency: str = "CAD"
    url: str | None = None
    #: Lifecycle state the FEED stated, already mapped to our vocabulary
    #: ("active" | "sold" | "hidden"), or None when the source doesn't say.
    #:
    #: Most dealer sites list only what is for sale, so `CLAUDE.md` records that
    #: sold can never be observed, only inferred from a vehicle disappearing for
    #: SOLD_AFTER_DAYS. That holds for Clutch and Convertus — but the Zop feed
    #: returns Instock/Sold/Unpublished outright, and 482 of Cardora's 645 rows
    #: are Sold. Ignoring that both understated inventory against the dealer's
    #: own page count and threw away a confirmed sale date.
    source_status: str | None = None
    #: The feed's own word for that state, untouched ("Archive", "Unpublished").
    #: `source_status` collapses Archive and Unpublished into "hidden"; the
    #: dashboard has to show them separately so a dealer's count can be matched
    #: against the feed by hand. Not part of the content hash.
    source_status_raw: str | None = None
    #: The date the dealer put this vehicle in stock, as their own site states it
    #: (Days in Stock = today - stock_date). Stored as a DATE rather than a day
    #: count so it never goes stale between syncs. None when the site does not say.
    #: Not part of the content hash: it must not register as a listing change.
    stock_date: date | None = None
    raw_payload: dict | None = None

    def content_hash(self) -> str:
        """Hash of the fields that, if changed, mean the listing meaningfully changed.

        Drives both change detection and `updated_at`, so anything a user would
        call "the listing changed" belongs here — including original_price, since
        a discount appearing or ending is a real change even when the asking
        price itself is unchanged.
        """
        basis = "|".join(str(x) for x in (
            self.price, self.original_price, self.mileage, self.make, self.model,
            self.year, self.trim, self.condition, self.exterior_color,
            self.interior_color, self.transmission, self.fuel_type, self.drivetrain,
        ))
        return hashlib.sha256(basis.encode("utf-8")).hexdigest()


def today_local() -> date:
    """Today's date where the dealers are (Canada), not on the server.

    Sites that report "days on lot" roll their counter over at local midnight;
    counting from the server's UTC date would shift a derived stock date by a day
    for several hours every night.
    """
    from .config import settings

    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo(settings.daily_sync_timezone or "UTC")).date()
    except Exception:
        return datetime.now(timezone.utc).date()


def local_tz():
    from .config import settings

    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(settings.daily_sync_timezone or "UTC")
    except Exception:
        return timezone.utc


def start_of_local_day() -> datetime:
    """Midnight at the start of today where the dealers are, as a UTC instant.

    "Today's sync" means since this moment. The daily sync is scheduled in this
    timezone, so a server-local or UTC midnight would split one night's run in two.
    """
    now = datetime.now(local_tz())
    return now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)


def days_in_stock(
    stock_date: date | None,
    status: str | None = "active",
    sold_at: datetime | None = None,
    removed_at: datetime | None = None,
    today: date | None = None,
) -> int | None:
    """Whole days the vehicle has been in the dealer's stock, or None if unknown.

    Counts to today while the car is for sale, but stops at the day it was sold or
    removed: a car sold after 30 days must keep saying 30, not grow forever.
    """
    if stock_date is None:
        return None
    end = today or today_local()
    stop = sold_at if status == "sold" else removed_at if status == "removed" else None
    if stop is not None:
        if stop.tzinfo is None:
            stop = stop.replace(tzinfo=timezone.utc)
        end = min(end, stop.astimezone(local_tz()).date())
    return max(0, (end - stock_date).days)


#: Feed keys that hold the stock DATE, most direct first. The first one a vehicle
#: has a parseable value for wins.
_STOCK_DATE_KEYS = ("stock_date", "instock_date", "date_in_stock", "in_stock_date")
#: Feed keys that hold a DAY COUNT (days in stock / on lot). Turned into a date.
_STOCK_DAYS_KEYS = ("days_in_stock", "days_on_lot", "days_in_inventory")
#: Provider signatures whose creation timestamp is the stock date. Each is
#: (keys that must all be present to recognise the provider, key holding the date).
#:   Hillz:      `instock_date` exists but is empty on every dealer checked; the row's
#:               `createdAt` is what its own `age` counts from.
#:   Car Pages:  `create_datetime` is when the listing was created, the closest
#:               thing the feed offers to a stock date.
_STOCK_DATE_SIGNATURES = (
    (("frk_dealership_id", "createdAt"), "createdAt"),
    (("organization", "create_datetime"), "create_datetime"),
)
_MIN_STOCK_YEAR = 2000


def parse_date(value) -> date | None:
    """A calendar date from ISO text ("2026-11-03", "2026-11-03 09:20:11",
    "2026-10-24T18:40:59.000Z"), a date or a datetime. None for anything else."""
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if len(text) < 8:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _sane_stock_date(d: date | None, today: date) -> date | None:
    """Reject a date no real listing has: before 2000, or later than tomorrow."""
    if d is None or d.year < _MIN_STOCK_YEAR or d > today + timedelta(days=1):
        return None
    return d


def stock_date_from(raw: dict, today: date | None = None) -> date | None:
    """The vehicle's stock date from whatever the feed offers, or None.

    Order: a field the dealer's config mapped explicitly (`stock_date` or
    `days_in_stock`), then well-known keys in the raw item, then a provider's
    creation timestamp. Sites that only give a day count are converted with the
    dealer-local date, so the result is stable across the day.
    """
    today = today or today_local()
    item = raw.get("_raw") if isinstance(raw.get("_raw"), dict) else {}

    def from_date(value) -> date | None:
        return _sane_stock_date(parse_date(value), today)

    def from_days(value) -> date | None:
        n = to_int(value)
        if n is None or n < 0 or n > 365 * 25:
            return None
        return _sane_stock_date(today - timedelta(days=n), today)

    for source in (raw, item):
        for key in _STOCK_DATE_KEYS:
            found = from_date(source.get(key))
            if found:
                return found
        for key in _STOCK_DAYS_KEYS:
            found = from_days(source.get(key))
            if found:
                return found
    for needed, key in _STOCK_DATE_SIGNATURES:
        if all(k in item for k in needed):
            found = from_date(item.get(key))
            if found:
                return found
    return None


def _original_price(raw: dict) -> Decimal | None:
    """The pre-discount figure, kept only when it is genuinely a higher price.

    Sites label this inconsistently (msrp / retail_price / was_price / list_price),
    and some emit it equal to — or below — the asking price when nothing is
    discounted. Storing those would invent discounts of zero or, worse, negative
    ones, so anything not strictly above `price` is discarded.
    """
    for key in ("original_price", "msrp", "retail_price", "was_price", "list_price"):
        candidate = to_decimal(raw.get(key))
        if candidate is None:
            continue
        price = to_decimal(raw.get("price"))
        if price is not None and candidate <= price:
            continue
        return candidate
    return None


_num_re = re.compile(r"[^\d.]")


def to_int(value) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return int(value)
    cleaned = _num_re.sub("", str(value))
    return int(float(cleaned)) if cleaned else None


# mileage is a 32-bit INTEGER column. A feed that sends garbage here (Rj1Motors
# sent 20134100000000000 for a 2013 Civic) makes the INSERT fail, and one bad
# row then aborts the whole scrape. No road vehicle has anywhere near a million
# km, so anything outside this range is treated as "not reported".
MAX_MILEAGE = 2_000_000


def sane_mileage(value) -> int | None:
    try:
        n = to_int(value)
    except (ValueError, OverflowError):
        return None
    if n is None or n < 0 or n > MAX_MILEAGE:
        return None
    return n


def to_decimal(value) -> Decimal | None:
    if value is None or value == "":
        return None
    if isinstance(value, Decimal):
        return value
    cleaned = _num_re.sub("", str(value))
    try:
        return Decimal(cleaned) if cleaned else None
    except InvalidOperation:
        return None


def _s(raw: dict, key: str, maxlen: int | None = None) -> str | None:
    """Trimmed string or None, capped to maxlen so it can never overflow its column."""
    v = raw.get(key)
    if v in (None, ""):
        return None
    s = str(v).strip()
    if not s:
        return None
    return s[:maxlen] if maxlen else s


#: Feed status wording -> our lifecycle vocabulary. Lower-cased, punctuation
#: and spacing stripped, so "In Stock", "in-stock" and "Instock" all land.
#: Anything unrecognised maps to None, which means "the feed did not say" and
#: leaves the existing inference untouched — safer than guessing a state.
_SOURCE_STATUS = {
    "instock": "active", "in stock": "active", "available": "active",
    "forsale": "active", "for sale": "active", "active": "active",
    "live": "active", "published": "active", "intransit": "active",
    "in transit": "active", "onorder": "active", "on order": "active",
    "coming soon": "active",
    "sold": "sold", "delivered": "sold",
    "unpublished": "hidden", "hidden": "hidden", "draft": "hidden",
    "deleted": "hidden", "archived": "hidden", "inactive": "hidden",
    # Zop's wording is "Archive", not "Archived". Unrecognised means "the feed
    # did not say", which stores the row as active — GR Cars kept 5 archived
    # vehicles in live inventory (site 126, tool 131).
    "archive": "hidden", "removed": "hidden", "expired": "hidden",
    "withdrawn": "hidden", "cancelled": "hidden", "canceled": "hidden",
}


def map_source_status(value) -> str | None:
    """Map a feed's own status wording onto "active" / "sold" / "hidden"."""
    if value is None:
        return None
    key = " ".join(str(value).strip().lower().replace("_", " ").replace("-", " ").split())
    return _SOURCE_STATUS.get(key) or _SOURCE_STATUS.get(key.replace(" ", ""))


def normalize(raw: dict) -> NormalizedVehicle:
    """Clean a field-mapped dict into a typed NormalizedVehicle."""
    vin = (raw.get("vin") or None)
    if vin:
        vin = str(vin).strip().upper()[:20] or None
    return NormalizedVehicle(
        # A blank/absent stock number is NOT an identity: str() would turn it into
        # "" or the literal "None", and every such listing would then collide on
        # uq_car_dealer_external. Real feeds do this — DealerOn omits the stock
        # number for factory-ordered cars. Identity falls back to VIN/URL.
        external_id=_s(raw, "external_id", 200),
        vin=vin,
        make=_s(raw, "make", 80),
        model=_s(raw, "model", 120),
        year=to_int(raw.get("year")),
        trim=_s(raw, "trim", 120),
        mileage=sane_mileage(raw.get("mileage")),
        body_type=_s(raw, "body_type", 80),
        condition=_s(raw, "condition", 20),
        exterior_color=_s(raw, "exterior_color", 60),
        interior_color=_s(raw, "interior_color", 60),
        transmission=_s(raw, "transmission", 40),
        fuel_type=_s(raw, "fuel_type", 40),
        drivetrain=_s(raw, "drivetrain", 40),
        price=to_decimal(raw.get("price")),
        original_price=_original_price(raw),
        currency=(str(raw.get("currency") or "CAD").strip().upper()[:3]),
        url=_s(raw, "url", 700),
        source_status=map_source_status(raw.get("source_status")),
        source_status_raw=_s(raw, "source_status", 40),
        stock_date=stock_date_from(raw),
        raw_payload=raw.get("_raw"),
    )
