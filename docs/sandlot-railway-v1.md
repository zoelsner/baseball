# Sandlot V1 Railway Setup

V1 runs all-in on Railway:

- `web`: FastAPI app serving `/api/*` plus `web/sandlot/index.html`
- `cron`: one-shot daily scraper using the same refresh runner as the API
- Postgres: source of truth for raw Fantrax snapshots, stored cookies, chat history, player stat cache, and cached player-card takes

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
source .venv/bin/activate
uvicorn sandlot_api:app --reload --port 8000
```

Open `http://127.0.0.1:8000/`.

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
runs `python sandlot_cron.py` on the desired daily schedule.

## API

- `GET /api/health`
- `GET /api/snapshot/latest`
- `POST /api/refresh`
- `GET /api/player/{fantrax_id}`
- `POST /api/player/{fantrax_id}/refresh`
- `GET /api/skipper/messages`
- `POST /api/skipper/messages`
- `DELETE /api/skipper/messages`

Player detail sheets are read-only. `GET /api/player/{fantrax_id}` uses the
latest Fantrax snapshot, lazily resolves an MLB player id, caches game logs in
`player_game_logs`, and lazily generates a roster-aware Skipper take in
`player_takes`. The `/refresh` variant refreshes MLB stat data; Skipper takes
remain keyed to the latest Fantrax snapshot and are reused until a new snapshot
is stored.

The app only stores and displays data. It does not make roster moves, drops,
claims, or trade actions in Fantrax.
