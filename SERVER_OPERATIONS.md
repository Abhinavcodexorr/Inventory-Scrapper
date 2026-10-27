# Server Operations

Day-to-day running of the deployed platform: what runs, how to restart it, and
what to check after a reboot.

Application directory: `/opt/vehicle-platform`

---

## What runs

| Component | Managed by | Auto-starts on reboot |
|---|---|---|
| API (FastAPI/uvicorn, port 8000) | `systemd` — `scraper-api` | Yes, once enabled |
| Worker (Celery, runs the scrapes) | `systemd` — `scraper-worker` | Yes, once enabled |
| Scheduler (Celery Beat, nightly sync) | `systemd` — `scraper-beat` | Yes, once enabled |
| PostgreSQL | Docker — `vehicle-platform-postgres-1` | Yes, with `restart: unless-stopped` |
| Redis | Docker — `vehicle-platform-redis-1` | Yes, with `restart: unless-stopped` |

The API listens on `127.0.0.1` only and is reached through nginx. Postgres and
Redis are bound to `127.0.0.1` and are not reachable from the internet.

---

## Enabling auto-start

`systemctl enable` is what makes a service survive a reboot; `start` alone does
not. Run once per service:

```bash
systemctl enable scraper-api scraper-worker scraper-beat
```

Confirm — every line must read `enabled`:

```bash
systemctl is-enabled scraper-api scraper-worker scraper-beat
```

Docker containers are separate: they restart only if the compose file sets a
restart policy. Confirm with:

```bash
docker inspect -f '{{.Name}} {{.HostConfig.RestartPolicy.Name}}' \
  vehicle-platform-postgres-1 vehicle-platform-redis-1
```

Anything other than `unless-stopped` means the database will not come back
after a reboot. Fix by re-creating them from the compose file:

```bash
cd /opt/vehicle-platform && docker compose up -d
```

---

## Restarting

```bash
systemctl restart scraper-api        # after any .env change
systemctl restart scraper-worker     # after a code deploy
systemctl restart scraper-beat

systemctl restart scraper-api scraper-worker scraper-beat   # all three
```

**Configuration is read once at startup.** Editing `.env` has no effect until
the API is restarted — a changed API key or CORS origin that "didn't work" is
almost always a missed restart.

Order matters when restarting everything: Docker containers first, then the
services that depend on them.

```bash
cd /opt/vehicle-platform && docker compose up -d
sleep 10
systemctl restart scraper-api scraper-worker scraper-beat
```

---

## After a reboot or resize — checklist

```bash
free -h                       # expect ~4 GB
systemctl is-active scraper-api scraper-worker scraper-beat
docker ps --format "{{.Names}}\t{{.Status}}"
curl -s -H "X-API-Key: $(grep ^API_KEY= /opt/vehicle-platform/.env | cut -d= -f2)" \
  localhost:8000/health/ready
```

`/health/ready` is the single most useful check. It reports each dependency
separately:

```json
{"status":"ok","checks":{"database":true,"redis":true,"worker":true,"chromium":true}}
```

- `database: false` — Postgres container is down
- `redis: false` — Redis container is down
- `worker: false` — Celery is not running; scrapes will not process
- `chromium: false` — browser not installed; re-run `playwright install --with-deps chromium`

---

## Admin logins

Operators sign in with email and password. Accounts live in the database, not
in `.env` — this server rewrites `.env` from the template on every deploy, so
a password kept there would stop working after each release.

```bash
cd /opt/vehicle-platform && source .venv/bin/activate

python -m api.manage_user create you@example.com   # prompts, never echoes
python -m api.manage_user list
python -m api.manage_user passwd you@example.com
python -m api.manage_user disable someone@example.com
```

Create the first account after deploying, or nobody can sign in.

Sessions last 12 hours (`SESSION_MAX_AGE_HOURS`) and are held in an httpOnly
cookie the browser's JavaScript cannot read. Changing someone's password does
not end their current session; `disable` does, immediately.

**Once the site has HTTPS, set `SESSION_COOKIE_SECURE=true`.** Until then leave
it false — with it on over plain HTTP the browser silently discards the cookie
and login appears to do nothing at all.

`X-API-Key` still works for machines — health checks, cron, the commands in
this document. It is no longer used by the browser, so the admin bundle no
longer contains a copy of it.

## Logs

```bash
journalctl -u scraper-api -n 50 --no-pager
journalctl -u scraper-worker -f          # live, during a scrape
journalctl -u scraper-beat -n 50 --no-pager
docker logs vehicle-platform-postgres-1 --tail 50
```

---

## Checking a sync

```bash
docker exec vehicle-platform-postgres-1 psql -U scraper -d dealer_inventory -c \
  "select d.name, r.status, r.scraped_count, r.finished_at, r.error
     from scrape_runs r join dealers d on d.id = r.dealer_id
    order by r.id desc limit 10;"
```

Every run should be `completed`. A `failed` or `incomplete` row always carries a
written reason in `error` — the system never reports a run that collected
nothing as a success.

---

## Common problems

| Symptom | Cause and fix |
|---|---|
| Can't sign in, no error shown | Cookie rejected. Almost always `SESSION_COOKIE_SECURE=true` on a site still served over HTTP |
| "Incorrect email or password" but you're sure | Check the account exists and is active: `python -m api.manage_user list` |
| Signed out constantly | `API_KEY` changed and `SESSION_SECRET` is unset — sessions are signed with a key derived from `API_KEY`. Set `SESSION_SECRET` explicitly to decouple them |
| Every request returns 503 | Neither `API_KEY` nor `SESSION_SECRET` is set in `.env` |
| Browser calls blocked by CORS | The site's address is missing from `CORS_ALLOW_ORIGINS`. Add it, restart the API |
| Sync does nothing, no error | Worker not running: `systemctl status scraper-worker` |
| Sync stuck on "Detecting…" | Stale lock: `docker exec vehicle-platform-redis-1 redis-cli --scan --pattern "scrape_host:*"` then `del` the key |
| Scrape ends with 0 vehicles on a protected site | `xvfb-run` missing from the worker unit, or the site blocks data-centre IPs (needs `SCRAPER_PROXY`) |
| Worker killed mid-scrape | Server below 4 GB, or `CELERY_WORKER_CONCURRENCY` above 2 |
| Database gone after reboot | Container had no restart policy: `docker compose up -d` |

---

## Corrections to the original installation steps

Found while deploying; the installation guide has been updated to match.

1. **Node 20.19+ or 22.12+ is required** — the frontend build tool refuses to
   run on Node 18.
2. **`xvfb` must be installed** (`apt-get install -y xvfb`) and the worker must
   start via `xvfb-run`. Without it, protected sites return an error page and
   syncs finish with zero vehicles.
3. **Install the browser with `playwright install --with-deps chromium`.**
   Without `--with-deps` the system libraries are missing and the browser will
   not start.
4. **Copying `.env.example` to `.env` is not sufficient.** `API_KEY` is empty in
   the template and `API_AUTH_REQUIRED` defaults to true, so every request
   returns 503 until a key is set. Generate one with `openssl rand -hex 32` and
   put the same value in `frontend/.env` as `VITE_API_KEY`.
5. **`CORS_ALLOW_ORIGINS` must list the address the admin UI is served from**,
   or the browser blocks every API call.
6. **Run services under `systemd`, not `nohup`** — a backgrounded process dies
   with the terminal that started it.
7. **Minimum server size is 2 vCPU / 4 GB.** Scraping runs a real browser at
   roughly 700 MB per concurrent site.
