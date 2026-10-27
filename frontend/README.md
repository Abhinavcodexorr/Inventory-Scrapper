# Dealership Inventory Hub — Frontend

React admin UI for the multi-dealer inventory scraper.

## Stack

- Vite + React 19 + TypeScript
- Material UI + Data Grid + Charts
- React Router + TanStack Query + Axios

## Setup

From the **repo root** (preferred):

```bash
npm install                 # once — installs concurrently
cd frontend && npm install && cd ..
# ensure .venv exists and frontend/.env is set
npm run dev                 # API :8000 + UI :5173 together
```

Or individually:

| Command | What it starts |
|---|---|
| `npm run api` | FastAPI on http://localhost:8000 |
| `npm run ui` | Vite on http://localhost:5173 |
| `npm run dev` | Both at once |

From `frontend/` alone: `npm run dev` (UI only).
## Routes

| Path | Screen |
|---|---|
| `/` | Dashboard |
| `/dealers` | Dealership list |
| `/dealers/new` | Add dealership |
| `/dealers/:id` | Dealership detail |
| `/dealers/:id/edit` | Edit dealership |
| `/vehicles` | Inventory browser |
| `/vehicles/:id` | Vehicle detail |

See `../HANDOFF/05_FRONTEND_SPEC.md` for acceptance criteria.
