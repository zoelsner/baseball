# Sandlot Matchup Projection Model

This is a baseline forecast, not a truth machine and not machine learning. Its job is to give Sandlot a deterministic, inspectable read on the current weekly matchup so the app can say something useful without pretending it knows more than the snapshot contains.

## Current Formula

For each open matchup, Sandlot starts with the live Fantrax score:

```text
projected_final = current_score + sum(active_player_fp_per_game * remaining_games)
```

It runs that formula for your active roster and the opponent's active roster. Bench, IL, reserve, and unavailable players are excluded from the active projection. Remaining games come from the snapshot's future-game data and only count games through the matchup period end.

The projected margin is:

```text
projected_my - projected_opponent
```

The rest-of-period swing is:

```text
projected_margin - current_margin
```

Those values drive the plain-language explanations: current margin, projected margin, schedule/game-volume edge, and risk level.

## Probability Approximation

The win probability is an approximation layered on top of the deterministic projection. It treats the remaining projected points as a rough variance source and uses a normal approximation to estimate the chance your projected final beats the opponent's projected final.

That means the probability is useful for direction and comparison, but it is not calibrated truth yet. The UI should prefer bands like "slight edge", "toss-up", or "comfortable edge" over precise probability claims when confidence is thin.

Completed matchups are simpler: the probability is deterministic from the final score.

## Recommendation Ranking

Lineup recommendations are deterministic too. The app simulates legal active-roster changes and compares the before/after projection.

Supported move shapes:

- Direct bench-to-active swaps when the bench player's eligibility proves the target slot is legal.
- One-hop freeing-up swaps, where an active multi-position player moves to another legal slot, opening the needed slot for a bench player.

The ranking layer prefers win-probability gain, but it also requires a meaningful projected-points gain. Tiny moves are suppressed so the app can honestly say "no compelling action" instead of manufacturing advice.

## What This Is Not

This is not machine learning. There is no training loop, no learned weights, and no model fitting. The current system is a deterministic scoring engine plus a probability approximation.

Actual ML would start after enough logged projections and outcomes exist to prove that a learned model beats this baseline. Until then, adding ML would mostly make the system harder to inspect.

## Current Assumptions

- FP/G is a reasonable short-term baseline for each player.
- Active roster slots define who contributes to the matchup projection.
- Future-game data is available and correctly attached to player rows.
- Injury/out/IL flags are enough to suppress unavailable players.
- Remaining points can be treated as a rough variance term for probability.
- Multi-position eligibility in the snapshot is enough to prove legal lineup swaps.
- The opponent's active roster and future games are available when projection quality is marked ready.

## Known Weaknesses

- Pitcher scoring is volatile, especially starts, relief appearances, and negative outings.
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
