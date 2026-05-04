# Project notes for Claude Code

**Sandlot V1 (the web app) is the live product.** All active development happens here: `sandlot_*.py` (FastAPI) + `web/sandlot/*` (React via in-browser Babel), deployed on Railway. See `docs/sandlot-railway-v1.md`.

The repo also contains an **older local CLI** (`audit.py`, `league_intel.py`, `claude_analyzer.py`, `decision_engine.py`, `research_layer.py`) that emails a daily/weekly report. **Not actively used.** Treat it as a scrape utility: when you need to inspect live Fantrax data during a session, run `python audit.py` to refresh `.data/snapshot-YYYY-MM-DD.json`. Don't spend time changing the CLI scripts unless asked.

Anything prefixed `sandlot_` belongs to the web app. `mlb_stats.py` and `player_service.py` are also web-app modules. Shared scrape modules (`auth.py`, `fantrax_data.py`) are used by both.

## Commands

```bash
.venv/bin/python -c "..."                                       # venv is not auto-activated
.venv/bin/uvicorn sandlot_api:app --reload --port 8000          # local API (most routes 503 without DATABASE_URL)
python audit.py                                                 # daily CLI (Selenium-prompts on first run)
```

There is **no test suite** — no `tests/`, no `conftest.py`, no pytest in `requirements.txt`. Don't run `pytest`.

## Required env (`.env`)

- `FANTRAX_USER` / `FANTRAX_PASS` / `FANTRAX_LEAGUE_ID` / `FANTRAX_TEAM_ID` — for the scrape
- `OPENROUTER_API_KEY` — Skipper chat (Kimi primary, Tencent fallback)
- `DATABASE_URL` — only set on Railway; locally most Sandlot endpoints will 503
- `SANDLOT_REFRESH_TOKEN` — Railway-only; gates `/api/refresh`
- `SANDLOT_KEEP_SNAPSHOTS` — optional, default 30
- `EMAIL_*` / `GMAIL_APP_PASSWORD` / `ANTHROPIC_API_KEY` — only the legacy CLI uses these; ignore unless explicitly working on `audit.py` / `league_intel.py`

## Sandlot frontend (`web/sandlot/`)

- **No bundler, no `npm install`.** JSX runs through `@babel/standalone` from CDN, configured via `<script type="text/babel" data-presets="env,react">` tags in `index.html`. Edit a `.jsx` file and refresh.
- **No `import`/`export` anywhere.** Babel's `env,react` presets don't transform module syntax — using it silently breaks the in-browser pipeline. Stick to top-level `function` / `const` declarations.
- **Inter-file refs go through `window.*`.** Each file ends with `Object.assign(window, { Foo, Bar, ... })`. When you add a shared symbol, add it to that block or later files won't see it. Script load order in `index.html` matters: `atoms.jsx` → `data.jsx` → `data2.jsx` → `v2-pages.jsx`.
- Files: `atoms.jsx` (tokens, `Sparkline`, `Avatar`, `Icons`), `data.jsx`/`data2.jsx` (mock fallback for when `DATABASE_URL` is unset), `v2-pages.jsx` (every page + the app shell `V2App`).
- **Three navigation states in `V2App`:** `page` (active tab), `detail` (bottom-sheet via `V2PlayerSheet` — quick preview, opened from roster row tap), `pushed` (full overlay via `V2PlayerProfile` — fetches `/api/player/{id}`, opened from Skipper chat link). They are not the same component; don't conflate them.
- **No `localStorage`.** Don't reach for it for new state.
- **Validating `.jsx` edits**: `node --check` doesn't understand JSX, so the user's post-edit hook errors out (harmless). To actually validate JSX, use Babel:
  ```bash
  node -e "require('/tmp/node_modules/@babel/parser').parse(require('fs').readFileSync('web/sandlot/v2-pages.jsx','utf8'),{sourceType:'module',plugins:['jsx']})"
  ```

## Sandlot backend (`sandlot_*.py`)

- `startup` only calls `init_schema()` when `DATABASE_URL` is set. Locally, `_snapshot_payload()` and other handlers will 503 (`/api/snapshot/latest`, `/api/skipper/messages`, `/api/player/{id}`).
- `/api/health` is the **only no-DB-friendly probe** — it catches DB errors and returns 200 with `ok: false`. Don't refactor it to behave like the others.
- Snapshots are stored as JSONB in `snapshots.data`. The frontend reads from `_snapshot_payload()` in `sandlot_api.py`, which derives a flat shape (roster rows, standings, player_index) from the raw blob.
- Skipper chat: primary `moonshotai/kimi-k2`, fallback `tencent/hy3-preview:free`. Streams via SSE through `sandlot_skipper.SkipperClient`. Fallback only triggers on **pre-stream** errors / empty stream — mid-stream cutoffs are not retried in V1.
- Single-user app — Sandlot routes are mostly unauthenticated by design. `/api/refresh` is the only one with an optional `SANDLOT_REFRESH_TOKEN` guard.

## Deploy

- Pushing to `main` triggers Railway auto-deploy. `Procfile` defines `web` (uvicorn) and `cron` (one-shot `sandlot_cron.py`).
- **Confirm with the user before pushing** (per `~/.claude/CLAUDE.md`).
