# Hot Swaps Data Readiness Plan

Date: 2026-06-22

## Objective

Unpause Hot Swaps safely by fixing the two remaining data-readiness gaps:

1. Future-game coverage for the current scoring period.
2. Trusted lineup-slot provenance for active roster rows.

No Fantrax writes, Zo writes, add/drop execution, trade execution, or executor
activation is in scope.

## Current Production Evidence

- Snapshot `218` has 37 roster rows and `errors: []`.
- FP/G coverage is ok: 40/40 active players.
- Eligibility coverage is ok: 40/40 active players.
- Future-game coverage is missing: 0/40.
- Lineup-slot provenance is partial: 17/37 trusted rows.
- `/api/hot-swaps/latest` is paused with zero proposals.

## Slice 1: Future-Game Coverage

Use MLB Stats schedule as the primary source for team games in the current
Fantrax scoring period.

After Claude review, future-game coverage is split into two concepts:

- Global schedule coverage is a diagnostic that explains whether rows can be
  mapped to MLB teams and schedule windows.
- Hot Swap readiness is proposal-participant scoped. A proposal can be emitted
  only when every row touched by that proposal has the trusted data needed for
  that player type.

Implementation shape:

- Add a schedule helper in `mlb_stats.py` that fetches MLB games by team id and
  date window.
- Resolve Fantrax team abbreviations to MLB team ids with explicit aliasing and
  hard failure metadata for unresolved teams.
- Enrich both my roster and opponent rosters during refresh with
  provenance-backed schedule data.
- For hitters, count remaining team games in the scoring period.
- For pitchers, do not count all team games as appearances. Preserve
  pitcher-specific semantics:
  - count probable starts when MLB schedule marks this pitcher
  - otherwise keep pitcher opportunity explicitly scoped out of Hot Swaps until
    expected-start modeling exists
- Add a lower date bound so already-played games are not counted as remaining.
- Make future-game quality provenance-aware: mapped-empty schedule,
  unresolved-team mapping, and schedule fetch failure must produce distinct
  diagnostics.
- Keep pitcher schedule context physically separate from hitter team-game
  counts so pitcher projections cannot accidentally multiply FP/G by all team
  games.
- Surface actionable aggregate diagnostics such as unresolved Fantrax team
  abbreviations and schedule fetch failures.

Tests:

- Team schedule normalization with frozen time, including late-today games,
  already-played games, doubleheaders, and postponed/cancelled games.
- Fantrax team abbreviation to MLB team id resolution and unresolved-team
  failure metadata.
- Hitter projection uses remaining team games.
- Pitcher projection cannot multiply pitcher FP/G by all team games.
- Future-game gate stays non-ready for a proposal when any participant row has
  failed team mapping or missing provenance.
- Mapped-empty schedule is treated as a real off-day, not a mapping failure.
- Both my roster and opponent roster must be enriched for coverage to be ok.

Acceptance:

- Production snapshot shows schedule diagnostics and either explicitly scoped
  hitter-only readiness or proposal-specific blocked reasons.
- Remaining-game counts look sane for the scoring window.
- No writes are enabled.

## Slice 2: Trusted Lineup-Slot Provenance

Use the existing read-only Fantrax DOM slot proof path to replace untrusted
`position_fallback` active slots with trusted `dom.lineup-btn` slots.

Implementation shape:

- Verify `SANDLOT_CAPTURE_ROSTER_DOM_SLOTS=1` can run safely on Railway with
  stored Fantrax cookies.
- Improve diagnostics if DOM capture fails: record why slot proof did not
  apply without marking roster scrape failed.
- Verify active rows specifically move from untrusted to trusted sources.
- Keep data-quality fail-closed until the row set needed for swaps has trusted
  slot provenance.
- Use Fantrax player id for DOM proof; name-only matches must not upgrade slot
  provenance.
- Record DOM proof diagnostics such as found/applied/conflicted/mismatch counts
  and active-row slot-proof gaps.

Tests:

- DOM fixture proves active player slot overrides.
- Conflicting DOM slot evidence does not override.
- Proposal-level gate reports ok only when the relevant swap-participating rows
  are trusted.
- Production diagnostic confirms active rows are the rows improving, not just
  already-trusted bench/reserve rows.

Acceptance:

- Production slot provenance improves from 17/37 and all active
  swap-participating rows are trusted.
- `/api/hot-swaps/latest` transitions from `paused` to either `ready` with real
  read-only proposals or a narrower truthful state such as hitter-only ready.
- Proposed cards name OUT and IN players, projected benefit, risk, confidence,
  and provenance.
- `writes_enabled` remains false.

## Stop Conditions

Stop and reassess before write/execution work unless production shows real
read-only proposals with trustworthy future-game and slot provenance. The next
phase after this plan is proposal confirmation safety, not direct Fantrax
mutation.
