"""Learning: read and write site patterns to improve future discoveries.

Successful discoveries populate site_patterns table with patterns that worked
(API endpoints, pagination styles, field mappings, card selectors, CMS
fingerprints). On future runs for the same host, these patterns seed priors
to accelerate discovery and boost initial confidence.

Queries filter on scalar indexed columns only (host, cms_platform) for SQLite
parity — no JSON containment searches.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import and_, func
from sqlalchemy.orm import Session

from ..models import SitePattern
from .context import CandidateKind, DiscoveryContext, Candidate

log = logging.getLogger("scraper.discovery.learning")


@dataclass
class SitePatternPrior:
    """A prior learned from a previous successful discovery run."""
    host: str
    cms_platform: str | None
    pattern_kind: str  # listing_url, api_endpoint, pagination, field_map, card_selector, cms_fingerprint
    pattern: dict
    confidence: float  # 0..1; how much to trust this prior


def read_priors(db: Session, host: str, cms_platform: str | None = None) -> list[SitePatternPrior]:
    """Retrieve learned patterns for a host (optionally filtered by CMS platform).

    Returns a list of SitePatternPrior ordered by success_count (descending),
    so the most-proven patterns are applied first.

    Filters only rows where success_count > failure_count to skip patterns that
    have repeatedly failed.
    """
    try:
        query = db.query(SitePattern).filter(SitePattern.host == host)
        if cms_platform:
            query = query.filter(SitePattern.cms_platform == cms_platform)
        rows = query.filter(SitePattern.success_count > SitePattern.failure_count).order_by(
            SitePattern.success_count.desc()
        ).all()

        priors = []
        for row in rows:
            total = row.success_count + row.failure_count
            confidence = row.success_count / total if total > 0 else 0.0
            priors.append(SitePatternPrior(
                host=row.host,
                cms_platform=row.cms_platform,
                pattern_kind=row.pattern_kind,
                pattern=row.pattern,
                confidence=confidence,
            ))
        return priors
    except Exception as exc:
        log.debug("Failed to read priors for host %s: %s", host, exc)
        return []


def write_pattern(
    db: Session,
    host: str,
    cms_platform: str | None,
    pattern_kind: str,
    pattern: dict,
    source_run_id: int,
    success: bool = True,
) -> None:
    """Record a pattern from a discovery run.

    On success, increment success_count and set last_success_at; on failure,
    increment failure_count. Maintains a single row per (host, pattern_kind)
    via UniqueConstraint — upserts if the row already exists.
    """
    try:
        now = datetime.now(timezone.utc)
        row = db.query(SitePattern).filter(
            and_(
                SitePattern.host == host,
                SitePattern.pattern_kind == pattern_kind,
            )
        ).first()

        if row is None:
            row = SitePattern(
                host=host,
                cms_platform=cms_platform,
                pattern_kind=pattern_kind,
                pattern=pattern,
                success_count=1 if success else 0,
                failure_count=0 if success else 1,
                last_success_at=now if success else None,
                source_run_id=source_run_id,
            )
            db.add(row)
        else:
            if success:
                row.success_count += 1
                row.last_success_at = now
                row.pattern = pattern
                row.cms_platform = cms_platform
                row.source_run_id = source_run_id
            else:
                # A failed run only counts against the pattern. Replacing the
                # stored pattern with the one that just failed would throw away
                # the last configuration known to work for this host.
                row.failure_count += 1

        db.commit()
    except Exception as exc:
        log.debug("Failed to write pattern for host %s, kind %s: %s", host, pattern_kind, exc)
        db.rollback()


def apply_priors(ctx: DiscoveryContext, priors: list[SitePatternPrior]) -> None:
    """Apply learned patterns to seed candidates and initial evidence.

    Priors are applied before exploration to accelerate discovery on
    sites we've seen before:
    - API endpoints become JSON_API candidates with high learned_prior signal
    - Field maps and pagination are pre-populated in draft
    - Card selectors seed HTML_LISTING candidates
    - Each prior adds LEARNED_PRIOR evidence weighted by confidence
    """
    if not priors:
        return

    from .evidence import Evidence, EvidenceKind

    for prior in priors:
        if prior.pattern_kind == "api_endpoint":
            cand = Candidate(
                key=f"api:learned:{prior.pattern.get('endpoint', 'unknown')}",
                kind=CandidateKind.JSON_API,
                url=prior.pattern.get("endpoint", ""),
                draft={
                    "endpoint": prior.pattern.get("endpoint"),
                    "endpoints": prior.pattern.get("endpoints"),
                    "field_map": prior.pattern.get("field_map", {}),
                    "pagination": prior.pattern.get("pagination"),
                    "headers": prior.pattern.get("headers", {}),
                    "learned_prior": prior.confidence,
                },
            )
            ctx.upsert_candidate(cand)
            ctx.evidence.add(Evidence(
                EvidenceKind.LEARNED_PRIOR, f"prior_api_endpoint",
                cand.key, weight=prior.confidence,
                note=f"API endpoint from prior discovery (confidence: {prior.confidence:.1%})"))

        elif prior.pattern_kind == "listing_url":
            cand = Candidate(
                key=f"page:learned:{prior.pattern.get('listing_url', 'unknown')}",
                kind=CandidateKind.HTML_LISTING,
                url=prior.pattern.get("listing_url", ""),
                draft={
                    "listing_url": prior.pattern.get("listing_url"),
                    "card_anchor": prior.pattern.get("card_anchor"),
                    "id_regex": prior.pattern.get("id_regex"),
                    "pagination": prior.pattern.get("pagination"),
                    "learned_prior": prior.confidence,
                },
            )
            ctx.upsert_candidate(cand)
            ctx.evidence.add(Evidence(
                EvidenceKind.LEARNED_PRIOR, f"prior_listing_url",
                cand.key, weight=prior.confidence,
                note=f"Listing URL from prior discovery (confidence: {prior.confidence:.1%})"))

        elif prior.pattern_kind == "field_map":
            if ctx.candidates:
                best_cand = max(ctx.candidates.values(),
                                key=lambda c: getattr(c.score, 'total', 0) if c.score else 0)
                if best_cand.draft is None:
                    best_cand.draft = {}
                best_cand.draft["field_map_learned"] = prior.pattern
                ctx.evidence.add(Evidence(
                    EvidenceKind.LEARNED_PRIOR, "prior_field_map",
                    best_cand.key, weight=prior.confidence,
                    note=f"Field mapping from prior (confidence: {prior.confidence:.1%})"))

    ctx.report.add_note(
        f"Seeded {len(priors)} prior pattern(s) for {ctx.host} "
        f"({', '.join(p.pattern_kind for p in priors)})")


def should_write_pattern(candidate_score: float, verified: bool) -> bool:
    """Decide whether to persist this discovery as a learnable pattern.

    Only high-confidence, verified discoveries are written — noisy priors
    reduce future discovery speed, so we're conservative.
    """
    return verified and candidate_score >= 0.75


def optimize_config(config: dict, adapter_mode: str, vehicles_found: int) -> dict:
    """Apply learned optimizations to a scrape config.

    Reduces timeouts and iteration limits on sites with few vehicles to speed up
    future syncs, while keeping conservative defaults for unknown sites.

    Returns optimized config dict (mutates original).
    """
    if adapter_mode == "browser":
        # Sites with few vehicles don't need many scroll/page attempts
        if vehicles_found > 0 and vehicles_found < 100:
            config.setdefault("scroll_rounds", min(20, config.get("scroll_rounds", 40)))
            config.setdefault("max_pages", min(10, config.get("max_pages", 60)))
        elif vehicles_found >= 100:
            # Sites with more vehicles might benefit from more iterations
            config.setdefault("scroll_rounds", config.get("scroll_rounds", 40))
            config.setdefault("max_pages", config.get("max_pages", 60))

        # Settle time: conservative default 1500ms is safe; could go shorter
        # if page loads consistently fast, but we keep defaults on discovery
        config.setdefault("settle_ms", config.get("settle_ms", 1500))

    elif adapter_mode == "json_api":
        # Page size: most APIs like 25-50; optimize only if we've confirmed a better value
        config.setdefault("page_size", config.get("page_size", 25))
        # Similarly for max_pages — be conservative if not explicitly set
        if vehicles_found > 0 and "max_pages" not in config:
            # Estimate max_pages as ceil(vehicles_found / page_size) + 2 for safety
            page_size = config.get("page_size", 25)
            config["max_pages"] = max(2, min((vehicles_found // page_size) + 2, 200))

    return config


def detect_cms_platform(candidate_key: str, config: dict) -> str | None:
    """Infer CMS platform from discovered patterns and config hints.

    Looks at endpoint paths, URL structure, and known fingerprints to suggest
    CMS type (Shopify, Wordpress, custom, etc.) for prior seeding.

    Phase 6 stub — full implementation in Phase 7 with fingerprint library.
    """
    # Placeholder: could detect Shopify, WP, custom based on URL patterns
    # For now, return None — future phases add fingerprint detection
    return None
