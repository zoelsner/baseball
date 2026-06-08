# Agent Guide

This repo is a single-user-first fantasy baseball app. Treat Sandlot as a
working product system, not a scratchpad. Keep changes scoped, prove behavior
with the strongest available evidence, and do not overwrite user or agent work
already in the tree.

## Product Context

Read `CLAUDE.md` and the relevant docs before product, UI, or backend work. The
active product is Sandlot V1: `sandlot_*.py`, `player_service.py`,
`mlb_stats.py`, shared scrape modules, and `web/sandlot/*`. The older CLI
scripts are legacy/supporting utilities unless the user explicitly asks for
them.

## Before Any Code

Use this lightweight gate before editing non-doc code:

1. Confirm the branch and dirty state with `git branch --show-current` and
   `git status --short`.
2. Identify the slice: issue, plan, or explicit user request.
3. Check for conflicts with `CLAUDE.md` and the relevant docs.
4. Name the validation plan. For frontend JSX, run the bundle build. For
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
- Sandlot remains recommend-only for human-facing surfaces. Do not imply the
  app UI, Skipper, Today, Adds, or League can execute Fantrax moves.
- `POST /api/actions` is the narrow exception: a Zo Computer
  machine-to-machine executor only, token-gated with `SANDLOT_ACTIONS_TOKEN`,
  Postgres advisory-lock guarded, and logged to `action_logs`. Do not expose it
  as a user-facing feature.

## Frontend Rules

- Source lives in `web/sandlot/*.jsx` and is bundled to `web/sandlot/app.js`.
- Use normal ES module `import`/`export` in the JSX source.
- Run `npm run build:sandlot` after JSX edits and commit the regenerated
  bundle.
- Keep bottom navigation durable and sparse: Today, Roster, Adds, League,
  Skipper.
- Use labels and reason text alongside color for all status states.

## Backend Rules

- Keep refresh work deterministic and cheap. Do not couple core refresh to
  optional AI warmups.
- Snapshot-derived payloads should remain cache-first and explicit about
  freshness.
- Use the cached-AI pattern for explanations: deterministic compute, AI
  overlay, cache by snapshot/input, graceful degradation.
- Fantrax write actions must have code-level safety constraints: no automatic
  retries, drop confirmation, IL eligibility checks, roster-size guards,
  session freshness checks, and transaction logging.

## Evidence To Report

At the end of a non-trivial change, report:

- files changed
- product slice completed
- validation run and result
- anything intentionally not covered
- remaining risk or follow-up issue

## Issues And PRs

Use PRs for non-trivial code changes. Docs-only updates may still use a PR when
they establish repo process or product direction.
