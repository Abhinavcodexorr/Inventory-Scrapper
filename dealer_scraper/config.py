"""Runtime settings, loaded from environment / .env."""
from __future__ import annotations

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://scraper:scraper@localhost:5432/dealer_inventory"
    scraper_proxy: str | None = None
    # Optional: proxy credentials kept out of the URL, so a password containing
    # '@', ':', '/' or '#' needs no percent-encoding. Override any credentials
    # embedded in SCRAPER_PROXY. Parsed by dealer_scraper/proxy.py.
    scraper_proxy_username: str | None = None
    scraper_proxy_password: SecretStr | None = None

    # -- API authentication --
    # Shared-secret key clients must send as `X-API-Key`. When unset and
    # api_auth_required is True (the default), every request is refused with
    # 503 rather than silently served unauthenticated — "forgot to set API_KEY
    # in prod" must fail loudly, not open the door.
    api_key: str | None = None
    api_auth_required: bool = True

    # -- Operator login (admin panel) --
    # Humans sign in with email + password and get a signed session cookie;
    # API_KEY above stays for machines (health checks, scripts, integrations).
    # Splitting them means the browser bundle no longer has to carry a secret.
    #
    # Key the session cookie is signed with. Left unset it is DERIVED from
    # API_KEY, so there is one fewer value to configure and sessions survive a
    # restart. Set it explicitly to rotate sessions without rotating the API key.
    session_secret: str | None = None
    # How long a login lasts before the operator must sign in again.
    session_max_age_hours: int = 12
    # Send the cookie only over HTTPS. Must stay False until the site has TLS,
    # or the browser silently drops the cookie and login appears to do nothing.
    session_cookie_secure: bool = False

    # Origins the frontend is served from. Credentials ARE allowed now, because
    # the session cookie has to travel on cross-origin requests during local
    # development (Vite on :5173 calling the API on :8000). In production nginx
    # serves UI and API on one origin, so CORS is not involved at all. The
    # CSRF surface that opens up is closed by the cookie's SameSite=Lax, which
    # stops browsers attaching it to cross-site writes; the allowlist below must
    # therefore never become "*", which the CORS spec forbids with credentials.
    cors_allow_origins: list[str] = ["http://localhost:5173", "http://localhost:3000"]
    default_delay_seconds: float = 1.0
    reconcile_min_fraction: float = 0.9
    # Runaway breaker: a run that has scraped more than
    #   max(runaway_floor, runaway_multiple * max(site total, vehicles already held))
    # is not reading inventory, it is reading the same pages again. cccmotors.ca
    # "scraped" 1,072,212 vehicles for a 199-vehicle dealer and held the only
    # worker for 90 minutes; the floor keeps a genuinely large first sync legal.
    runaway_floor: int = 10_000
    runaway_multiple: float = 3.0
    # A run where more than this fraction of scraped rows were repeats of a
    # vehicle already seen in the SAME run is re-reading pages (a feed whose page
    # parameter is ignored). Only judged once a run is big enough to mean it.
    max_duplicate_fraction: float = 0.5
    min_rows_for_duplicate_check: int = 20
    http_timeout: float = 30.0
    # Block image/font/media bytes and known analytics/tracker scripts in the
    # browser adapter (dealer_scraper/adapters/blocking.py) — extraction only
    # ever reads element URL attributes, never the bytes. Per-dealer override:
    # scrape_config["block_resources"]. Proxy bandwidth, not CPU, is the
    # dominant per-scrape running cost (HANDOFF/06).
    resource_blocking_default: bool = True
    # Default browser adapter engine: "sync" (the original single-page
    # Playwright adapter) or "async" (dealer_scraper/adapters/browser_async.py
    # — parallel URL pagination, page pool). Kept at "sync" until async is
    # actually shown faster with a real multi-trial benchmark: the only
    # browser-mode dealer on record (Cardora, paginate="click") gets none of
    # async's parallel-fetch benefit — that path only exists for
    # paginate="url" — so async just adds thread/event-loop overhead for it.
    # Per-dealer override: scrape_config["browser_engine"].
    browser_engine_default: str = "sync"
    # How many pages the async browser adapter fetches concurrently per
    # dealer. Per-dealer override: scrape_config["fetch_concurrency"]. Backs
    # off to 1 automatically on a 403/429/WAF signal (see browser_async.py
    # _BackoffGate) regardless of this setting.
    per_dealer_fetch_concurrency: int = 4

    # User-Agent for API-mode requests. The browser adapter and discovery both
    # drive a real Chromium and so present as Chrome; json_api used to send
    # "InventoryBot/1.0" instead, which meant the SAME dealer site could accept
    # one adapter and refuse the other. clutch.ca's WAF returned 403 to every
    # API request purely on that header — no proxy involved, and a Chrome UA
    # cleared it immediately.
    #
    # Set this to a self-identifying string (e.g. "InventoryBot/1.0 (+you@co)")
    # if a target's terms require it; expect WAF-protected sites to refuse it.
    scraper_user_agent: str = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    )
    # A car removed for longer than this many days (and still absent) is auto-marked 'sold'.
    # Set to None to disable auto-sold (e.g. if sales are confirmed only via dealer data).
    sold_after_days: int | None = 7

    # Which backend executes a manual sync:
    #   local  — always in-process. Clicking Sync runs the scrape immediately and
    #            NO Celery worker is needed. Simplest for local/UI work; the cost
    #            is that the scrape dies if the API restarts (e.g. --reload).
    #   celery — always queue it. Fails loudly when no worker is listening rather
    #            than silently running an hour-long scrape inside the API.
    #   auto   — Celery when a worker is actually consuming, else in-process.
    # Anything unrecognised is treated as "auto".
    sync_backend: str = "auto"

    # Celery / Redis (HANDOFF Task 3)
    redis_url: str = "redis://localhost:6379/0"
    celery_worker_concurrency: int = 3
    # Seconds to wait for a per-host scrape lock before failing the task.
    scrape_host_lock_timeout: int = 3600
    scrape_host_lock_ttl: int = 7200
    # A ScrapeRun still marked 'running' after this long is considered abandoned
    # (worker killed mid-scrape) and no longer blocks a new manual sync.
    sync_stale_after_seconds: int = 7200

    # -- Daily synchronisation schedule (SOW §3) --
    # Celery Beat drives the daily sync so the schedule lives with the app rather
    # than in a host crontab: one deployment artefact, and the cadence is a config
    # value the client can change ("or at a configurable schedule").
    daily_sync_enabled: bool = True
    # Local time of day for the run. Off-peak by default — gentler on dealer sites
    # and keeps the whole fleet well inside the 24h window.
    daily_sync_hour: int = 3
    daily_sync_minute: int = 15
    # IANA timezone the hour/minute above are interpreted in. Dealers are Canadian,
    # so "off-peak" means off-peak for them, not UTC.
    daily_sync_timezone: str = "America/Toronto"
    # Seconds between enqueuing each dealer, so 100 dealers don't all hit their
    # sites in the same instant. Per-host locks prevent same-host overlap, but this
    # also spreads load on our own workers and proxy pool.
    daily_sync_stagger_seconds: int = 30

    # -- Discovery engine (dealer_scraper/discovery/) --
    # Overall wall-clock budget for one discovery run (DiscoveryContext.deadline).
    discovery_budget_seconds: float = 300.0
    # Discovery yields the host lock to an in-progress SCRAPE quickly rather than
    # queueing behind it (scrapes can run up to scrape_host_lock_timeout, ~1hr).
    # Minimum share of scraped vehicles that must carry at least one describing
    # field (year/make/model/price) for a run to count as a success. A listing
    # whose cards the extractor cannot read still yields rows — links are found,
    # fields are not — so without this a broken card_selector reports a healthy
    # sync over entirely empty inventory. Deliberately low: it exists to catch a
    # broken extractor, not to police sites with sparse listings.
    min_described_fraction: float = 0.5

    discovery_host_lock_timeout: int = 60
    # How long a discovery host lock survives its holder dying (kill -9, container
    # restart, uvicorn --reload mid-run). Sized to the discovery budget plus
    # headroom, NOT the 2h scrape TTL it used to inherit — an orphaned lock blocks
    # every retry for that dealer until it expires, and two hours of that looks
    # exactly like a broken product.
    discovery_host_lock_ttl: int = 900
    # Cap on active httpx pagination/politeness probes per run (net/politeness.py, Phase 3).
    discovery_probe_budget: int = 20
    # Optional Claude fallback tier (llm.py, Phase 4) — off until explicitly enabled.
    discovery_llm_enabled: bool = False
    anthropic_api_key: str | None = None
    discovery_llm_max_calls: int = 3
    # Scalar overrides applied onto HeuristicsConfig via HeuristicsConfig.from_overrides().
    discovery_heuristics_overrides: dict = {}


settings = Settings()
