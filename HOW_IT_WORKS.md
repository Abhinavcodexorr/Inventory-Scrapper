# How the Inventory Hub Works

A plain-English guide to the daily flow, the schedule, and what each job status
means.

---

## What the system does

Every day it visits each dealership website you have added, reads the vehicles
currently listed for sale, and updates a central database. It then compares
today's list against yesterday's to work out what is new, what has gone, and
what has changed price.

Nothing is ever deleted. A vehicle that disappears from a dealer's website is
marked *removed* and kept, so the history stays intact for reporting.

---

## The daily flow

1. **The scheduler wakes up** at the configured time and creates one job per
   active dealership.
2. **Jobs are queued**, not run all at once. Dealerships on the same website are
   run one after another so no site receives simultaneous requests.
3. **For each dealership**, the system opens the inventory page in a real
   browser, moves through every page of results, and reads each vehicle listing.
4. **Each vehicle is matched** against what is already stored — first by VIN,
   then by listing URL, then by stock number. Matching means a re-run updates
   existing records rather than creating duplicates.
5. **The comparison runs**: vehicles seen today stay active, new ones are added,
   and ones no longer listed are marked removed with the date.
6. **A daily snapshot is written** — total active, added, removed, sold — which
   is what the dashboard charts.

---

## The schedule

| | |
|---|---|
| **When** | 03:15, Toronto time |
| **How often** | Once per day |
| **Spacing** | Dealerships start 30 seconds apart, so sites aren't hit together |
| **Configurable** | Yes — time, timezone and spacing are settings, not code |

Off-peak is deliberate: it is polite to the dealer websites and means the
overnight run is finished before anyone looks at the dashboard in the morning.

**"Sync now"** in the admin panel runs a single dealership immediately, without
waiting for the schedule. Useful after adding a dealership or changing settings.

---

## Job statuses

Every run records a status. The system will never report a run as successful if
it did not actually collect the inventory.

### Completed

The run finished and collected what it expected to. Comparison ran, and removals
were applied normally.

### Incomplete

The run worked but collected noticeably less than the dealer's website says it
has — for example 160 vehicles when the site reports 3,000.

**Importantly, the system does not mark the missing vehicles as removed.** If it
did, a half-finished run would wipe most of a dealership's inventory and then
report those cars as sold a week later. Instead it stops, keeps the existing
inventory untouched, and records why.

Common causes: the run was deliberately limited during testing, the site was
slow, or the connection dropped part-way.

### Failed

The run could not read the inventory at all. The reason is always recorded in
plain text — for example the website returned an error page, or blocked the
request. Inventory is left exactly as it was.

---

## Do failed or incomplete jobs run again automatically?

**No — not on their own, and this is deliberate.**

- Within a single run, the system already retries a page **three times** with
  increasing delays before giving up. Most temporary glitches are absorbed here
  and never become a failed job.
- If the run still fails, it is **not** re-queued immediately. Repeatedly
  hammering a site that just refused us is the fastest way to get blocked
  properly.
- The dealership is picked up again at the **next daily run**, as normal.
- If you don't want to wait, press **Sync now** on that dealership.

**A re-run always starts from the beginning**, not from where it stopped. The
inventory comparison needs a complete picture of what is listed *right now* —
stitching together half of today's list with half of yesterday's would produce a
state that never actually existed. Re-running is safe: matching means vehicles
are updated, never duplicated.

---

## What you see in the admin panel

- **Dashboard** — totals for today and yesterday, added, removed and sold, plus
  which dealerships failed to sync.
- **Dealerships** — each site with its inventory count, last sync time and
  status. This is where you add a dealership or trigger a manual sync.
- **Scraping jobs** — every run with its duration, how many vehicles were
  collected, and the reason for anything that did not complete.
- **Inventory** — every vehicle, searchable and filterable, including ones now
  removed or sold.

---

## The vehicle lifecycle

```
   first seen          still listed          no longer listed        still gone
      │                     │                       │               after 7 days
      ▼                     ▼                       ▼                    ▼
    ACTIVE  ──────────►  ACTIVE  ──────────►     REMOVED  ──────────►   SOLD
                     (price changes                                 
                      recorded)              (returns to ACTIVE if it comes back)
```

**A note on "sold":** scraping a public website cannot observe an actual sale.
What it can observe is a vehicle disappearing from the listing and staying gone.
After 7 days absent, it is marked sold — a sensible inference, not a confirmed
fact. The threshold is configurable.

---

## Why some websites need extra setup

Most dealership sites can be read directly. Some run bot-protection that refuses
requests coming from data-centre servers — the page loads, but the vehicle list
is replaced with an error. This is a deliberate restriction on their side and
affects any automated tool.

Those sites are read through a residential connection instead, which is a
metered service billed by data used. The system supports this through
configuration; no code changes are needed per site.

When a site cannot be read at all, it is **refused at the point you add it**,
with the reason shown — rather than being accepted and then sitting at zero
vehicles forever.
