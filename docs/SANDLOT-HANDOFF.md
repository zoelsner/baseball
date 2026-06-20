# Sandlot — Application Map (Handoff)

> Orientation doc for an agent picking up the Sandlot web app. Pairs with
> `CLAUDE.md` (rules + conventions) and `docs/sandlot-railway-v1.md` (deploy).
> Snapshot taken 2026-06-10 on `main` @ 44f6d81.

---

## 1. What Sandlot is

A single-user fantasy-baseball cockpit for one Fantrax league. It scrapes the
user's league (rosters, free agents, standings), stores a daily **snapshot**,
and renders a mobile-first web app over it with an AI assistant ("Skipper").
The daily question it answers: *who needs my attention today, and what move
should I make?*

There's also an **older local CLI** (`audit.py`, `league_intel.py`,
`claude_analyzer.py`, `decision_engine.py`, `research_layer.py`, `notify.py`,
`pybaseball_layer.py`) that emailed a daily report. **Not active** — treat it
only as a scrape utility (`python audit.py` refreshes `.data/snapshot-*.json`).
Everything below is the live web app.

---

## 2. Stack & how it runs

| Layer | Tech | Notes |
|---|---|---|
| API | FastAPI (`sandlot_api.py`) | `uvicorn sandlot_api:app` |
| DB | Postgres, snapshots as JSONB | `DATABASE_URL` only set on Railway; **locally most routes 503** |
| Frontend | React 18, **esbuild bundle** | `npm run build:sandlot` → committed `web/sandlot/app.js` |
| Scrape | Selenium/Playwright | `auth.py` + `fantrax_data.py` (shared with CLI) |
| AI | OpenRouter | Skipper chat + cached-AI briefs |
| Host | Railway | `Procfile`: `web` (uvicorn) + `cron` (one-shot refresh) |

**Frontend rules (changed in PR #54 — older docs may contradict this):**
- **Build required.** Source is `web/sandlot/*.jsx`, bundled with esbuild to
  `web/sandlot/app.js`. Run `npm run build:sandlot` after JSX edits and commit
  the regenerated bundle — CI fails if `app.js` is stale.
- **ES modules are allowed** (normal `import`/`export`). Do **not** add globals
  via `window.*` — that was the old in-browser-Babel pattern, now retired.
- Production is API-backed only: the mock-data fallback files were deleted
  (PR #52). No-data states are explicit loading/error states.
- **No `localStorage`** for new state.

---

## 3. Frontend map (`web/sandlot/`)

| File | Role |
|---|---|
| `main.jsx` | Entry point — renders `<V2App/>` into `#root` |
| `atoms.jsx` | Design tokens + primitives: `STATUS_LABEL`, `vsExpTier`, `Sparkline`, `Icons`, `TrendIcon`, `Avatar`, `PlayerPhoto`, `buildPlayerNameIndex` |
| `v2-pages.jsx` | **Everything else** — every page + the app shell `V2App`. ~3000 lines |
| `app.js` | Committed esbuild bundle — regenerate with `npm run build:sandlot`, never hand-edit |
| `index.html` | Loads `app.js` only |

### The app shell — `V2App` (`v2-pages.jsx:278`)

Three navigation states (they are **not** the same component):

| State | Mechanism | Status |
|---|---|---|
| `page` | active bottom-nav tab (`pages[page]` switch at `:362`) | ✅ live |
| `detail` | bottom-sheet quick preview via `V2PlayerSheet` (`:1714`) | ✅ live |
| `pushed` | full overlay via `V2PlayerProfile` | ⚠️ **described in CLAUDE.md but NOT built** — issue #37. Today `openPlayer` just falls back to roster + sheet (`:355`) |

### Current tabs (`V2TabBar`, `v2-pages.jsx:438`) — **6 tabs**

| # | id | Label | Component | What it shows |
|---|---|---|---|---|
| 1 | `today` | Today | `V2Today` (`:640`) | Roster-health view: in-lineup / bench / injured sections + a decision card |
| 2 | `roster` | Roster | `V2Roster` (`:886`) | My roster grouped by position |
| 3 | `fa` | **Adds** | `V2FreeAgents` (`:1175`) | Free agents + waiver-swap cards (`/api/waiver-swaps/latest`) |
| 4 | `skipper` | Skipper | `V2Skipper` (`:2562`) | AI chat (SSE), matchup presets, refresh brief |
| 5 | `trade` | Trade | `V2TradeGrader` (`:1608`) | Player pickers + `/api/trades/grade` |
| 6 | `league` | League | `V2League` (`:1125`) → `V2TeamRoster` (`:923`) | Standings; tap a team to see its roster (`/api/team/{id}/roster`) |

Plus a hidden `settings` page (`V2Settings`, `:1671`) — refresh token, sign-out.

Other notable components: `V2PlayerSheet` + its `V2ProfileHero/Stats/Clips`
(player bottom-sheet), `V2WaiverSwapCard`, `V2TradeGradeCard`, `V2MatchupDonut`,
`V2ChatSheet`/`V2ChatInner` (Skipper internals).

---

## 4. ⭐ The tab-reduction work (what we've been discussing)

**Status: pushed as DRAFT PRs, NOT merged. `main` still has all 6 tabs.**

The plan is to go from **6 tabs → 5** and make Today smarter:

```
CURRENT (live on main):   Today · Roster · Adds · Skipper · Trade · League
PLANNED (draft PRs):      Today · Roster · Adds · League · Skipper
                          (Today → "Attention Queue";  Trade → moved under League)
```

Tracked work:

| Issue | PR | What | State |
|---|---|---|---|
| #57 simplify bottom nav | **#61** "Move trade grading under League" | Removes `Trade` from bottom nav; adds a "Trade desk" entry at top of League. Durable tabs become Today/Roster/Adds/League/Skipper | DRAFT |
| #58 reshape Today → Attention Queue | **#62** "Reshape Today into Attention Queue" | Replaces Today's separate injury/cold/role sections with one ordered queue (status → role → low-output → matchup). **Stacked on #61.** | DRAFT |
| #59 detect player status changes between snapshots | — | Backend signal feeding the attention queue | OPEN |
| — | **#63** "Add Sandlot actions executor" | Execute roster moves (not just recommend) | DRAFT |
| — | **#60** "docs: add Sandlot product and repo guardrails" | The new `AGENTS.md`/`PRODUCT.md`/`DESIGN.md` scaffold | OPEN |

To ship the tab reduction: merge **#61 first**, then retarget/merge **#62**.

---

## 5. Backend map (`sandlot_*.py` + shared)

| File | Lines | Responsibility |
|---|---|---|
| `sandlot_api.py` | 510 | FastAPI app, all 12 routes, `_snapshot_payload()` (flattens snapshot for frontend), `_player_index()` |
| `sandlot_db.py` | 595 | Postgres layer. `init_schema()`, snapshots (JSONB), `ai_briefs` cache, `get_ai_brief` / `set_ai_brief` |
| `sandlot_skipper.py` | 619 | `SkipperClient` — OpenRouter SSE chat. Model-order helpers: `reasoning_model_order()` (GLM 5.2 first for Skipper/value reasoning), `summary_model_order()` (DeepSeek V4 Flash first for player summaries) |
| `sandlot_waivers.py` | 880 | Waiver-swap engine (cached-AI). Protects IL stashes from drops |
| `sandlot_trades.py` | 362 | Trade grading (cached-AI) |
| `sandlot_matchup.py` | ~1100 | Deterministic matchup projection (baseline, drivers, lineup-move simulation, ranked actions) |
| `sandlot_data_quality.py` | ~270 | Deterministic snapshot data-quality gates |
| `sandlot_calibration.py` | 28 | Admin CLI to review projection calibration |
| `sandlot_refresh.py` | ~200 | Orchestrates scrape → build snapshot blob → store |
| `sandlot_cron.py` | ~50 | One-shot refresh entry (Railway `cron` / GitHub Playwright workflow) |
| `sandlot_config.py` | 27 | Env-flag helpers; warm-ups are opt-in (`SANDLOT_PROFILE_WARM_ENABLED`, `SANDLOT_WAIVER_AI_WARM_ENABLED`) |
| `sandlot_bootstrap_cookies.py` | 35 | Seed scrape auth cookies |
| `player_service.py` | 600 | Player profile + player-index service (backs `/api/player`) |
| `mlb_stats.py` | 405 | MLB stats: season / L7 / L30, pitcher-vs-hitter splits |
| `auth.py` | 246 | Fantrax login/session (shared scrape) |
| `fantrax_data.py` | 624 | Fantrax scrape: rosters, free agents, standings (shared) |

**Cached-AI pattern** (`ai_briefs` table): deterministic compute → AI overlay →
cache by `(snapshot_id, brief_type, subject_key)` with `input_hash` for
staleness. `sandlot_waivers.py` and `sandlot_trades.py` are the references; use
`sandlot_db.{get,set}_ai_brief` for any new cached-AI feature.

---

## 6. API routes (`sandlot_api.py`)

| Method | Path | Does |
|---|---|---|
| GET | `/api/health` | **Only no-DB-friendly probe** — returns 200 `ok:false` on DB error |
| GET | `/api/snapshot/latest` | Flattened snapshot (roster rows, standings, player_index) |
| POST | `/api/refresh` | Trigger scrape + new snapshot. Optional `SANDLOT_REFRESH_TOKEN` guard |
| GET | `/api/waiver-swaps/latest` | Cached waiver board |
| POST | `/api/trades/grade` | Grade a proposed trade |
| GET | `/api/skipper/options` | Skipper model choices |
| GET | `/api/skipper/messages` | Chat history |
| POST | `/api/skipper/messages` | Send message → **SSE stream** |
| DELETE | `/api/skipper/messages` | Clear chat |
| GET | `/api/player/{id}` | Player profile (MLB stats + AI take) |
| POST | `/api/player/{id}/refresh` | Refresh one player |
| GET | `/api/team/{id}/roster` | Per-team roster |

---

## 7. Data flow

```
Fantrax  ──scrape (auth.py + fantrax_data.py)──▶  sandlot_refresh.py
                                                       │ builds snapshot blob
                                                       ▼
                                          Postgres  snapshots.data (JSONB)
                                                       │
                              sandlot_api._snapshot_payload() flattens
                                                       ▼
                              GET /api/snapshot/latest  ──▶  V2App (frontend)

Cached-AI:  deterministic compute (waivers/trades)
              → Skipper/OpenRouter overlay (Kimi → Tencent fallback)
              → ai_briefs cache keyed by (snapshot_id, brief_type, subject_key)
Cron:       sandlot_cron.py runs the refresh on schedule (Railway / Playwright workflow)
```

**Snapshot blob shape:** `data["roster"]["rows"]` (mine),
`data["all_team_rosters"]` (`{team_id: {rows, is_me, ...}}`),
`data["free_agents"]["players"]`, `data["standings"]`. `_player_index()`
flattens all three with a `source` field.

---

## 8. Recent work (merged to `main`)

Three waves, newest first:

**Production hardening (#40–#56):** frontend moved to an esbuild production
build with committed `app.js` (#54) · mock-data paths deleted (#52) ·
`localStorage` persistence removed (#45) · refresh scraper stability +
production noise cleanup (#46–#49) · snapshot freshness aligned with cron
cadence (#56) · cached-AI warm now compares `input_hash` (#40) · warm-ups
became **opt-in** (`SANDLOT_*_WARM_ENABLED`) via `sandlot_config.py` · a real
`unittest` suite (75 tests) now runs in CI.

**Matchup projection engine (#17–#33):** baseline matchup projection (#17) ·
data-quality gates (#18) · skipper projection insight (#21) · lineup move
impact simulation (#22) · ranked improvement actions (#23) · calibration hooks
(#24) · pitcher game-counting fix (#28) · refresh cadence + stale guardrails
(#30–#33) · Playwright E2E suite.

**V1 foundation:** Today as roster-health view · trade grade endpoint + picker
UI (#4) · IL-stash waiver protection · player-index fixes (#15/#41) · Skipper
chat polish (markdown, model controls, matchup context).

---

## 9. Repo state & gotchas

- Work from `main`. The old `codex/*` feature branches are merged or superseded.
- The autonomous agent-loop infra (auto-merge + canary workflows, daily
  routine docs) is **parked on branch `shelf/agent-loop`** — deliberately not
  active.
- Untracked scaffold files in the working tree (`AGENTS.md`, `PRODUCT.md`,
  `DESIGN.md`, `docs/ARCHITECTURE.md`, `.github/` templates) duplicate open
  PR **#60** — remove the local copies when #60 merges.
- **`pushed` nav state / `V2PlayerProfile` is not implemented** (issue #37) —
  only `page` + `detail` (player bottom-sheet) exist today.
- Locally, expect 503s on most routes without `DATABASE_URL`; `/api/health` is
  the only probe that works without a DB.

### Open issues / next up
- The Zo Computer integration path: #57 simplify nav (PR #61) → #58
  Today→Attention Queue (PR #62) → #59 detect status changes → review +
  manually test PR #63 (`POST /api/actions` executor, token-gated, built for
  Zo) → one-tap swap UI from the queue.
- #37 build `V2PlayerProfile` + `pushed` state
- #9 Skipper probability insight · #2 side-by-side matchup roster compare

### CI / workflow
- `.github/workflows/ci.yml` runs on every PR: `python-import-smoke` (imports
  every module **and runs the 75-test `unittest` suite**) + `frontend-build`
  (`npm ci`, `npm run build:sandlot`, fails if committed `app.js` is stale).
- Run tests locally: `.venv/bin/python -m unittest discover -s tests -p "test_*.py"`.
- Non-trivial work → branch `<type>/<issue#>-<slug>`, PR, squash-merge. `gh` is
  authed as `zoelsner` for `zoelsner/baseball`.
