"""SOW §1 — Dealership Onboarding module (CRUD + activate/deactivate + manual sync)."""
from __future__ import annotations

import logging
from copy import deepcopy
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select, func, delete, update
from sqlalchemy.orm import Session

from dealer_scraper.adapters.registry import _host_of as _registry_host
from dealer_scraper.adapters.registry import validate_scrape_config
from dealer_scraper.config_lint import find_identity_conflict
from dealer_scraper.config import settings
from dealer_scraper.discovery.persistence import _finalize
from dealer_scraper.models import (
    Car,
    CarPriceHistory,
    DailyInventorySnapshot,
    Dealer,
    DiscoveryRun,
    ScrapeRun,
    SitePattern,
    VehicleAuditLog,
)
from worker.dispatch import (
    cancel_discovery,
    cancel_sync,
    dispatch_discovery,
    dispatch_sync,
    discovery_local_job_for_run,
    is_local_job,
    local_job,
    local_job_for_dealer,
)
from ..deps import get_db
from ..schemas import (
    DealerCreate,
    DealerOut,
    DealerUpdate,
    DiscoverIn,
    DiscoveryCancelOut,
    DiscoveryJobOut,
    DiscoveryOut,
    DiscoveryRunStatusOut,
    DiscoveryRunSummaryOut,
    SiteCompatibilityOut,
    SiteCompatibilityReportOut,
    SiteCompatibilitySummaryOut,
    SyncCancelOut,
    SyncJobOut,
    SyncJobStatusOut,
    SyncProgressOut,
    SyncRunOut,
)

log = logging.getLogger("api.dealers")

router = APIRouter(prefix="/api/dealers", tags=["dealers"])


def _require_proven_site_or_409(db: Session, base_url: str) -> None:
    """Refuse to onboard a dealership whose website the scraper cannot read.

    Discovery already proves a config by running the adapter and extracting real
    vehicles (discovery/decision.py verify()), recording the count on the
    DiscoveryRun. Nothing consulted that result, so a site discovery had judged
    unsupported could still be saved — and then sat at zero inventory forever,
    looking like a healthy dealer with no cars. Observed live with grcars.ca.

    Rules, deliberately conservative:
      * no completed discovery for this host -> refuse (run Detect settings first)
      * discovery completed but verified 0 vehicles -> refuse
      * verified >= 1 vehicle -> allow
    `force=true` bypasses this for a deliberate manual override.
    """
    host = _host_of_url(base_url)
    if not host:
        return
    runs = db.execute(
        select(DiscoveryRun)
        .where(DiscoveryRun.status == "completed")
        .order_by(DiscoveryRun.id.desc())
        .limit(50)
    ).scalars().all()
    latest = next((r for r in runs if _host_of_url(r.base_url) == host), None)

    if latest is None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"No successful site check exists for {host}. Run 'Detect settings' "
                f"first so the scraper can confirm it can read this website. "
                f"Use force=true to add it anyway."
            ),
        )
    if (latest.vehicles_verified or 0) < 1:
        raise HTTPException(
            status_code=409,
            detail=(
                f"The scraper could not extract any vehicles from {host}, so adding it "
                f"would create a dealership permanently stuck at zero inventory. "
                f"This website structure needs custom scraper development before it can "
                f"be onboarded. Use force=true to add it anyway."
            ),
        )


def _detected_platform_for(db: Session, base_url: str) -> str | None:
    """Platform the most recent completed discovery run fingerprinted for this host.

    Read off the persisted DiscoveryReport rather than a dedicated column so this
    needs no migration — `report.detected_cms` is written by the discovery
    coordinator's fingerprinting step.
    """
    host = _host_of_url(base_url)
    if not host:
        return None
    runs = db.execute(
        select(DiscoveryRun)
        .where(DiscoveryRun.status == "completed")
        .order_by(DiscoveryRun.id.desc())
        .limit(50)
    ).scalars().all()
    for run in runs:
        if _host_of_url(run.base_url) != host:
            continue
        detected = (run.report or {}).get("detected_cms") or []
        return detected[0] if detected else None
    return None


def _reusable_config_for(
    db: Session, provider: str | None, detected_platform: str | None,
) -> Dealer | None:
    """An existing dealer whose scrape_config can seed a new one on the same platform.

    This is the mechanism the per-website-structure pricing model assumes: dealer
    sites cluster by vendor, so the second dealership on a platform we already
    support should cost a row, not a fresh discovery run. Without it every dealer
    is a bespoke investigation, which is exactly what does not scale to a hundred.

    Prefers a same-provider dealer, then a same-detected-platform one. Only ever
    returns a dealer that has a non-empty config and has actually scraped
    something — copying a config that has never produced a vehicle would spread a
    broken template across the fleet.
    """
    for column, value in ((Dealer.website_provider, provider),
                          (Dealer.detected_platform, detected_platform)):
        if not value:
            continue
        candidate = db.execute(
            select(Dealer)
            .where(column == value, Dealer.inventory_count > 0)
            .order_by(Dealer.inventory_count.desc())
            .limit(1)
        ).scalar_one_or_none()
        if candidate is not None and candidate.scrape_config:
            return candidate
    return None


def _site_label(netloc: str) -> str:
    """First label of a host without www.: www.cardora.ca -> cardora."""
    host = netloc.lower().rsplit("@", 1)[-1].split(":")[0]
    host = host[4:] if host.startswith("www.") else host
    return host.split(".")[0]


def _retarget_config(config: dict, from_url: str, to_url: str) -> dict:
    """Copy `config`, repointing every URL from the template's site to the new one.

    Reuse is only safe if this happens. A verbatim copy keeps the template
    dealer's list_url, so the new dealership would scrape the *other* dealer's
    inventory and store it under its own name — silent, plausible-looking, and
    very hard to spot once two dealers share a platform. Paths and query strings
    are preserved, since those are the platform-shaped part worth reusing; only
    scheme and host move.

    Every string in the config is considered, nested ones included: a fixed key
    list missed next_build_id_url and detail.url (HillzDealer), which kept
    reading the template dealer's site. A vendor-hosted feed named after the
    dealer (cardora.zopsoftware.com) moves with it (newcastleauto.zopsoftware.com).
    """
    src, dst = urlsplit(from_url), urlsplit(to_url)
    if not src.netloc or not dst.netloc:
        return deepcopy(config)
    src_label, dst_label = _site_label(src.netloc), _site_label(dst.netloc)

    def retarget(value):
        if isinstance(value, dict):
            return {k: retarget(v) for k, v in value.items()}
        if isinstance(value, list):
            return [retarget(v) for v in value]
        if not isinstance(value, str) or "://" not in value:
            return value
        parts = urlsplit(value)
        netloc = parts.netloc.lower()
        if not netloc:
            return value
        # The template's own site, with or without www.
        if _registry_host(value) == _registry_host(from_url):
            return urlunsplit((dst.scheme or parts.scheme, dst.netloc,
                               parts.path, parts.query, parts.fragment))
        if (src_label and dst_label and src_label != dst_label
                and netloc.startswith(src_label + ".")):
            return urlunsplit((parts.scheme, dst_label + netloc[len(src_label):],
                               parts.path, parts.query, parts.fragment))
        return value

    return retarget(deepcopy(config))


def _host_of_url(url: str | None) -> str:
    """Normalised hostname (lowercase, no www./port), shared with the config
    guard so the compatibility report groups hosts exactly the way validation
    and site_patterns do."""
    return _registry_host(url) or ""


def _get_or_404(db: Session, dealer_id: int) -> Dealer:
    dealer = db.get(Dealer, dealer_id)
    if dealer is None:
        raise HTTPException(status_code=404, detail="Dealer not found")
    return dealer


def _validate_config_or_422(
    adapter_mode: str, scrape_config: dict | None, dealer_base_url: str | None = None
) -> None:
    """Reject an unscrapeable dealer up front — a browser dealer with no list_url
    used to save fine and only fail minutes later inside the background scrape.

    Also rejects a config whose URLs point at a different website than this
    dealer's own: copying a provider template keeps the original dealer's URLs,
    and scraping it would file that site's vehicles under this dealer."""
    try:
        validate_scrape_config(adapter_mode, scrape_config, dealer_base_url)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc


def _reject_identity_conflict_or_422(
    db: Session, scrape_config: dict | None, exclude_dealer_id: int | None = None
) -> None:
    """Refuse a config that addresses inventory another ACTIVE dealer already owns.

    The hostname guard cannot see this: a cloned Convertus template keeps the
    original dealer's company id inside a URL-encoded query parameter while its
    outer host is the new dealer's own. Inactive dealers are skipped so retiring
    the wrongly-configured one is how the conflict is cleared."""
    if not scrape_config:
        return
    stmt = select(Dealer.id, Dealer.name, Dealer.scrape_config).where(Dealer.active.is_(True))
    if exclude_dealer_id is not None:
        stmt = stmt.where(Dealer.id != exclude_dealer_id)
    conflict = find_identity_conflict(
        scrape_config, ((i, n, c or {}) for i, n, c in db.execute(stmt))
    )
    if conflict:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=conflict)


@router.get("", response_model=list[DealerOut])
def list_dealers(
    response: Response,
    db: Session = Depends(get_db),
    active: bool | None = Query(None, description="Filter by active flag"),
    provider: str | None = Query(None, description="Filter by website_provider"),
    q: str | None = Query(None, description="Search by name (case-insensitive)"),
    limit: int | None = Query(None, ge=1, le=500, description="Page size. Omit for all."),
    offset: int = Query(0, ge=0, description="Rows to skip. Use with limit."),
):
    """List dealerships, optionally filtered and paged.

    The matching total is always returned in the `X-Total-Count` header, so a
    caller that pages can render a pager without a second request. The body
    stays a plain array whether or not paging is used — several callers consume
    it directly, and wrapping it in an envelope would break them for no gain at
    the scale this runs at.
    """
    filters = []
    if active is not None:
        filters.append(Dealer.active.is_(active))
    if provider:
        filters.append(Dealer.website_provider == provider)
    if q:
        filters.append(Dealer.name.ilike(f"%{q}%"))

    total = db.execute(
        select(func.count()).select_from(Dealer).where(*filters)
    ).scalar_one()
    response.headers["X-Total-Count"] = str(total)

    stmt = select(Dealer).where(*filters).order_by(Dealer.name)
    if limit is not None:
        stmt = stmt.limit(limit).offset(offset)
    elif offset:
        stmt = stmt.offset(offset)
    return list(db.execute(stmt).scalars())


@router.post("", response_model=DealerOut, status_code=status.HTTP_201_CREATED)
def create_dealer(payload: DealerCreate, db: Session = Depends(get_db)):
    existing = db.execute(
        select(Dealer).where(Dealer.base_url == payload.base_url)
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail="A dealer with this base_url already exists")

    fields = payload.model_dump()
    fields.pop("force", None)          # request-only flag, not a column

    detected = _detected_platform_for(db, payload.base_url)
    if detected:
        fields["detected_platform"] = detected
    # An admin who typed nothing gets the detected platform as their provider
    # label, which is what makes reuse work without anyone having to know the
    # vendor's name. A value they DID type always wins.
    if not fields.get("website_provider") and detected:
        fields["website_provider"] = detected

    reused_from = None
    if not payload.scrape_config:
        template = _reusable_config_for(db, fields.get("website_provider"), detected)
        if template is not None:
            # The template's adapter_mode comes with it: it is the mode that was
            # proven against this platform, and DealerCreate.adapter_mode carries
            # a default the admin may never have chosen deliberately.
            fields["scrape_config"] = _retarget_config(template.scrape_config,
                                                        template.base_url, payload.base_url)
            fields["adapter_mode"] = template.adapter_mode
            reused_from = template.name

    _validate_config_or_422(fields["adapter_mode"], fields["scrape_config"], payload.base_url)
    _reject_identity_conflict_or_422(db, fields["scrape_config"])
    # 'stub' is the in-memory test adapter, not a real website — there is nothing
    # to have discovered, so the proven-site gate does not apply to it.
    # A config proven on another dealer of the same platform is its own evidence
    # the site is readable, so reuse satisfies the gate the way discovery does.
    if not payload.force and fields["adapter_mode"] != "stub" and reused_from is None:
        _require_proven_site_or_409(db, payload.base_url)

    dealer = Dealer(**fields)
    db.add(dealer)
    db.commit()
    db.refresh(dealer)

    out = DealerOut.model_validate(dealer)
    out.scraper_reused = reused_from is not None
    out.scraper_reused_from = reused_from
    return out


@router.post("/discover")
def discover_config(
    payload: DiscoverIn,
    response: Response,
    sync: bool = Query(
        False,
        description="Run discovery inline and return the result directly (legacy "
                    "behavior — blocks the request for up to a few minutes). "
                    "Default is to dispatch a background run and return 202.",
    ),
    db: Session = Depends(get_db),
) -> DiscoveryJobOut | DiscoveryOut:
    """Auto-discover a scrape_config from just the dealer's website URL.

    Onboarding flow: admin types the website URL -> this opens the site, finds
    the inventory listing, works out pagination and where the vehicle data
    lives, and returns a ready-to-save config. No hand-written JSON, no copied
    template.

    Default: dispatched through the same worker/dispatch machinery as a sync
    job (Celery when available, else the local thread pool) and returns 202
    immediately; poll GET .../discover/runs/{run_id} for progress and result.
    An in-flight run for the same base_url is returned instead of starting a
    duplicate browser session.

    ?sync=true: blocks and returns the result directly (200), matching the
    original synchronous contract. A failed discovery reports
    confidence='failed' with notes explaining what to do, rather than an error
    the caller has to special-case.
    """
    base_url = payload.base_url.strip()

    if sync:
        # Deprecated: ?sync=true blocks the request for minutes, has no cancellation,
        # no dedupe, no host lock, and spawns unbounded concurrent Chromiums. Only for
        # single-site debugging. Production deployments must use async (202 + polling).
        log.warning("Synchronous discovery requested (deprecated path)", extra={
            "base_url": base_url[:100], "caller": "api/discover?sync=true"
        })
        from dealer_scraper.discovery import discover  # lazy: pulls in Playwright

        result = discover(base_url)
        return DiscoveryOut(**result.as_dict())

    from dealer_scraper.discovery.persistence import create_run, find_in_flight

    existing = find_in_flight(db, base_url)
    if existing is not None:
        job = discovery_local_job_for_run(existing.id)
        return DiscoveryJobOut(
            status="running", run_id=existing.id,
            job_id=job.job_id if job else f"run-{existing.id}",
            mode="local" if job else "celery",
        )

    run = create_run(db, base_url, dealer_id=payload.dealer_id)
    outcome = dispatch_discovery(run.id, base_url)
    if outcome["status"] == "no_worker":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=("SYNC_BACKEND=celery but no Celery worker is consuming the queue. "
                    "Start one with 'npm run worker', or set SYNC_BACKEND=local in .env "
                    "to run discovery in-process."),
        )
    log.info("Discovery dispatched run=%s base_url=%s mode=%s status=%s job=%s",
             run.id, base_url, outcome["mode"], outcome["status"], outcome["job_id"])
    response.status_code = status.HTTP_202_ACCEPTED
    return DiscoveryJobOut(status=outcome["status"], job_id=outcome["job_id"],
                            run_id=run.id, mode=outcome["mode"])


@router.get("/compatibility", response_model=SiteCompatibilityReportOut)
def site_compatibility_report(
    billable_only: bool = Query(False, description="Only hosts needing custom work"),
    db: Session = Depends(get_db),
):
    """Fleet-wide view of which dealer websites our scraper can handle.

    Every discovery attempt is already recorded per host; this rolls them up to
    one row per host (latest completed attempt wins) and joins on the dealers
    table so you can see evaluated-but-not-onboarded sites too.

    Two things fall out of it commercially:
      * `requires_custom_work` is the list to quote new-website-structure work for
      * `hosts_sharing_cms` shows where one scraper already covers a platform, so
        further dealers on it are reuse rather than new development
    """
    runs = db.execute(
        select(DiscoveryRun)
        .where(DiscoveryRun.status == "completed")
        .order_by(DiscoveryRun.id.desc())
    ).scalars().all()

    # Latest completed run wins per host; count earlier ones as attempts.
    latest: dict[str, DiscoveryRun] = {}
    attempts: dict[str, int] = {}
    for run in runs:
        host = _host_of_url(run.base_url)
        if not host:
            continue
        attempts[host] = attempts.get(host, 0) + 1
        latest.setdefault(host, run)

    # CMS per host, from the learned patterns.
    cms_by_host: dict[str, str] = {}
    for host, platform in db.execute(
        select(SitePattern.host, SitePattern.cms_platform)
        .where(SitePattern.cms_platform.is_not(None))
    ).all():
        cms_by_host.setdefault(host, platform)

    cms_counts: dict[str, int] = {}
    for platform in cms_by_host.values():
        cms_counts[platform] = cms_counts.get(platform, 0) + 1

    dealers_by_host = {
        _host_of_url(d.base_url): d
        for d in db.execute(select(Dealer)).scalars()
    }

    items: list[SiteCompatibilityOut] = []
    for host, run in latest.items():
        if billable_only and not run.requires_custom_work:
            continue
        dealer = dealers_by_host.get(host)
        cms = cms_by_host.get(host)
        items.append(SiteCompatibilityOut(
            host=host,
            base_url=run.base_url,
            cms_platform=cms,
            verdict=run.verdict,
            confidence=run.confidence,
            requires_custom_work=bool(run.requires_custom_work),
            adapter_mode=run.adapter_mode,
            vehicles_verified=run.vehicles_verified or 0,
            last_checked_at=run.finished_at or run.created_at,
            dealer_id=dealer.id if dealer else None,
            dealer_name=dealer.name if dealer else None,
            onboarded=dealer is not None,
            hosts_sharing_cms=cms_counts.get(cms, 0) if cms else 0,
            attempts=attempts.get(host, 0),
        ))

    items.sort(key=lambda i: (not i.requires_custom_work, i.host))

    return SiteCompatibilityReportOut(
        summary=SiteCompatibilitySummaryOut(
            hosts_total=len(items),
            ready=sum(1 for i in items if i.verdict == "ready"),
            needs_review=sum(1 for i in items if i.verdict == "needs_review"),
            unsupported=sum(1 for i in items if i.verdict == "unsupported"),
            billable=sum(1 for i in items if i.requires_custom_work),
            onboarded=sum(1 for i in items if i.onboarded),
            distinct_cms_platforms=len({i.cms_platform for i in items if i.cms_platform}),
        ),
        items=items,
    )


@router.get("/discover/runs", response_model=list[DiscoveryRunSummaryOut])
def list_discovery_runs(
    base_url: str | None = Query(None, description="Filter by exact base_url"),
    dealer_id: int | None = Query(None),
    limit: int = Query(20, le=100),
    db: Session = Depends(get_db),
):
    """Discovery run history — powers a 're-detect' / diagnostics view. Runs
    exist independently of dealers (discovery can run before a dealer is
    saved), so both filters are optional."""
    stmt = select(DiscoveryRun).order_by(DiscoveryRun.id.desc()).limit(limit)
    if base_url:
        stmt = stmt.where(DiscoveryRun.base_url == base_url.strip())
    if dealer_id is not None:
        stmt = stmt.where(DiscoveryRun.dealer_id == dealer_id)
    return list(db.execute(stmt).scalars())


@router.get("/discover/runs/{run_id}", response_model=DiscoveryRunStatusOut)
def discovery_run_status(
    run_id: int,
    include_report: bool = Query(False, description="Include the full structured diagnostics report"),
    db: Session = Depends(get_db),
):
    """Poll target for a background discovery run started by POST /discover."""
    run = db.get(DiscoveryRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Discovery run not found")
    result = None
    if run.result is not None:
        result = DiscoveryOut(**run.result, run_id=run.id, report_available=run.report is not None)
    return DiscoveryRunStatusOut(
        run_id=run.id, status=run.status, phase=run.phase,
        phases_done=run.phases_done, phases_total=run.phases_total,
        started_at=run.started_at, finished_at=run.finished_at, error=run.error,
        result=result, report=run.report if include_report else None,
    )


@router.post("/discover/runs/{run_id}/cancel", response_model=DiscoveryCancelOut)
def cancel_discovery_run(
    run_id: int,
    job_id: str | None = Query(None, description="Celery task id, if the caller has one"),
    db: Session = Depends(get_db),
):
    """Stop an in-flight discovery run. Cooperative, same as sync cancellation —
    the coordinator stops between strategies/phases and the run lands on
    'cancelled' with whatever evidence it had already gathered."""
    run = db.get(DiscoveryRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Discovery run not found")
    job = discovery_local_job_for_run(run_id)
    in_flight = run.status in ("queued", "running") or (job is not None and job.finished_at is None)

    outcome = cancel_discovery(run_id, job_id, in_flight=in_flight)
    if outcome["status"] == "not_running":
        return DiscoveryCancelOut(
            status="not_running", job_id="", run_id=run_id, mode=outcome["mode"],
            detail="No discovery run is currently running for this id.",
        )
    log.info("Discovery cancel requested run=%s mode=%s job=%s",
             run_id, outcome["mode"], outcome["job_id"])
    return DiscoveryCancelOut(
        status="cancelling", job_id=outcome["job_id"] or f"run-{run_id}",
        run_id=run_id, mode=outcome["mode"],
        detail="Stop requested — the run will finish its current phase and stop.",
    )


@router.post("/discover/runs/{run_id}/force-finalize", response_model=DiscoveryRunStatusOut)
def force_finalize_discovery_run(
    run_id: int,
    db: Session = Depends(get_db),
):
    """Admin-only: Force a stuck discovery run (queued or running) to finalize.

    Used to recover from the zombie-run bug where a run never starts but remains
    queued forever, blocking all subsequent discovery attempts for that base_url.
    The run is marked as failed with a note about manual recovery.
    """
    run = db.get(DiscoveryRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Discovery run not found")

    if run.status not in ("queued", "running"):
        raise HTTPException(
            status_code=409,
            detail=f"Run is already {run.status}; can only force-finalize queued/running runs",
        )

    # Finalize as failed with a note that it was manually recovered
    _finalize(
        run_id,
        status="failed",
        result=None,
        error="Manually force-finalized by admin (recovery from stuck/zombie run)",
    )
    db.refresh(run)
    log.info("Force-finalized discovery run %s (was %s)", run_id, run.status)

    return discovery_run_status(run_id, include_report=False, db=db)


@router.get("/providers/list", response_model=list[str])
def list_providers(db: Session = Depends(get_db)):
    """Distinct website providers — powers the 'reuse existing scraper' hint in the UI."""
    stmt = (select(Dealer.website_provider)
            .where(Dealer.website_provider.is_not(None))
            .group_by(Dealer.website_provider)
            .order_by(func.count().desc()))
    return [row for row in db.execute(stmt).scalars()]


@router.get("/{dealer_id}", response_model=DealerOut)
def get_dealer(dealer_id: int, db: Session = Depends(get_db)):
    return _get_or_404(db, dealer_id)


@router.patch("/{dealer_id}", response_model=DealerOut)
def update_dealer(dealer_id: int, payload: DealerUpdate, db: Session = Depends(get_db)):
    dealer = _get_or_404(db, dealer_id)
    changes = payload.model_dump(exclude_unset=True)
    # Validate against the POST-patch pair: either field alone can invalidate the
    # other. Untouched pairs are left alone so an unrelated PATCH (e.g. {"active":
    # false}) still works on a dealer whose config is already broken.
    if "adapter_mode" in changes or "scrape_config" in changes or "base_url" in changes:
        _validate_config_or_422(
            changes.get("adapter_mode") or dealer.adapter_mode,
            changes["scrape_config"] if "scrape_config" in changes else dealer.scrape_config,
            changes.get("base_url") or dealer.base_url,
        )
        if "scrape_config" in changes:
            _reject_identity_conflict_or_422(db, changes["scrape_config"], dealer.id)
    for field, value in changes.items():
        setattr(dealer, field, value)
    db.commit()
    db.refresh(dealer)
    return dealer


@router.delete("/{dealer_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_dealer(
    dealer_id: int,
    purge: bool = Query(
        False,
        description="Also delete this dealer's inventory and history. Destructive — "
                    "the SOW requires history to be retained, so use only to remove "
                    "test data or a dealer added in error.",
    ),
    db: Session = Depends(get_db),
):
    """Delete a dealership.

    Inventory, price history, audit logs, snapshots and sync runs all reference
    the dealer, so a plain delete would violate those foreign keys. Rather than
    fail with a 500, this refuses with 409 when the dealer holds data and points
    at the two safe options:

      * deactivate (recommended) — retires the dealer, keeps all history
      * ?purge=true              — removes the dealer AND everything it owns

    Purge exists because a dealer created by mistake (e.g. pointed at the wrong
    website) leaves inventory that would otherwise be permanently wrong.
    """
    dealer = _get_or_404(db, dealer_id)

    car_count = db.execute(
        select(func.count()).select_from(Car).where(Car.dealer_id == dealer_id)
    ).scalar_one()

    if car_count and not purge:
        raise HTTPException(
            status_code=409,
            detail=(
                f"This dealership has {car_count} vehicle(s) on record. Deactivate it to "
                f"retire it while keeping history, or delete with purge=true to remove "
                f"the dealership and all of its inventory and history permanently."
            ),
        )

    # Discovery history survives the dealer either way — it documents how the
    # site behaved at detection time, independent of whether the dealer itself
    # was kept. Null the FK rather than deleting so it can never block the
    # delete (the dealer may hold discovery_runs even when car_count is 0).
    db.execute(
        update(DiscoveryRun).where(DiscoveryRun.dealer_id == dealer_id).values(dealer_id=None)
    )

    if purge:
        # FK-safe order: children before parents. Snapshots reference scrape_runs,
        # and price history / audit logs reference cars.
        car_ids = select(Car.id).where(Car.dealer_id == dealer_id)
        db.execute(delete(CarPriceHistory).where(CarPriceHistory.car_id.in_(car_ids)))
        db.execute(delete(VehicleAuditLog).where(VehicleAuditLog.dealer_id == dealer_id))
        db.execute(delete(DailyInventorySnapshot).where(
            DailyInventorySnapshot.dealer_id == dealer_id))
        db.execute(delete(ScrapeRun).where(ScrapeRun.dealer_id == dealer_id))
        db.execute(delete(Car).where(Car.dealer_id == dealer_id))
        log.warning("Purged dealer %s (%s) and %s vehicle(s)", dealer_id, dealer.name, car_count)

    db.delete(dealer)
    db.commit()


@router.post("/{dealer_id}/activate", response_model=DealerOut)
def activate_dealer(dealer_id: int, db: Session = Depends(get_db)):
    dealer = _get_or_404(db, dealer_id)
    dealer.active = True
    db.commit(); db.refresh(dealer)
    return dealer


@router.post("/{dealer_id}/deactivate", response_model=DealerOut)
def deactivate_dealer(dealer_id: int, db: Session = Depends(get_db)):
    dealer = _get_or_404(db, dealer_id)
    dealer.active = False
    db.commit(); db.refresh(dealer)
    return dealer


@router.get("/{dealer_id}/runs", response_model=list[SyncRunOut])
def dealer_runs(dealer_id: int, limit: int = Query(20, le=100), db: Session = Depends(get_db)):
    """Synchronisation history for this dealer (SOW §5 / dashboard sync status)."""
    _get_or_404(db, dealer_id)
    stmt = (select(ScrapeRun)
            .where(ScrapeRun.dealer_id == dealer_id)
            .order_by(ScrapeRun.id.desc()).limit(limit))
    return list(db.execute(stmt).scalars())


def _latest_run(db: Session, dealer_id: int) -> ScrapeRun | None:
    return db.execute(
        select(ScrapeRun)
        .where(ScrapeRun.dealer_id == dealer_id)
        .order_by(ScrapeRun.id.desc())
        .limit(1)
    ).scalar_one_or_none()


def _running_run(db: Session, dealer_id: int) -> ScrapeRun | None:
    """The dealer's in-flight run, ignoring rows abandoned by a killed worker."""
    run = db.execute(
        select(ScrapeRun)
        .where(ScrapeRun.dealer_id == dealer_id, ScrapeRun.status == "running")
        .order_by(ScrapeRun.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    if run is None:
        return None
    started = run.started_at
    if started is not None:
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - started).total_seconds()
        if age > settings.sync_stale_after_seconds:
            log.warning("Ignoring stale running run #%s for dealer %s (%.0fs old)",
                        run.id, dealer_id, age)
            return None
    return run


def _progress(run: ScrapeRun | None) -> SyncProgressOut | None:
    if run is None:
        return None
    started = run.started_at
    if started is not None and started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    end = run.finished_at or datetime.now(timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    elapsed = (end - started).total_seconds() if started else 0.0
    percent = None
    if run.reported_total:
        percent = round(min(100.0, run.scraped_count / run.reported_total * 100), 1)
    return SyncProgressOut(
        run_id=run.id,
        status=run.status,
        scraped_count=run.scraped_count,
        reported_total=run.reported_total,
        new_count=run.new_count,
        updated_count=run.updated_count,
        price_changed_count=run.price_changed_count,
        removed_count=run.removed_count,
        percent=percent,
        elapsed_seconds=round(elapsed, 1),
    )


@router.post(
    "/{dealer_id}/sync",
    response_model=SyncJobOut,
    status_code=status.HTTP_202_ACCEPTED,
)
def trigger_sync(dealer_id: int, db: Session = Depends(get_db)):
    """Start a background scrape for one dealer.

    Runs via Celery when Redis is up, otherwise on this process's thread pool —
    either way the scrape happens in the background and this returns immediately.
    """
    dealer = _get_or_404(db, dealer_id)
    if not dealer.active:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Dealer is inactive - activate it before syncing",
        )
    # Catches dealers onboarded before this validation existed: fail the click,
    # not a background job the user has to go read a run row to diagnose.
    _validate_config_or_422(dealer.adapter_mode, dealer.scrape_config)

    # Already scraping? Return the in-flight job instead of starting a duplicate.
    in_flight = _running_run(db, dealer_id)
    if in_flight is not None:
        job = local_job_for_dealer(dealer_id)
        return SyncJobOut(
            status="running",
            job_id=job.job_id if job else f"run-{in_flight.id}",
            dealer_id=dealer_id,
            mode="local" if job else "celery",
            run_id=in_flight.id,
        )

    outcome = dispatch_sync(dealer_id, dealer.base_url)
    if outcome["status"] == "no_worker":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=("SYNC_BACKEND=celery but no Celery worker is consuming the queue. "
                    "Start one with 'npm run worker', or set SYNC_BACKEND=local in .env "
                    "to run syncs in-process."),
        )
    log.info("Sync dispatched dealer=%s mode=%s status=%s job=%s",
             dealer_id, outcome["mode"], outcome["status"], outcome["job_id"])
    return SyncJobOut(
        status=outcome["status"],
        job_id=outcome["job_id"],
        dealer_id=dealer_id,
        mode=outcome["mode"],
    )


def _status_from_run(running: ScrapeRun | None, latest: ScrapeRun | None) -> str:
    """Map DB run state onto the Celery-style vocabulary the UI polls on."""
    if running is not None:
        return "STARTED"
    if latest is None:
        return "PENDING"
    if latest.status == "failed":
        return "FAILURE"
    if latest.status == "cancelled":
        return "REVOKED"
    return "SUCCESS"


@router.post("/{dealer_id}/sync/cancel", response_model=SyncCancelOut)
def cancel_dealer_sync(
    dealer_id: int,
    job_id: str | None = Query(None, description="Celery task id, if the caller has one"),
    db: Session = Depends(get_db),
):
    """Stop this dealer's in-flight scrape.

    Cooperative: the engine stops at its next commit checkpoint, so everything
    already scraped is kept and the run lands on 'cancelled' instead of being
    stranded at 'running' the way killing the worker leaves it.
    """
    _get_or_404(db, dealer_id)
    running = _running_run(db, dealer_id)
    job = local_job_for_dealer(dealer_id)
    in_flight = running is not None or (job is not None and job.finished_at is None)

    outcome = cancel_sync(dealer_id, job_id, in_flight=in_flight)
    if outcome["status"] == "not_running":
        return SyncCancelOut(
            status="not_running", job_id="", dealer_id=dealer_id, mode=outcome["mode"],
            detail="No sync is currently running for this dealer.",
        )
    log.info("Sync cancel requested dealer=%s mode=%s job=%s",
             dealer_id, outcome["mode"], outcome["job_id"])
    return SyncCancelOut(
        status="cancelling",
        job_id=outcome["job_id"] or (f"run-{running.id}" if running else ""),
        dealer_id=dealer_id,
        mode=outcome["mode"],
        detail="Stop requested — the scrape will finish its current batch and stop.",
    )


@router.get("/{dealer_id}/sync/current", response_model=SyncJobStatusOut)
def current_sync_status(dealer_id: int, db: Session = Depends(get_db)):
    """Live sync state for a dealer without needing a job id (survives a page reload)."""
    _get_or_404(db, dealer_id)
    running = _running_run(db, dealer_id)
    latest = running or _latest_run(db, dealer_id)
    job = local_job_for_dealer(dealer_id)
    # A job that has been dispatched but whose worker has not yet written its
    # ScrapeRun row is still a sync in flight. Without this, a poll landing in
    # that window reports the PREVIOUS run as the current state and the caller
    # concludes the sync already finished.
    job_status = "STARTED" if (job is not None and job.finished_at is None) \
        else _status_from_run(running, latest)
    return SyncJobStatusOut(
        job_id=job.job_id if job else (f"run-{latest.id}" if latest else ""),
        dealer_id=dealer_id,
        job_status=job_status,
        mode="local" if job else "celery",
        result=job.result if job else None,
        progress=_progress(latest),
        latest_run=latest,
    )


@router.get("/{dealer_id}/sync/{job_id}", response_model=SyncJobStatusOut)
def sync_job_status(dealer_id: int, job_id: str, db: Session = Depends(get_db)):
    """Task progress for a previously queued sync; primary history remains GET .../runs.

    Handles both backends: in-process jobs (job_id 'local-…') read the dispatcher's
    job table, Celery jobs read the result backend.
    """
    _get_or_404(db, dealer_id)
    latest = _latest_run(db, dealer_id)
    running = _running_run(db, dealer_id)

    if is_local_job(job_id):
        job = local_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Unknown sync job")
        return SyncJobStatusOut(
            job_id=job_id,
            dealer_id=dealer_id,
            job_status=job.status,
            mode="local",
            result=job.result,
            progress=_progress(running or latest),
            latest_run=latest,
        )

    # Synthetic id handed out when a sync was already in flight (POST returned
    # "running" with no Celery task id of its own) — answer from the run rows.
    if job_id.startswith("run-"):
        return SyncJobStatusOut(
            job_id=job_id,
            dealer_id=dealer_id,
            job_status=_status_from_run(running, latest),
            mode="celery",
            progress=_progress(running or latest),
            latest_run=latest,
        )

    # Celery-backed job. Reading AsyncResult needs the result backend; if Redis is
    # down we still answer from the ScrapeRun rows rather than hanging or 500ing.
    try:
        from worker.tasks import scrape_dealer

        result = scrape_dealer.AsyncResult(job_id)
        job_status = result.status
        payload = result.result if isinstance(result.result, dict) else None
    except Exception as exc:
        log.warning("Could not read Celery result for %s: %s", job_id, exc)
        job_status = _status_from_run(running, latest)
        payload = None

    return SyncJobStatusOut(
        job_id=job_id,
        dealer_id=dealer_id,
        job_status=job_status,
        mode="celery",
        result=payload,
        progress=_progress(running or latest),
        latest_run=latest,
    )
