# Running the Dealership Inventory Hub locally

From a fresh clone to a working UI. Every command here was run end-to-end against
an empty database on macOS; where something commonly goes wrong, the symptom is
listed with it.

**Time:** ~10 minutes, most of it `pip install` and `playwright install`.

---

## 0. Prerequisites

| | Version | Check |
|---|---|---|
| Python | 3.11+ | `python3 --version` |
| Node | 18+ | `node --version` |
| Docker Desktop | any recent | `docker info` (must not error) |

Docker Desktop has to be **running**, not just installed — `docker compose up`
fails with "Cannot connect to the Docker daemon" otherwise.

---

## 1. Python environment

```bash
cd scrapping-cars
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
playwright install chromium
```

`requirements-dev.txt` is what provides `pytest`. Without it `pytest` is simply
not found, even though CI uses it.

`playwright install chromium` downloads ~150 MB. Discovery and browser-mode
scrapes fail at runtime without it, and `/health/ready` reports
`"chromium": false`.

> Re-run `pip install -r requirements.txt` after every `git pull`. Dependencies
> have been added repeatedly, and a stale venv shows up as a confusing
> `ModuleNotFoundError` deep inside a background job.

---

## 2. Node dependencies

Two separate installs — the root one is only for the dev-server runner:

```bash
npm install
npm install --prefix frontend
```

---

## 3. Configuration

```bash
cp .env.example .env
cp frontend/.env.example frontend/.env
```

Then edit both. **Exactly one thing matters to get started:**

```
.env            API_KEY=some-local-value
frontend/.env   VITE_API_KEY=some-local-value      <- must be the SAME value
```

Auth is on by default and fails closed. If the two differ, or the frontend one is
blank, **every request returns 401** and the UI looks completely broken.

Defaults for everything else are fine locally. `.env` is gitignored; never commit
it.

---

## 4. Start the databases

```bash
docker compose up -d
```

Brings up Postgres on host port **5433** (not 5432) and Redis on **6379**.

> **Postgres:** the container deliberately uses 5433 because a locally-installed
> Postgres usually owns 5432 and silently wins the connection. Symptom:
> `role "scraper" does not exist`. Check `.env` says `localhost:5433`.
>
> **Redis:** the container publishes 6379, but a Homebrew `redis-server` on
> 127.0.0.1:6379 **wins the bind**, so the app talks to the local Redis while
> `docker compose exec redis redis-cli` inspects the (empty) container. Nothing
> errors — the inspection just silently lies to you. Check with
> `lsof -i:6379 -sTCP:LISTEN`; run `brew services stop redis` if you want the
> container to win.

Wait for Postgres to accept connections before the next step:

```bash
until docker compose exec -T postgres pg_isready -U scraper -d dealer_inventory; do sleep 1; done
```

---

## 5. Create the schema

```bash
alembic upgrade head
```

Replays all 8 migrations from empty and ends at `c3d4e5f6a7b8 (head)`. Verify with
`alembic current`.

> Do **not** run `alembic revision --autogenerate` to create the initial schema —
> the migrations are already committed.
>
> This works on Postgres only. Migration `c2d3e4f5a6b7` uses `DISTINCT ON` and
> `EXTRACT(EPOCH`, so the chain cannot replay from scratch on SQLite. Tests are
> unaffected: they use `create_all`.

---

## 6. Run it

```bash
npm run dev
```

Starts the API on **:8000** and the UI on **:5173** together. The API runner finds
`.venv` on its own, so you don't need the venv activated in that shell.

- UI — <http://localhost:5173>
- API docs — <http://localhost:8000/docs>

Variant: `npm run dev:all` also starts a Celery worker. You don't need it —
without a worker, syncs and discovery run on an in-process thread pool. See
§9 for when it matters.

### Confirm it's healthy

```bash
curl -s http://localhost:8000/health
curl -s -H "X-API-Key: $(grep -E '^API_KEY' .env | cut -d= -f2)" http://localhost:8000/health/ready
```

Expected:

```json
{"status":"ok"}
{"status":"ok","checks":{"database":true,"redis":true,"worker":false,"chromium":true}}
```

`"worker": false` is normal without `dev:all`. Anything else:

| Check false | Cause |
|---|---|
| `database` | containers not up, or `.env` points at 5432 instead of 5433 |
| `redis` | containers not up |
| `chromium` | `playwright install chromium` not run |

---

## 7. Get some data on screen

The UI is empty on a fresh database. Fastest way to see it populated, with no
network access and no scraping:

```bash
source .venv/bin/activate
python -m dealer_scraper.sample_data
```

Creates three stub dealers (~14 vehicles, one inactive) that exercise the whole
Create → Sync → Inventory flow. `--reset` wipes and re-seeds them.

To scrape a real site instead, see §8.

---

## 8. Adding a real dealership

**From the UI:** Dealers → Add Dealer → paste the URL → **Detect settings**.
Discovery opens the site, finds the listing, works out pagination, and fills in
the config. You'll see live phase progress.

A dealer whose site discovery cannot read is refused with a 409 and a written
reason — that's deliberate, so a dealership can't be onboarded into a permanent
zero-inventory state. `force=true` overrides it.

**From the CLI**, which is easier to debug and can be interrupted:

```bash
source .venv/bin/activate
python -m dealer_scraper.discovery --show-config https://example-dealer.ca
python -m dealer_scraper.discovery --file sites.txt        # batch
python -m dealer_scraper.discovery --headed https://...    # see the browser
```

`--headed` matters for sites behind AWS WAF (clutch.ca): headless Chromium gets
served an error page instead of the inventory. **The UI always runs headless**, so
a WAF'd site has to be done from the CLI.

To load the known-good clutch.ca dealer and scrape it:

```bash
python -m dealer_scraper.seed
python -m dealer_scraper.run_daily
```

Expect ~3,100 vehicles in ~11 minutes, with a visible Chromium window.

---

## 9. Tests

```bash
source .venv/bin/activate
DATABASE_URL="sqlite:////tmp/t.db" python -m pytest tests -q
```

243 passing, 9 skipped. The skips need a real browser or network; add `--live` to
include them. Tests never touch your Postgres — they use a throwaway SQLite file,
and `require_disposable_db()` refuses to run destructive suites against anything
else.

Frontend:

```bash
cd frontend && npx tsc -b --noEmit && npm run lint && npm run build
```

---

## 10. Common problems

| Symptom | Cause and fix |
|---|---|
| Every request 401s; UI looks broken | `API_KEY` and `VITE_API_KEY` differ, or the frontend one is blank. They must match exactly. Restart Vite after editing — `.env` is read at startup. |
| `command not found: alembic`, `No module named sqlalchemy` | venv not active. `source .venv/bin/activate` in **every** new terminal. |
| `role "scraper" does not exist` | A local Postgres owns 5432. The container is on **5433** — check `.env`. |
| `Cannot connect to the Docker daemon` | Docker Desktop isn't running. |
| `pytest: command not found` | `pip install -r requirements-dev.txt`. |
| `/health/ready` says `chromium: false` | `playwright install chromium`. |
| Detect settings spins on "Detecting…" forever | A per-host lock is held, usually left behind by a run that was killed. It self-clears within 15 minutes; to clear it now: `python -c "from worker.tasks import _redis_client; _redis_client().delete('scrape_host:example.ca')"` |
| Discovery returns 0 vehicles on a site that clearly has them | Often anti-bot. Retry with `--headed`. A `scrape_debug.png` is saved on failure — look at it. |
| Ports 8000/5173 already in use | `pkill -f "run-api.mjs"; pkill -f uvicorn; pkill -f vite` |
| `git commit` fails on `.git/index.lock` | `rm -f .git/index.lock`, then retry. |

---

## 11. Resetting

Stop the servers, keep the data:

```bash
docker compose stop
```

Wipe everything and start over — **deletes all local dealers, vehicles and
history**:

```bash
docker compose down -v
docker compose up -d
alembic upgrade head
```

Clear leftover host locks (they block discovery for a host until they expire):

```bash
python -c "from worker.tasks import _redis_client; c=_redis_client(); [c.delete(k) for k in c.keys('scrape_host:*')]"
```

---

## Where to go next

- `CLAUDE.md` — decisions, the two rules the system is built on, and the gotchas
  that cost real time
- `HANDOFF/01_ARCHITECTURE.md` — how the pieces fit together
- `HANDOFF/04_API_SPEC.md` — REST contract
- `HANDOFF/10_DISCOVERY_AND_ONBOARDING_FIXES.md` — current state and open items
