# Multi-Dealer Vehicle Inventory Scraper

Config-driven daily scraper for used-car dealer sites. Cron runs it once a day;
it scrapes every dealer in the database, upserts inventory (no duplicates),
records price history, and marks cars that left a dealer's inventory as
`removed` with a `removed_at` date. Built for a cross-dealer price-comparison
portal.

## How it works

```
cron (daily)  ->  python -m dealer_scraper.run_daily
                     └─ for each ACTIVE dealer:
                          adapter (from dealers.scrape_config)  ->  yields vehicles
                          engine: upsert + content-hash change detection
                                  + price history + removal reconciliation (safety-gated)
```

- **Zero manual intervention day-to-day.** Once a dealer is configured, the cron
  handles it forever.
- **Adding an API-mode dealer = one config row**, no new code (see "Onboarding").
- **Removals are safety-gated:** if a scrape returns far fewer cars than the
  site reports (a partial/blocked run), removals are skipped so inventory is
  never corrupted — the run is marked `incomplete` and the cron exits non-zero
  so monitoring can alert.

## Project layout

```
dealer_scraper/
  config.py          settings (DATABASE_URL, proxy, delays, safety gate)
  db.py              engine + session factory
  models.py          Dealer, Car, CarPriceHistory, ScrapeRun  (SQLAlchemy 2.0)
  normalize.py       raw dict -> typed NormalizedVehicle + content hash
  engine.py          upsert / change-detection / price history / removal logic
  run_daily.py       cron entrypoint (scrapes all active dealers)
  seed.py            insert/update the clutch.ca dealer row
  adapters/
    base.py          DealerAdapter protocol
    json_api.py      config-driven JSON API adapter (the common case)
    registry.py      adapter_mode -> adapter class
    stub.py          in-memory adapter for tests
migrations/          Alembic (schema versioning)
tests/smoke_test.py  end-to-end: day1(10)->day2(8) removals, price history, reappearance
cron/                crontab example
docker-compose.yml   local Postgres + Redis for dev
```

## Server requirements (read before deploying)

| | Minimum | Why |
|---|---|---|
| **RAM** | **4 GB** | Scraping drives a real Chromium instance: 400–700 MB each, ~1.4 GB at the default concurrency of 2, plus the API, worker and Postgres. **A 1 GB or 2 GB server will be OOM-killed part-way through a large dealer** and the run will fail. |
| **CPU** | 2 vCPU | One core is saturated by a single browser render. |
| **Disk** | 40 GB+ | Chromium and its libraries are ~1 GB before any data. |
| Python | 3.11+ | |
| Node | **20.19+ or 22.12+** | Vite 8 refuses to build on Node 18. |
| System packages | `xvfb` | The worker runs Chromium under `xvfb-run`. Without it, bot-protected sites return an error page and syncs finish with zero vehicles. |

Verify before deploying, not after:

```bash
free -h     # MemTotal ~4 GB
nproc       # 2
node --version
```

**Dealer sites with bot protection** (Clutch.ca among them) reject requests
from datacentre IP ranges — their page loads but returns their own error
instead of inventory. That is not a scraper fault and no code change fixes it;
it needs a residential proxy in `SCRAPER_PROXY`. Budget for it as a metered
running cost.

## Setup (local)

Requires Python 3.10+.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env          # then edit DATABASE_URL / SCRAPER_PROXY

# Start local Postgres + Redis (or point DATABASE_URL at RDS)
docker compose up -d

# Create the schema
alembic revision --autogenerate -m "init"   # first time only
alembic upgrade head

# Configure the Clutch dealer (edit real API details in seed.py first — see below)
python -m dealer_scraper.seed

# Run the daily scrape once, by hand
python -m dealer_scraper.run_daily
```

## Running the web app

The hub is four processes. From the repo root:

```bash
npm install                # once — dev-only tooling (concurrently)
npm run redis              # start the Celery broker (local redis-server, else Docker)
npm run dev:all            # API :8000 + frontend :5173 + Celery worker
```

| Script | What it starts |
|---|---|
| `npm run api` | `uvicorn api.main:app` on :8000 (`/docs` for OpenAPI) |
| `npm run ui` | Vite dev server on :5173 |
| `npm run worker` | `celery -A worker.celery_app worker` |
| `npm run redis` / `redis:stop` | Redis broker — local `redis-server`, else the `docker-compose.yml` container |
| `npm run dev` | API + UI only (no worker) |
| `npm run dev:all` | API + UI + worker |

On Windows the worker script forces Celery's `solo` pool — the default `prefork`
pool relies on `fork()` and silently accepts tasks without ever running them.
Override with `CELERY_POOL=threads`.

### Manual sync (Sync now)

`POST /api/dealers/{id}/sync` runs the **same** pipeline as the daily cron
(`engine.run_dealer`) — no scrape logic is duplicated. It picks its backend at
call time:

```
POST /dealers/{id}/sync
   ├─ Redis reachable?  ── yes ──>  Celery task  worker.tasks.scrape_dealer
   │                                (per-host lock in Redis)
   └────────────────────  no ───>  in-process thread pool
                                    (per-host lock via threading.Lock)
```

Either way the request returns `202` immediately with a `job_id`; the UI polls
`GET /api/dealers/{id}/sync/current` for live progress. **Redis is optional for
development** — with the broker down, syncs still run, they just live inside the
API process instead of a durable queue. Run the worker for the production path.

### Redis on Windows without Docker

`npm run redis` prefers a plain `redis-server` binary and only falls back to
Docker. Docker Desktop needs hardware virtualization plus the
`VirtualMachinePlatform` / WSL2 features (admin PowerShell + a reboot), which is
a lot of setup for a broker — so on a bare Windows box, unpack a portable build
instead. No admin, no reboot:

```powershell
# https://github.com/tporadowski/redis/releases  ->  Redis-x64-5.0.14.1.zip
Expand-Archive Redis-x64-5.0.14.1.zip -DestinationPath $HOME\redis
```

`scripts/run-redis.mjs` looks in `%USERPROFILE%\redis` by default; point
`REDIS_HOME` elsewhere if you unpack somewhere else. It reads
`$REDIS_HOME/redis.local.conf` when present (bind 127.0.0.1, `noeviction` — Celery
loses queued tasks if the broker evicts keys). Redis 5 is fine here: the app only
uses `SET NX EX` / `GET` / `DEL` plus Celery's own commands.

For a broker that starts with Windows rather than on demand, install
[Memurai](https://www.memurai.com/) (`winget install Memurai.MemuraiDeveloper`,
needs admin) — it is Redis 7-compatible and registers a real service.

Without any of this the app still works end-to-end on the in-process fallback.

## Onboarding a dealer

For an **API-mode** dealer, no code is needed — insert a `dealers` row whose
`scrape_config` describes the endpoint and field mapping:

```jsonc
{
  "endpoint":   "https://api.<dealer>/vehicles?page={page}&limit={page_size}",
  "page_size":  25,
  "start_page": 1,
  "list_path":  "results.vehicles",   // dotted path to the car array
  "total_path": "results.total",      // dotted path to total count (the "3,915")
  "field_map": {
    "external_id": "stockNumber",     // stable per-car id (fallback key for dedup)
    "vin": "vin", "make": "make", "model": "model", "year": "year",
    "trim": "trim", "mileage": "mileage", "body_type": "bodyType",
    "price": "price", "url": "detailUrl"
  },
  "headers": {"Accept": "application/json"},
  "delay_seconds": 1.0
}
```

**Finding those values (one-time, ~5 min):** open the dealer's inventory page in
Chrome → DevTools → **Network** → filter **Fetch/XHR** → page through the list →
find the JSON request returning the cars. Read the response to fill `list_path`,
`total_path`, and `field_map`. `seed.py` shows a worked (placeholder) example for
Clutch — replace its endpoint/field names with the real ones you observe.

Sites with no clean JSON API — or ones behind a JS bot challenge — use the
`browser` adapter (Playwright); the engine is unchanged.

### Browser mode (JS-rendered / bot-protected sites, e.g. clutch.ca)

clutch.ca is a JS-rendered site behind AWS WAF bot protection, so a plain HTTP
scrape gets challenged. The `browser` adapter drives a real Chromium (which
passes the WAF JS challenge and carries its token), renders the grid, paginates,
and reads listing cards by their stable `/vehicles/<id>` link (Clutch's CSS
classes are build-hashed and unstable). Config lives in `seed.py` (`CLUTCH_CONFIG`).

```bash
pip install playwright
playwright install chromium

docker compose up -d                       # Postgres + Redis
cp .env.example .env
alembic revision --autogenerate -m "init"  # first time
alembic upgrade head
python -m dealer_scraper.seed              # Clutch (adapter=browser)
python -m dealer_scraper.run_daily         # renders + scrapes -> DB
```

Notes: the browser adapter pulls **listing-level** fields (year/make/model/trim/
mileage/price + detail URL). VIN, colors, transmission, drivetrain, etc.
live on each `/vehicles/<id>` detail page and need the detail-scrape pass (Phase 2).
If the WAF challenge doesn't clear headless, set `"headless": false` in the config;
for scale, route through residential proxies via `SCRAPER_PROXY`. The card parser
is covered by `tests/test_browser_parse.py` (runs without a browser).

## Scheduling

**Cron:** see `cron/dealer-scraper.cron`.

**systemd timer** (preferred on modern hosts):

```ini
# /etc/systemd/system/dealer-scraper.service
[Service]
Type=oneshot
WorkingDirectory=/opt/dealer-scraper
ExecStart=/opt/dealer-scraper/.venv/bin/python -m dealer_scraper.run_daily
EnvironmentFile=/opt/dealer-scraper/.env
```
```ini
# /etc/systemd/system/dealer-scraper.timer
[Timer]
OnCalendar=*-*-* 03:15:00
Persistent=true
[Install]
WantedBy=timers.target
```
```bash
sudo systemctl enable --now dealer-scraper.timer
```

## Data model highlights (aligned to the SOW)

- **Dealers** (SOW §1): onboarding fields (address/city/province/postal/phone),
  `active` flag, denormalized `last_synced_at` + `inventory_count`.
- **Dedup:** `UNIQUE (dealer_id, external_id)`; VIN stored for cross-dealer matching.
- **Vehicle fields** (SOW §2): VIN, stock #, year/make/model/trim, mileage,
  condition, colors, transmission, fuel, drivetrain, price, detail URL.
- **Change detection:** `content_hash` — unchanged rows only bump `last_seen_at`.
- **Price history:** a `car_price_history` row is appended whenever price changes.
- **Lifecycle** (SOW §4): `active -> removed -> sold`. Removal sets `removed_at`;
  a car removed longer than `SOLD_AFTER_DAYS` (default 7) is auto-marked `sold`
  with `sold_at`. Set `SOLD_AFTER_DAYS=` empty to only mark sold from confirmed
  dealer data. Reappeared cars flip back to `active`.
- **Audit trail** (SOW §5): `vehicle_audit_logs` — append-only created/updated/
  price_changed/removed/sold/reactivated events per vehicle.
- **Daily snapshots** (SOW §6/§7): `daily_inventory_snapshots` — one row per
  dealer per day (totals, added/removed/sold, sync status) powering the dashboard.
- **`scrape_runs`:** one row per dealer per run (counts, status) — powers the
  safety gate and monitoring.

> Not yet built (SOW UI layer): the admin onboarding screens and the summary
> dashboard are front-end deliverables on top of this engine + schema. See the
> SOW review document for the phased plan.

## Tests

```bash
DATABASE_URL="sqlite:////tmp/smoke.db" python -m tests.smoke_test
```
Simulates three consecutive days and asserts dedup, removal marking (with
`removed_at`), reappearance, and price history. (Uses SQLite for convenience;
production runs on PostgreSQL, where JSON columns are JSONB and PKs are BIGSERIAL.)

## Production notes

- Point `DATABASE_URL` at RDS PostgreSQL; set `SCRAPER_PROXY` to your rotating
  proxy. Redis (ElastiCache) is for when you move fan-out to Celery.
- `run_daily.py` scrapes dealers sequentially — fine within a 24h window. To
  parallelize, wrap `run_dealer` in a Celery task per dealer.
- Respect each site's Terms of Service and robots.txt; keep `delay_seconds`
  polite. Confirm the legal posture with the client (see implementation plan).
```
