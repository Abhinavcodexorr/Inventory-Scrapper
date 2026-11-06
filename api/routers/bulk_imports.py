"""Add many dealers from one list of websites. Accepted here, then worked through
in the background by api/bulk_runner.py."""
from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from dealer_scraper.models import BulkImport, BulkImportRow, Dealer

from ..deps import get_db
from ..schemas import (
    BulkImportIn, BulkImportOut, BulkImportRowOut, BulkPreviewOut, BulkPreviewRowOut, BulkRowIn,
)
from .dealers import _site_key, _site_label

router = APIRouter(prefix="/api/bulk-imports", tags=["bulk-imports"])

_FIELD_LIMITS = {"address": 300, "city": 120, "province": 80, "postal_code": 20, "phone": 40}
_WEBSITE_LIMIT = 500
# What the report calls "failed" covers addresses that were never usable.
_STATUS_GROUPS = {"failed": ("failed", "invalid")}


def normalize_website(raw: str) -> str:
    """The form a dealer's base_url is stored in. Raises ValueError for anything
    that cannot be a public website, with a message fit for the report.

    Offline on purpose: the DNS-based safety check runs just before each site is
    visited, since resolving thousands of hosts here would stall the upload.
    """
    value = (raw or "").strip()
    if not value:
        raise ValueError("No website address on this row.")
    if any(c.isspace() for c in value):
        raise ValueError("A website address cannot contain spaces.")
    if len(value) > _WEBSITE_LIMIT:
        raise ValueError(f"The website address is longer than {_WEBSITE_LIMIT} characters.")
    try:
        parts = urlsplit(value if "://" in value else f"https://{value}")
        host, port = parts.hostname, parts.port
    except ValueError:
        raise ValueError("This is not a valid website address.") from None
    if parts.scheme.lower() not in ("http", "https"):
        raise ValueError("A website address must start with http:// or https://.")
    if parts.username or parts.password:
        raise ValueError("A website address cannot contain a username or password.")
    if port not in (None, 80, 443):
        raise ValueError(f"Port {port} is not allowed.")
    try:
        ipaddress.ip_address(host or "")
        is_ip = True
    except ValueError:
        is_ip = False
    if not host or "." not in host.strip(".") or is_ip:
        raise ValueError("This is not a valid website address.")
    netloc = host.lower() if port is None else f"{host.lower()}:{port}"
    return f"{parts.scheme.lower()}://{netloc}{parts.path.rstrip('/')}"


def _default_name(website: str) -> str:
    """cardora.ca -> Cardora, blue-star.ca -> Blue Star. Editable afterwards."""
    label = _site_label(urlsplit(website).netloc)
    return label.replace("-", " ").replace("_", " ").title()[:200]


def _clean(value: str | None, limit: int) -> str | None:
    return (value or "").strip()[:limit] or None


def _classify(db: Session, rows: list[BulkRowIn], provider: str | None) -> list[BulkImportRow]:
    """Turn a list into unsaved rows, each queued, invalid or duplicate.

    Every site key is loaded once, rather than querying per row: this runs on
    the request path, against up to 5,000 rows and a fleet of 20,000 dealers.
    """
    existing: dict[str, str] = {}
    for dealer_name, base_url in db.execute(select(Dealer.name, Dealer.base_url)):
        if key := _site_key(base_url):
            existing.setdefault(key, dealer_name)

    first_seen: dict[str, int] = {}
    out: list[BulkImportRow] = []
    for position, raw in enumerate(rows, start=1):
        row = BulkImportRow(
            position=position, status="queued",
            provider=_clean(raw.provider, 120) or provider,
            name=_clean(raw.name, 200),
            **{field: _clean(getattr(raw, field), limit) for field, limit in _FIELD_LIMITS.items()},
        )
        out.append(row)
        try:
            row.website = normalize_website(raw.website)
        except ValueError as exc:
            row.website = (raw.website or "").strip()[:_WEBSITE_LIMIT]
            row.status, row.reason = "invalid", str(exc)
            continue
        row.name = row.name or _default_name(row.website)
        key = _site_key(row.website)
        if key in existing:
            row.status, row.reason = "duplicate", f"Already in the tool as {existing[key]}."
        elif key in first_seen:
            row.status, row.reason = "duplicate", f"Same website as row {first_seen[key]} of this list."
        else:
            first_seen[key] = position
    return out


def _summaries(db: Session, jobs: list[BulkImport]) -> list[BulkImportOut]:
    counts: dict[int, dict[str, int]] = {}
    if jobs:
        rows = db.execute(
            select(BulkImportRow.import_id, BulkImportRow.status, func.count())
            .where(BulkImportRow.import_id.in_([j.id for j in jobs]))
            .group_by(BulkImportRow.import_id, BulkImportRow.status)
        )
        for import_id, row_status, n in rows:
            counts.setdefault(import_id, {})[row_status] = n
    out = []
    for job in jobs:
        c = counts.get(job.id, {})
        out.append(BulkImportOut(
            id=job.id, filename=job.filename, provider=job.provider, status=job.status,
            notice=job.notice, created_at=job.created_at, finished_at=job.finished_at,
            total=sum(c.values()), added=c.get("added", 0),
            failed=c.get("failed", 0) + c.get("invalid", 0),
            duplicate=c.get("duplicate", 0), skipped=c.get("skipped", 0),
            waiting=c.get("queued", 0), checking=c.get("checking", 0),
        ))
    return out


def _get_or_404(db: Session, import_id: int) -> BulkImport:
    job = db.get(BulkImport, import_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Import not found")
    return job


@router.post("/preview", response_model=BulkPreviewOut)
def preview_import(payload: BulkImportIn, db: Session = Depends(get_db)):
    """What would happen to each row. Saves nothing."""
    rows = _classify(db, payload.rows, _clean(payload.provider, 120))
    count = lambda wanted: sum(r.status == wanted for r in rows)  # noqa: E731
    return BulkPreviewOut(
        ready=count("queued"), invalid=count("invalid"), duplicate=count("duplicate"),
        rows=[BulkPreviewRowOut(position=r.position, website=r.website, name=r.name, provider=r.provider,
                                status=r.status, reason=r.reason) for r in rows],
    )


@router.post("", response_model=BulkImportOut, status_code=status.HTTP_202_ACCEPTED)
def create_import(payload: BulkImportIn, db: Session = Depends(get_db)):
    """Accept a list and answer right away. Rows with nothing to check are recorded
    straight away too, so the report accounts for every line that was uploaded."""
    provider = _clean(payload.provider, 120)
    job = BulkImport(filename=_clean(payload.filename, 200), provider=provider, status="running")
    db.add(job)
    db.flush()
    for row in _classify(db, payload.rows, provider):
        row.import_id = job.id
        if row.status != "queued":
            row.finished_at = func.now()
        db.add(row)
    db.commit()
    db.refresh(job)
    return _summaries(db, [job])[0]


@router.get("", response_model=list[BulkImportOut])
def list_imports(
    response: Response,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    response.headers["X-Total-Count"] = str(db.scalar(select(func.count()).select_from(BulkImport)) or 0)
    jobs = db.execute(
        select(BulkImport).order_by(BulkImport.id.desc()).limit(limit).offset(offset)
    ).scalars().all()
    return _summaries(db, list(jobs))


@router.get("/{import_id}", response_model=BulkImportOut)
def get_import(import_id: int, db: Session = Depends(get_db)):
    return _summaries(db, [_get_or_404(db, import_id)])[0]


@router.get("/{import_id}/rows", response_model=list[BulkImportRowOut])
def list_rows(
    import_id: int,
    response: Response,
    row_status: str | None = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    _get_or_404(db, import_id)
    where = [BulkImportRow.import_id == import_id]
    if row_status:
        where.append(BulkImportRow.status.in_(_STATUS_GROUPS.get(row_status, (row_status,))))
    response.headers["X-Total-Count"] = str(
        db.scalar(select(func.count()).select_from(BulkImportRow).where(*where)) or 0)
    rows = db.execute(
        select(BulkImportRow).where(*where).order_by(BulkImportRow.position).limit(limit).offset(offset)
    ).scalars().all()
    return rows


@router.post("/{import_id}/stop", response_model=BulkImportOut)
def stop_import(import_id: int, db: Session = Depends(get_db)):
    """Skip every website still waiting. Checks already running finish and count."""
    job = _get_or_404(db, import_id)
    if job.status == "running":
        job.status, job.notice = "stopping", None
        db.execute(
            update(BulkImportRow)
            .where(BulkImportRow.import_id == import_id, BulkImportRow.status == "queued")
            .values(status="skipped", finished_at=func.now(),
                    reason="The import was stopped before this website was checked.")
        )
        db.commit()
    return _summaries(db, [job])[0]


@router.post("/{import_id}/retry", response_model=BulkImportOut)
def retry_failed(import_id: int, db: Session = Depends(get_db)):
    """Check failed and skipped websites again. An invalid address would only fail
    the same way, so those stay as they are."""
    job = _get_or_404(db, import_id)
    if job.status in ("running", "stopping"):
        raise HTTPException(status_code=409, detail="This import is still running.")
    result = db.execute(
        update(BulkImportRow)
        .where(BulkImportRow.import_id == import_id, BulkImportRow.status.in_(("failed", "skipped")))
        .values(status="queued", reason=None, discovery_run_id=None, dispatch_mode=None,
                vehicles_found=None, started_at=None, finished_at=None)
    )
    if result.rowcount:
        job.status, job.finished_at = "running", None
    db.commit()
    db.refresh(job)
    return _summaries(db, [job])[0]
