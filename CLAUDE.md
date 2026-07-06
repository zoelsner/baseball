# Project notes for Claude Code

**Sandlot V1 (the web app) is the live product.** All active development happens here: `sandlot_*.py` (FastAPI) + `web/sandlot/*` (React via in-browser Babel), deployed on Railway. See `docs/sandlot-railway-v1.md`.

**Picking up ongoing work? Read `STATUS.md` (repo root) first** — it holds the current state, the ordered next steps, and the standing safety rules for the actions executor. Update it whenever the plan changes.

The repo also contains an **older local CLI** (`audit.py`, `league_intel.py`, `claude_analyzer.py`, `decision_engine.py`, `research_layer.py`) that emails a daily/weekly report. **Not actively used.** Treat it as a scrape utility: when you need to inspect live Fantrax data during a session, run `python audit.py` to refresh `.data/snapshot-YYYY-MM-DD.json`. Don't spend time changing the CLI scripts unless asked.

Anything prefixed `sandlot_` belongs to the web app. `mlb_stats.py` and `player_service.py` are also web-app modules. Shared scrape modules (`auth.py`, `fantrax_data.py`) are used by both.

## Commands

```bash
.venv/bin/python -c "..."                                       # venv is not auto-activated
npm install && npm run build:sandlot                            # rebuild web/sandlot/app.js after JSX edits
.venv/bin/uvicorn sandlot_api:app --reload --port 8000          # local API (most routes 503 without DATABASE_URL)
python audit.py                                                 # daily CLI (Selenium-prompts on first run)
```

**Tests live in two places:** `tests/test_*.py` (Python `unittest` — run with `.venv/bin/python -m unittest discover -s tests -p "test_*.py"`) and `tests/playwright/` (Playwright + TypeScript E2E — run with `npx playwright test` from that directory; targets the deployed Railway URL by default via `SANDLOT_URL`). The repo doesn't use `pytest` — don't reach for `conftest.py` or pytest fixtures. See `tests/playwright/README.md` for E2E details.

## Required env (`.env`)

- `FANTRAX_USER` / `FANTRAX_PASS` / `FANTRAX_LEAGUE_ID` / `FANTRAX_TEAM_ID` — for the scrape
- `OPENROUTER_API_KEY` — Skipper chat (DeepSeek V4 Flash primary, Kimi fallback)
- `DATABASE_URL` — only set on Railway; locally most Sandlot endpoints will 503
- `SANDLOT_REFRESH_TOKEN` — Railway-only; gates `/api/refresh`
- `SANDLOT_KEEP_SNAPSHOTS` — optional, default 30
- `EMAIL_*` / `GMAIL_APP_PASSWORD` / `ANTHROPIC_API_KEY` — only the legacy CLI uses these; ignore unless explicitly working on `audit.py` / `league_intel.py`

## Sandlot frontend (`web/sandlot/`)

- **Frontend build required.** Source lives in `web/sandlot/*.jsx` and is bundled with esbuild to `web/sandlot/app.js`. Run `npm run build:sandlot` after JSX edits and commit the regenerated bundle.
- **ES modules are allowed.** Use normal `import`/`export`; do not add globals through `window.*`.
- Files: `main.jsx` (entry/render), `atoms.jsx` (tokens, `Sparkline`, `Avatar`, `Icons`), and `v2-pages.jsx` (every page + the app shell `V2App`).
- The production app is API-backed only. Do not add mock globals or `file://` demo fallbacks to `index.html`; no-data states should be explicit API loading/error states.
- **Two navigation states in `V2App`:** `page` (active tab from the bottom tab bar) and `detail` (player id → renders `V2PlayerSheet`, the bottom sheet that itself fetches `/api/player/{id}`). The sheet is opened by any roster row tap and dismissed via its `aria-label="Close"` button or backdrop click — Escape is not wired up. A third state for a full-overlay player profile (opened from Skipper chat links) is planned but not yet built — see #37.
- **No `localStorage`.** Don't reach for it for new state.
- **Validating `.jsx` edits**: build the frontend bundle:
  ```bash
  npm run build:sandlot
  ```

## Sandlot backend (`sandlot_*.py`)

- `startup` only calls `init_schema()` when `DATABASE_URL` is set. Locally, `_snapshot_payload()` and other handlers will 503 (`/api/snapshot/latest`, `/api/skipper/messages`, `/api/player/{id}`).
- `/api/health` is the **only no-DB-friendly probe** — it catches DB errors and returns 200 with `ok: false`. Don't refactor it to behave like the others.
- Snapshots are stored as JSONB in `snapshots.data`. The frontend reads from `_snapshot_payload()` in `sandlot_api.py`, which derives a flat shape (roster rows, standings, player_index) from the raw blob.
- Refresh architecture: Railway cron runs `python sandlot_cron.py` at `0 13,21 * * *` UTC during baseball season (9 AM + 5 PM ET while EDT is active). Manual refresh remains available through `/api/refresh`. The frontend must not auto-refresh on page load. Refresh work should stay deterministic: scrape Fantrax, store one snapshot, compute Python projections/recommendations, and leave AI as a cached explanation layer.
- Snapshot freshness in `sandlot_api._freshness()` matches that twice-daily cadence: `fresh` for 18 hours, `stale` until 36 hours, then `old`.
- Skipper chat: primary `deepseek/deepseek-v4-flash`, fallback `moonshotai/kimi-k2`. `z-ai/glm-5.2` is available as a selectable deeper-analysis option. Streams via SSE through `sandlot_skipper.SkipperClient`. Fallback triggers on **any** exception during streaming (pre-stream, mid-stream, or empty stream) — the `for model in model_order: try ... except: continue` loop in `SkipperClient.stream` retries the next model on any failure. The SSE client may already have received partial tokens from the failed model when the retry kicks in; downstream consumers should treat the stream as best-effort, not transactional.
- Single-user app — Sandlot routes are mostly unauthenticated by design. `/api/refresh` is the only one with an optional `SANDLOT_REFRESH_TOKEN` guard.
- **Cached-AI pattern** (`ai_briefs` table): deterministic compute → AI overlay → cache by `(snapshot_id, brief_type, subject_key)` with `input_hash` for staleness. `sandlot_waivers.py` and `sandlot_trades.py` are reference. Use `sandlot_db.{get,set}_ai_brief` for new cached-AI features.
- **Model order helpers** in `sandlot_skipper`: `default_model_order()` = primary-first, currently DeepSeek V4 Flash then Kimi. For a one-off path that needs a different order, pass an explicit `model_order`.
- **Snapshot blob shape**: `data["roster"]["rows"]` (mine), `data["all_team_rosters"]` (`{team_id: {rows, is_me, ...}}`), `data["free_agents"]["players"]`, `data["standings"]`. `_player_index()` flattens all three with a `source` field.
- **`game_scores` table**: normalized per-game league-scored history (one row per player/game/stat-group, exact league points). `sandlot_scores.sync_latest()` maintains it from the cron after each refresh (kill-switch `SANDLOT_GAME_SCORES_SYNC_DISABLED=1`). Analytics should read this table (`sandlot_db.game_scores_between`) instead of re-fetching MLB game logs; fall back to `mlb_stats.fetch_game_log` only for uncovered players.

## Git workflow

- **Non-trivial work goes through PRs.** Branch `<type>/<issue#>-<slug>`, open PR, wait for CI green, merge with `--squash --delete-branch`. Direct push to `main` is fine for one-line fixes only.
- **CI** runs two workflows on every PR:
  - `.github/workflows/ci.yml`: `python-import-smoke` (imports every Sandlot module + runs `python -m unittest discover -s tests -p "test_*.py"`) and `frontend-build` (`npm ci`, `npm run build:sandlot`, and verifies `web/sandlot/app.js` is current).
  - `.github/workflows/playwright.yml`: `E2E against Railway` runs Playwright specs from `tests/playwright/` against the live Railway deploy (or `SANDLOT_URL`). Also fires on a daily 14:30 UTC cron.
  - Green CI means: modules import, the frontend bundle builds, the Python unit suite is clean, and the Playwright assertions pass against prod. It does *not* mean the new code under review is wired up correctly — read the diff, don't rubber-stamp.
- **Labels** (`gh label list`): `type:feature` / `type:bug` / `type:chore`, `area:backend` / `area:frontend`. Use both axes when filing.
- `gh` is authed as `zoelsner` for `zoelsner/baseball`. Use the `distill-issue` skill (`.claude/skills/distill-issue/SKILL.md`) for structured issues.

## Deploy

- Pushing to `main` triggers Railway auto-deploy. `Procfile` defines `web` (uvicorn) and `cron` (one-shot `sandlot_cron.py`).
- **Confirm with the user before pushing** (per `~/.claude/CLAUDE.md`).
