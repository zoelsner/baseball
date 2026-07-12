# Sandlot Architecture

## Thesis

Sandlot is a single-user-first fantasy baseball operating tool. It should turn
Fantrax and baseball context into a small set of visible decisions for Zach,
starting with the Today Attention Queue.

The architecture favors deterministic compute, cached data, and explicit
freshness over live AI-heavy behavior. AI explains and summarizes; it should not
be the only way important information reaches the user.

## Current Stack

- FastAPI app: `sandlot_api.py`
- Railway web service: `uvicorn sandlot_api:app`
- Railway cron service: `python sandlot_cron.py`
- Postgres: snapshots, refresh runs, chat messages, player profile caches, AI
  briefs, player takes, and durable recommendation receipts
- Frontend: static `web/sandlot/index.html` plus in-browser Babel JSX files
- Scrape layer: `fantrax_data.py`, `auth.py`, `sandlot_refresh.py`
- Player context: `player_service.py`, `mlb_stats.py`
- AI context: `sandlot_skipper.py`, `sandlot_waivers.py`, `sandlot_trades.py`

## Source Of Truth

- Product strategy: `PRODUCT.md`
- Visual system: `DESIGN.md`
- Agent/implementation rules: `AGENTS.md` and `CLAUDE.md`
- Railway/API operations: `docs/sandlot-railway-v1.md`
- Current architecture: this file

If these disagree, resolve the conflict before implementation.

## Data Flow

1. `sandlot_cron.py` or `/api/refresh` calls `sandlot_refresh.run_refresh()`.
2. `fantrax_data.collect_all()` builds the Fantrax snapshot.
3. `sandlot_db` stores the raw snapshot JSONB and refresh-run metadata.
4. `sandlot_api._snapshot_payload()` derives the frontend payload.
5. `web/sandlot/v2-pages.jsx` renders the mobile app from that payload.
6. Optional warmers populate player profiles, media, takes, waiver AI briefs,
   and trade explanations without blocking the core snapshot path.

## Recommendation Evidence

`recommendation_receipts` preserves immutable decision-time evidence beyond the
short snapshot retention window. The first writer is the deterministic Monday
lineup optimizer. Receipts are versioned, scoped to an exact league/team/week,
and superseded rather than overwritten when inputs change. See
`docs/recommendation-receipts.md` for identity, lifecycle, and execution
boundaries.

The receipt ledger is not an execution log. `execution_requests` remains the
separate dry-run control plane, and any future link between them must preserve
exact confirmation, visible live preflight, protected-player enforcement, and
post-write verification.

## Product Boundaries

### Today

Today is moving from roster-health dashboard to Attention Queue. Queue items
should be ordered by consequence, not by source category:

1. injury/status change for an active or high-value player
2. active starter not playing, not pitching, or otherwise risky before lock
3. actionable replacement or waiver review
4. league/trade context worth inspection

### Roster

Roster is the place to inspect Zach's players, slots, status, and player sheet
details. It should not become the home for league-wide trade workflows.

### Adds

Adds stays a deterministic waiver-swap board. It can be entered from Today when
an attention item implies replacement review.

### League

League owns other-team context and trade workflows. Trade should live here until
the workflow proves frequent enough to justify a primary tab.

The trade grader returns a structured `analysis` alongside the legacy grade:
recommendation, evidence by time horizon, roster fit, recommended counter, and
an exact Skipper handoff prompt. Current snapshot FP/G is the only fully modeled
horizon today. Weekly and rest-of-season cards are explicitly unavailable, and
average age is labeled as a limited dynasty signal. The frontend must preserve
those evidence states and must never turn a trade recommendation into an
automatic Fantrax write.

### Skipper

Skipper is an explainer and Q&A layer over real snapshot context. It must not be
the only way to discover important roster issues.

## AI Pattern

Use this pattern for AI-enabled features:

1. deterministic compute chooses or ranks the thing
2. AI explains the already-chosen thing
3. explanation is cached by snapshot/input
4. UI degrades to deterministic text when AI is missing

Do not let OpenRouter latency or model output control the core refresh path.

## Frontend Boundaries

`web/sandlot/index.html` loads scripts in order:

1. `atoms.jsx`
2. `data.jsx`
3. `data2.jsx`
4. `v2-pages.jsx`

There is no module system. Shared symbols must be exported through
`Object.assign(window, ...)`. JSX validation requires Babel parser, not
`node --check`.

## Definition Of Done

For docs-only work:

- content matches current product direction
- no app behavior changed
- final response names the files changed

For Python/backend work:

- targeted unit tests pass where applicable
- import smoke remains viable
- refresh path stays deterministic and cache-first

For frontend work:

- JSX parses with Babel
- relevant UI flow is manually or browser-verified when feasible
- new behavior is tied to a product slice or issue

For PRs:

- issue and PR describe the same slice
- non-goals are preserved
- test evidence is included
- architecture impact is stated

## Exceptions Register

- The app is single-user and mostly unauthenticated by design. `/api/refresh` is
  token-gated when configured. Execution-control routes are the exception:
  they fail closed behind an explicit feature flag and separate SHA-256 owner
  and runner credential digests.
- Local app routes that need `DATABASE_URL` may return 503. `/api/health` is the
  no-DB-friendly probe.
- IL/IR players are protected from waiver-drop suggestions until a richer
  current-news layer can classify return timing.
