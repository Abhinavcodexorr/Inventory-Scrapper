"""Decision engine: turn a winning Candidate into a validated scrape_config.

Builds typed configs, validates through the same registry the dealer save path
uses, scores confidence from detection signals, and exposes assess_readiness()
for signal-based verdicts. verify() remains available for optional smoke tests
but is not invoked during discovery — scraping belongs to the scraper stage.
"""
from __future__ import annotations

import copy
import time
from dataclasses import dataclass, field as dc_field

from ..adapters.registry import validate_scrape_config, build_adapter
from .context import Candidate
from .heuristics import DEFAULT_HEURISTICS, HeuristicsConfig
from .net.headers import classify_headers
from .scoring import CandidateScore, ConfidenceModel
from .types import BrowserConfig, JsonApiConfig


@dataclass
class VerificationResult:
    ok: bool
    attempted: bool = False
    vehicles_yielded: int = 0
    identity_rate: float = 0.0
    field_coverage: dict[str, float] = dc_field(default_factory=dict)
    total_count: int | None = None
    duration_seconds: float = 0.0
    error: str | None = None


class JsonApiConfigBuilder:
    """Builds a json_api scrape_config from a JSON_API Candidate's accumulated
    draft (endpoint, field_map, pagination, headers, ... — set by the
    coordinator as it sniffs traffic and detects pagination)."""

    def build(self, cand: Candidate) -> dict:
        draft = cand.draft
        cfg = JsonApiConfig(
            endpoint=draft.get("endpoint"),
            endpoints=draft.get("endpoints"),
            page_size=max(1, int(draft.get("page_size") or 25)),
            # `or 1` turned a zero-based feed's start of 0 into 1, skipping its
            # whole first page (clutch.ca: page=0 is the first 32 vehicles).
            start_page=int(1 if draft.get("start_page") is None else draft["start_page"]),
            page_step=draft.get("page_step"),
            list_path=draft.get("list_path") or "",
            total_path=draft.get("total_path"),
            field_map=draft.get("field_map") or {},
            headers=draft.get("headers") or {},
            max_pages=draft.get("max_pages"),
            method=draft.get("method"),
            body=draft.get("body"),
            defaults=draft.get("defaults"),
            url_base=draft.get("url_base"),
            next_build_id_url=draft.get("next_build_id_url"),
            detail=draft.get("detail"),
            delay_seconds=draft.get("delay_seconds"),
        )
        return cfg.to_dict()


class BrowserConfigBuilder:
    """Builds a browser scrape_config from an HTML_LISTING Candidate's draft
    (list_url, card_anchor, id_regex, pagination mode, ... — set by the
    coordinator from the DOM probe + pagination verification)."""

    def build(self, cand: Candidate) -> dict:
        draft = cand.draft
        cfg = BrowserConfig(
            list_url=draft["list_url"],
            base_url=draft.get("base_url"),
            card_anchor=draft.get("card_anchor"),
            wait_selector=draft.get("wait_selector"),
            id_regex=draft.get("id_regex"),
            extract=draft.get("extract", "dom"),
            paginate=draft.get("paginate", "scroll"),
            page_url=draft.get("page_url"),
            next_selector=draft.get("next_selector"),
            scroll_rounds=draft.get("scroll_rounds"),
            max_pages=draft.get("max_pages"),
            headless=draft.get("headless", True),
            settle_ms=draft.get("settle_ms"),
        )
        return cfg.to_dict()


class DecisionEngine:
    def __init__(self, heuristics: HeuristicsConfig = DEFAULT_HEURISTICS) -> None:
        self.heuristics = heuristics
        self.scoring = ConfidenceModel(heuristics)
        self._json_builder = JsonApiConfigBuilder()
        self._browser_builder = BrowserConfigBuilder()

    def optimize_config(self, adapter_mode: str, config: dict, vehicles_found: int) -> None:
        """Apply learned optimizations in-place to a config dict.

        Reduces iteration limits on small sites to speed up future syncs.
        """
        from .learning import optimize_config
        optimize_config(config, adapter_mode, vehicles_found)

    def build_config(self, adapter_mode: str, cand: Candidate) -> dict:
        if adapter_mode == "json_api":
            return self._json_builder.build(cand)
        if adapter_mode == "browser":
            return self._browser_builder.build(cand)
        raise ValueError(f"Unknown adapter_mode '{adapter_mode}'")

    def validate(self, adapter_mode: str, config: dict, base_url: str) -> str | None:
        """Returns None if the config is usable, or an error message if it would
        be rejected by the exact validator the dealer save path uses."""
        try:
            validate_scrape_config(adapter_mode, config, base_url)
        except ValueError as exc:
            return str(exc)
        return None

    def assess_readiness(
        self, adapter_mode: str, cand: Candidate, score: CandidateScore,
    ) -> tuple[bool, str]:
        """Signal-based readiness check — discovery must not run the scraper.

        Uses evidence collected during detection (field_map, link counts,
        confidence score) rather than iterating vehicles through an adapter.
        """
        w = self.heuristics.scoring
        draft = cand.draft or {}

        if score.hard_blockers:
            return False, f"Hard blockers: {', '.join(score.hard_blockers)}"

        if adapter_mode == "json_api":
            field_map = draft.get("field_map") or {}
            if len(field_map) < 4:
                return False, "Insufficient field_map coverage (need ≥4 fields)"
            if not any(f in field_map for f in self.heuristics.identity_fields):
                return False, "No identity field (vin/external_id) mapped"
            array_len = draft.get("sample_array_len") or 0
            if array_len < 1:
                return False, "No sample vehicles observed in API response"

        elif adapter_mode == "browser":
            dom_count = int(draft.get("dom_link_count") or draft.get("vehicles_found") or 0)
            jsonld = int(draft.get("jsonld_count") or 0)
            if dom_count < self.heuristics.candidate_score_min and jsonld < 5:
                return False, "Insufficient vehicle signals on listing page"
            if not draft.get("list_url") or not draft.get("card_anchor"):
                return False, "Missing list_url or card_anchor"

        else:
            return False, f"Unknown adapter_mode '{adapter_mode}'"

        if score.total < w.needs_review_threshold:
            return False, f"Confidence score too low ({score.total:.2f})"

        # Sitemap contradiction: the site's own sitemap says there are far more
        # vehicles than this config can reach, and nothing proved pagination
        # works. That combination means "page 1 only" — the config will look
        # healthy and silently under-report the lot forever. clutch.ca shipped
        # at high/ready on 32 of ~3,100 vehicles this way, because sitemap
        # disagreement was only ever scored as a soft signal.
        sitemap_count = int(draft.get("sitemap_detail_count") or 0)
        found = int(draft.get("vehicles_found") or 0)
        if (
            sitemap_count > 0
            and found > 0
            and not draft.get("pagination_verified")
            and sitemap_count >= found * self.heuristics.sitemap_contradiction_factor
        ):
            return False, (
                f"The site's sitemap lists {sitemap_count} vehicle pages but this "
                f"configuration only reaches {found}, and pagination could not be "
                f"proven — it would capture the first page only. Needs a manual "
                f"pagination setting before this dealer can be trusted."
            )

        return True, ""

    def verify(self, adapter_mode: str, config: dict, budget_seconds: float = 90.0) -> VerificationResult:
        """Run a bounded iteration of the adapter to prove the config yields valid vehicles.

        Clones config with limits (json_api max_pages=2; browser max_pages=1,
        scroll_rounds≤2). Strips volatile auth headers. Iterates up to 25 vehicles
        within the time budget, computing identity_rate and per-field coverage.
        Fetches and validates total_count. Returns ok=True only if successful
        with reasonable field coverage.
        """
        result = VerificationResult(ok=False, attempted=True)
        start_time = time.time()

        try:
            # Clone config with limits applied
            limited_config = copy.deepcopy(config)

            if adapter_mode == "json_api":
                limited_config["max_pages"] = 2
                limited_config["headers"] = limited_config.get("headers") or {}
                durable, _volatile = classify_headers(limited_config["headers"])
                limited_config["headers"] = durable

            elif adapter_mode == "browser":
                limited_config["max_pages"] = 1
                limited_config["scroll_rounds"] = min(
                    limited_config.get("scroll_rounds") or 2, 2
                )
                limited_config["headless"] = True
            else:
                result.error = f"Unknown adapter_mode '{adapter_mode}'"
                result.duration_seconds = time.time() - start_time
                return result

            # Build adapter with limited config
            adapter = build_adapter(adapter_mode, limited_config)

            # Fetch total_count early for sanity check
            try:
                total_count = adapter.fetch_total_count()
                result.total_count = total_count
            except Exception as e:
                result.error = f"Failed to fetch total count: {str(e)[:100]}"
                result.duration_seconds = time.time() - start_time
                return result

            # Sanity check total_count
            if total_count is not None:
                if not (0 < total_count < 100_000):
                    result.error = f"Invalid total_count: {total_count}"
                    result.duration_seconds = time.time() - start_time
                    return result

            # Iterate vehicles up to 25 with time budget
            max_vehicles = 25
            remaining_budget = budget_seconds - (time.time() - start_time)

            if remaining_budget <= 0:
                result.error = "Time budget exhausted before iteration"
                result.duration_seconds = time.time() - start_time
                return result

            vehicles = []
            field_counts: dict[str, int] = {}
            total_identity_score = 0.0

            try:
                for vehicle in adapter.iter_vehicles():
                    if len(vehicles) >= max_vehicles:
                        break
                    if time.time() - start_time > budget_seconds:
                        break

                    vehicles.append(vehicle)

                    # Track field presence
                    for field in ("vin", "make", "model", "year", "mileage", "external_id"):
                        if getattr(vehicle, field, None) is not None:
                            field_counts[field] = field_counts.get(field, 0) + 1

                    # Compute identity score (VIN is strongest identity)
                    if vehicle.vin:
                        total_identity_score += 1.0
                    elif vehicle.external_id:
                        total_identity_score += 0.8
                    else:
                        total_identity_score += 0.0

            except Exception as e:
                result.error = f"Failed iterating vehicles: {str(e)[:100]}"
                result.duration_seconds = time.time() - start_time
                return result

            result.vehicles_yielded = len(vehicles)

            # Require at least 1 vehicle
            if result.vehicles_yielded < 1:
                result.error = "No vehicles yielded"
                result.duration_seconds = time.time() - start_time
                return result

            # Compute identity rate
            result.identity_rate = total_identity_score / len(vehicles) if vehicles else 0.0

            # Compute field coverage (% of vehicles with each required field)
            required_fields = ("vin", "make", "model", "year")
            min_coverage = 0.5  # At least 50% coverage required

            for field in required_fields:
                coverage = field_counts.get(field, 0) / len(vehicles)
                result.field_coverage[field] = coverage
                if coverage < min_coverage:
                    result.error = f"Field '{field}' coverage too low: {coverage:.0%}"
                    result.duration_seconds = time.time() - start_time
                    return result

            # Success
            result.ok = True
            result.error = None

        except Exception as e:
            result.error = f"Verification failed: {str(e)[:100]}"

        finally:
            result.duration_seconds = time.time() - start_time

        return result

    def score(self, cand: Candidate, evidence) -> CandidateScore:
        return self.scoring.score(cand, evidence)

    def verdict(self, score: CandidateScore, verified: bool) -> tuple[str, str, bool]:
        return self.scoring.verdict(score, verified)
