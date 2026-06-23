# Claude Checkpoint: Hot Swaps Data Readiness Plan

Model: `opus`
Effort: `xhigh`
Date: 2026-06-22

## Verdict

Claude agreed that MLB schedule data is the right source for future-game
coverage and that Fantrax writes should stay out of scope. It strongly
recommended splitting the work into two PRs:

1. Future-game schedule enrichment.
2. Trusted lineup-slot provenance.

## Feedback Accepted

- Do not model pitcher opportunity as `team_games * pitcher FP/G`; that would
  badly overstate starters and relievers.
- Future-game coverage must be provenance-aware, not just field-presence aware.
  A row with `future_games: []` because team mapping failed should not make the
  gate green.
- Add a lower date bound when counting remaining games so already-played games
  do not inflate projections.
- Enrich both my roster and opponent roster, because the gate covers active
  players from both sides.
- Treat slot proof as a separate operational slice because it depends on live
  Fantrax cookies / DOM capture.
- Consider unpausing a hitter-only Hot Swaps surface before pitcher modeling is
  fully trusted, as long as the product clearly limits the scope.

## Resulting Direction

Build schedule-backed future games first. For hitters, team games can feed
`FP/G * remaining_games`. For pitchers, preserve the existing contract:
project only pitcher-specific starts/appearances when backed by probable-start
or expected-appearance evidence. Do not count every team game as a pitcher
appearance.

After schedule coverage is production-proven, enable and verify the read-only
DOM slot proof path so active rows move from `position_fallback` to
`dom.lineup-btn` or another trusted source. Keep all execution/write paths
disabled until real OUT/IN proposals are generated and reviewed.
