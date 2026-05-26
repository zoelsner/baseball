# Sandlot V1 Railway Setup

V1 runs all-in on Railway:

- `web`: FastAPI app serving `/api/*` plus `web/sandlot/index.html`
- `cron`: scheduled Railway scraper using the same refresh runner as the API
- Postgres: source of truth for raw Fantrax snapshots, stored cookies, chat history, player stat/media caches, and cached player-card takes

## Required Variables

Set these on the Railway services:

```bash
DATABASE_URL=postgresql://...
FANTRAX_LEAGUE_ID=lydahdo6mhcvnob7
FANTRAX_TEAM_ID=tuumpjsjmhcvnobp
SANDLOT_REFRESH_TOKEN=<private token>
```

Optional:

```bash
SANDLOT_KEEP_SNAPSHOTS=30
SANDLOT_PROFILE_WARM_ENABLED=0
SANDLOT_PROFILE_WARM_LIMIT=12
SANDLOT_PROFILE_WARM_PARALLELISM=3
SANDLOT_PROFILE_WARM_TAKES=0
SANDLOT_WAIVER_AI_WARM_ENABLED=0
FANTRAX_COOKIES_JSON=<json array of Fantrax cookies>
OPENROUTER_API_KEY=<openrouter key for Skipper chat and player takes>
```

## Cookie Bootstrap

Railway should not open Selenium for normal refreshes. Seed the cookie table
from a local logged-in run:

```bash
source .venv/bin/activate
python audit.py
DATABASE_URL=<railway postgres url> python sandlot_bootstrap_cookies.py
```

That writes `.cookies/fantrax.json` into `fantrax_sessions` in Postgres.

## Local Run

```bash
npm install
npm run build:sandlot
source .venv/bin/activate
uvicorn sandlot_api:app --reload --port 8000
```

Open `http://127.0.0.1:8000/`.
FastAPI serves the committed `web/sandlot/app.js` bundle; rerun
`npm run build:sandlot` after editing `web/sandlot/*.jsx`.

Manual refresh:

```bash
curl -X POST http://127.0.0.1:8000/api/refresh \
  -H "x-refresh-token: $SANDLOT_REFRESH_TOKEN"
```

If no `SANDLOT_REFRESH_TOKEN` is set, local refresh is allowed without a header.
In the browser, set `localStorage.sandlot_refresh_token` if the token is set:

```js
localStorage.setItem('sandlot_refresh_token', '<private token>')
```

## Railway Commands

The `Procfile` defines:

```bash
web: uvicorn sandlot_api:app --host 0.0.0.0 --port ${PORT:-8000}
cron: python sandlot_cron.py
```

For Railway, create one web service from the repo and one cron service that
runs `python sandlot_cron.py`.

Production cron cadence:

```bash
0 13,21 * * *
```

Railway evaluates cron in UTC. During EDT baseball season this runs at 9:00 AM
ET and 5:00 PM ET: one baseline morning snapshot and one pre-lineup-lock
snapshot. If the app is used after daylight saving time ends, revisit the UTC
hours.

`sandlot_refresh.run_refresh()` uses a Postgres advisory lock, so Railway cron,
manual refresh, and any future refresh caller cannot run overlapping Fantrax
scrapes. If another refresh is already running, the later attempt records a
skipped `refresh_runs` row and returns the latest successful snapshot instead.
The web app must not auto-refresh on page load.

## API

- `GET /api/health`
- `GET /api/snapshot/latest`
- `POST /api/refresh`
- `GET /api/player/{fantrax_id}`
- `POST /api/player/{fantrax_id}/refresh`
- `GET /api/skipper/messages`
- `POST /api/skipper/messages`
- `DELETE /api/skipper/messages`

Player detail sheets are read-only. `GET /api/player/{fantrax_id}` is the fast
cache-first path: it returns the latest Fantrax snapshot row plus any cached
MLB game log, MLB media, and Skipper take from Postgres. It does not block the
page on MLB or OpenRouter calls. If the cached profile is missing or stale, the
web service schedules a best-effort background warm.

`POST /api/player/{fantrax_id}/refresh` is the explicit slow path: it resolves
the MLB id, refreshes game logs, refreshes MLB game-content media, and can
generate/cache a roster-aware Skipper take keyed to the latest Fantrax snapshot.

The Railway cron refresh runs `python sandlot_cron.py` and stores the Fantrax
snapshot. Profile and waiver AI warmups are off by default to keep refresh cheap:
set `SANDLOT_PROFILE_WARM_ENABLED=1` for MLB profile cache warming, and
`SANDLOT_WAIVER_AI_WARM_ENABLED=1` for cached waiver explanations. Set
`SANDLOT_PROFILE_WARM_TAKES=1` only if you want profile warming to spend
OpenRouter calls pre-generating takes.

Architectural boundary: refresh should produce one deterministic Fantrax
snapshot plus deterministic Python projections/recommendations. AI reads cached
snapshots and computed fields; it should not be required to compute core facts
or make refresh succeed.

The app only stores and displays data. It does not make roster moves, drops,
claims, or trade actions in Fantrax.
