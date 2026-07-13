# Sandlot Matchup Projection Model

This is a baseline forecast, not a truth machine and not machine learning. Its job is to give Sandlot a deterministic, inspectable read on the current weekly matchup so the app can say something useful without pretending it knows more than the snapshot contains.

## Current Formula

For each open matchup, Sandlot starts with the live Fantrax score:

```text
projected_final = current_score + sum(active_player_fp_per_game * remaining_opportunities)
```

It runs that formula for your active roster and the opponent's active roster. Bench, IL, reserve, and unavailable players are excluded from the active projection. Hitter opportunities are remaining team games through the matchup period end.

For `matchup_projection_v5`, pitchers never inherit every team game as an appearance. Posted MLB probable starts remain exact opportunities. Active SP rows may additionally use a fractional expected-start total from `verified_gs_cadence_v1`: exact MLB player/team identity, completed pitching game-log rows with `gamesStarted`, completed team-game exposure over a frozen 30-day lookback, and the remaining team schedule.

```text
expected_starts = max(
  posted_probable_starts,
  verified_starts_recent / completed_team_games_recent * future_team_games
)
```

The estimate requires at least two verified recent starts, a latest start no more than 14 days old, a starter-majority role, and nonzero historical/future team-game exposure. It remains fractional and is capped by remaining team games. Reliever cadence is not yet in the live matchup model. Missing or stale evidence fails closed to an explicit zero.

The projected margin is:

```text
projected_my - projected_opponent
```

The rest-of-period swing is:

```text
projected_margin - current_margin
```

Those values drive the plain-language explanations: current margin, projected margin, schedule/game-volume edge, opportunity scope, and risk level.

## Probability Approximation

The win probability approximation is layered on top of the deterministic projection, but it remains withheld in product. A forecast that uses cadence estimates is stored in its own non-complete opportunity cohort and is never eligible for probability release or action-probability deltas.

Completed matchups are simpler: the probability is deterministic from the final score.

## Recommendation Ranking

Lineup recommendations are deterministic too. The app simulates legal active-roster changes and compares the before/after projection.

Supported move shapes:

- Direct bench-to-active swaps when the bench player's eligibility proves the target slot is legal.
- One-hop freeing-up swaps, where an active multi-position player moves to another legal slot, opening the needed slot for a bench player.

Cadence evidence is projection-only. Exact-game counting, proposal participant checks, action contracts, dry-runs, and execution still require a posted player-specific probable start. A cadence-only pitcher cannot become a lineup proposal or execution participant.

The ranking layer prefers win-probability gain, but it also requires a meaningful projected-points gain. Tiny moves are suppressed so the app can honestly say "no compelling action" instead of manufacturing advice.

## What This Is Not

This is not machine learning. There is no training loop, no learned weights, and no model fitting. The current system is a deterministic scoring engine plus a probability approximation.

Actual ML would start after enough logged projections and outcomes exist to prove that a learned model beats this baseline. Until then, adding ML would mostly make the system harder to inspect.

## Current Assumptions

- FP/G is a reasonable short-term baseline for each player.
- Active roster slots define who contributes to the matchup projection.
- Future-game data is available and correctly attached to hitter rows.
- Pitcher exact games require a posted player-specific start. A qualified active SP can contribute a separately labeled fractional verified-GS estimate to the informational matchup projection only.
- Injury/out/IL flags are enough to suppress unavailable players.
- Remaining points can be treated as a rough variance term for probability.
- Multi-position eligibility in the snapshot is enough to prove legal lineup swaps.
- The opponent's active roster and future games are available when projection quality is marked ready.

## Known Weaknesses

- Reliever scoring remains conservative: relievers without a posted start are unmodeled until a separately validated appearance-cadence model exists.
- Starter cadence can miss a rotation change after the frozen snapshot; posted probable schedules remain the exact evidence source as MLB publishes them.
- Negative fantasy points are handled in the mean but do not add negative variance.
- Recent form is not modeled beyond whatever is already reflected in FP/G.
- Lineup uncertainty, rest days, rainouts, probables, and role changes are only as good as the snapshot.
- Opponent context is basic: the model compares active rows and game volume, not detailed matchup quality.
- Cap/transaction constraints are absent from lineup recommendations.
- Eligibility thresholds are not scraped here; the engine only trusts eligibility already present in snapshot rows.

## Evaluation Path

Projection logs are the source of truth for learning whether this works. Each prediction is tagged by `model_version`, matchup key, surface, and day. Completed matchup snapshots fill in actual final score and actual winner.

The calibration report checks:

- projected score error
- projected margin error and bias
- Brier score for probability error
- game-volume bias, especially whether teams with more remaining games are consistently overrated

Daily forecasts and Skipper/API surfaces are retained for horizon diagnostics,
but they are not independent samples. Release readiness selects the earliest
forecast checkpoint for each exact model/matchup and gates on unique completed
matchups. Unlabeled forecast matchups remain in the coverage denominator.
Lower-bound opportunities are reported as a separate cohort rather than being
silently treated as complete pitcher coverage.

Sandlot exposes this evidence at `GET /api/matchup-probability-readiness`.
The endpoint is diagnostic only: product activation, precise probability,
action probability deltas, and autopilot stay locked behind a separate reviewed
release even if a future evidence checkpoint passes. Coarse probability bands
require at least 40 independent, provenance-verified, complete-opportunity
matchups, 95% label coverage, balanced wins and losses, and skill over naive
Brier and margin baselines. Precise percentages remain uncertified until a
later contract adds probability-bin coverage, reliability error, uncertainty,
and temporal holdout evidence; action deltas need separate counterfactual
evidence.

Completed matchup scores are outcomes, never forecasts. The logger refuses to
upsert a completed matchup because doing so could overwrite an earlier same-day
forecast with the final answer. Outcome attachment is exact on matchup and
period and rejects contradictory previously recorded results.

Until there is enough logged history, any accuracy claim should be treated as premature.

## Roadmap Sequence

The reliable path is:

1. Fix snapshot/player-index correctness: [#5](https://github.com/zoelsner/baseball/issues/5)
2. Add baseline projection drivers and logging: [#8](https://github.com/zoelsner/baseball/issues/8)
3. Add data-quality gates: [#14](https://github.com/zoelsner/baseball/issues/14)
4. Keep trade and projection UI honest about confidence: [#3](https://github.com/zoelsner/baseball/issues/3)
5. Feed Skipper the same projection/data-quality surface users see: [#9](https://github.com/zoelsner/baseball/issues/9)
6. Simulate legal lineup move impact: [#10](https://github.com/zoelsner/baseball/issues/10)
7. Rank only meaningful actions and expose no-action states: [#11](https://github.com/zoelsner/baseball/issues/11)
8. Evaluate calibration over time: [#12](https://github.com/zoelsner/baseball/issues/12)

The parent roadmap is [#7](https://github.com/zoelsner/baseball/issues/7). The rule of thumb for future work: make the deterministic engine measurable before making it smarter.
