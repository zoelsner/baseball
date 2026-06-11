# Agent Guide

This repo is a single-user-first fantasy baseball app. Treat the repo as a
working product system, not a scratchpad. Keep changes scoped, prove behavior
with the strongest available evidence, and do not overwrite user or agent work
already in the tree.

## Product Context

Read these before product or UI work:

- `PRODUCT.md` for the product frame and non-goals.
- `DESIGN.md` for visual system and component rules.
- `docs/ARCHITECTURE.md` for current technical boundaries.
- `CLAUDE.md` for detailed local commands and Sandlot-specific implementation
  notes.

The active product is Sandlot V1: `sandlot_*.py`, `player_service.py`,
`mlb_stats.py`, shared scrape modules, and `web/sandlot/*`. The older CLI
scripts are legacy/supporting utilities unless the user explicitly asks for
them.

## Before Any Code

Use this lightweight gate before editing non-doc code:

1. Confirm the branch and dirty state with `git branch --show-current` and
   `git status --short`.
2. Identify the slice: issue, plan, or explicit user request.
3. Check for product/design/architecture conflicts in `PRODUCT.md`,
   `DESIGN.md`, and `docs/ARCHITECTURE.md`.
4. Name the validation plan. For docs-only work, say docs-only. For frontend
   JSX, run `npm run build:sandlot` and commit the regenerated bundle. For
   Python, run targeted unit tests where available.
5. Preserve unrelated dirty files. Do not revert or rewrite changes you did not
   make.

If a slice cannot pass this gate, ask for the missing decision instead of
guessing.

## Implementation Boundaries

- Keep one product slice per PR.
- Today is the Attention Queue surface. Do not hide important workflows behind
  Skipper prompts.
- Skipper explains and answers questions; deterministic data powers the core
  queue and rankings.
- Trade workflows live under League until they earn a separate primary surface.
- The product UI is recommend-first: it surfaces decisions, it does not fire
  Fantrax writes. Fantrax-write actions are allowed only through the
  token-gated machine API (`POST /api/actions`), and every action must be
  explicitly confirmed by Zach upstream (e.g., a Telegram yes relayed by his
  agent). Never add autonomous or implicit execution paths.
- The Town integration is downstream of the structured Attention Queue.

## Frontend Rules

- Source lives in `web/sandlot/*.jsx` and is bundled with esbuild to the
  committed `web/sandlot/app.js`. Run `npm run build:sandlot` after JSX edits
  and commit the regenerated bundle — CI fails if it is stale.
- ES modules are allowed: use normal `import`/`export`. Do not add globals
  through `window.*`.
- Production is API-backed only. Do not add mock-data globals or `file://`
  demo fallbacks; no-data states are explicit loading/error states.
- Keep bottom navigation durable and sparse: Today, Roster, Adds, League,
  Skipper.
- Use labels and reason text alongside color for all status states.

## Backend Rules

- Keep refresh work deterministic and cheap. Do not couple core refresh to
  optional AI warmups.
- Snapshot-derived payloads should remain cache-first and explicit about
  freshness.
- Use the cached-AI pattern for explanations: deterministic compute, AI overlay,
  cache by snapshot/input, graceful degradation.
- Treat current-news/injury enrichment as a separate data layer, not a reason to
  weaken existing IL stash safety.

## Evidence To Report

At the end of a non-trivial change, report:

- files changed
- product slice completed
- validation run and result
- anything intentionally not covered
- remaining risk or follow-up issue

## Issues And PRs

Use GitHub issues for product slices that will take more than one short edit or
need acceptance criteria. Use the repo issue template so the PR can mirror it.

Use PRs for non-trivial code changes. Docs-only updates may still use a PR when
they establish repo process or product direction.
