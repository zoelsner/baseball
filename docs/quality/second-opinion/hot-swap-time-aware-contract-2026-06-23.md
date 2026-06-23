# Second Opinion: Hot Swap Time-Aware Contract

## Context

Sandlot has a read-only Hot Swaps feature for fantasy baseball lineup changes.
The current production card can name the OUT player, IN player, projected
benefit, confidence, risk, and safety checklist, but all Fantrax writes remain
disabled. The user wants a future one-click proposal path, but not until the app
can prove it will not move or drop the wrong player.

## Current Slice

This branch adds a stricter non-executable proposal contract and a more
conservative movability check:

- `proposal.executable` is always `false`.
- `proposal.writes_enabled` remains `false`.
- `proposal.contract` records the snapshot id, league/team ids, move-out
  player, move-in player, target slot, fallback slot, projected benefit,
  movability state, blocked gates, confirmation copy, and a deterministic
  `input_hash`.
- Movability is an AND of:
  - Fantrax raw provider data: `raw.scorer.disableLineupChange`.
  - MLB schedule timing from `future_games` / `team_future_games`.
- Provider `true` means `locked`.
- Provider `false` plus no started game means `movable`.
- Missing/non-boolean provider data means `unknown`.
- If any relevant MLB `gameDate` is at or before current time, the player is
  `locked` even if Fantrax says movable.
- If a game is today but has no parseable start time, the player is `unknown`.
- Protected/minors/IL rows are still excluded before a card can be emitted.
- No add/drop/trade/Fantrax/Zo write path is added.

## Verification So Far

- Focused recommendation tests passed, including:
  - provider-locked participant blocks movability
  - explicit provider-movable participants still keep executor blocked
  - missing provider data becomes warning/unknown
  - started MLB game locks even when provider says movable
  - missing same-day MLB start time becomes warning/unknown
  - contract is non-executable and carries stable OUT/IN fields
- Attention/Hot Swaps route tests passed.
- Full Python suite passed: 179 tests.
- `git diff --check` passed.
- Frontend bundle rebuilt with local esbuild and produced no bundle diff.

## Review Questions

1. Is the movability model conservative enough for a future one-click lineup
   executor, given that writes are still disabled in this slice?
2. Is the proposal contract missing any field that a safe future executor would
   need before performing a Fantrax lineup-only slot change?
3. Are there failure modes where this could incorrectly mark a player movable?
4. Are there tests we should add before merging this read-only slice?
5. Would you change the ordering or wording of the safety gates?

Please be skeptical and concise. Separate must-fix blockers from follow-up
recommendations.
