# Win This Week

Win This Week is Sandlot's deterministic, read-only current-matchup decision
engine. It answers: "What can I still do before lock to maximize projected
points this week without making a reckless dynasty move?"

## Surfaces

- `GET /api/snapshot/latest` includes `win_this_week`.
- `GET /api/win-this-week/latest` returns the same plan as a dedicated
  machine-readable surface. Both routes use the same matchup-decision context
  so data quality, projection, lineup ranking, and action ranking cannot drift
  through separate derivation paths.
- Today renders the primary action immediately below the matchup score and
  above the supporting Hot Swaps and Attention Queue evidence.

## Ranking Contract

The engine ranks alternatives by deterministic expected remaining-week points.
It does not add alternatives together because lineup and waiver actions can
overlap.

Each ranked action includes:

- exact ordered steps
- expected remaining-week point impact and its calculation basis
- projected matchup margin before and after the primary action
- an exact MLB-start-derived deadline
- confidence
- dynasty cost
- legal-path state and remaining live-preflight requirements
- `writes_enabled: false`

Win probability is included only after calibration is supported. Until then,
the engine explicitly ranks by projected points and returns
`win_probability_delta: null`.

The post-action outlook is deterministic: Sandlot adds the primary action's
comparable point impact to the pre-action projected margin and states whether
the remaining-week estimate still leaves the manager behind, tied, or ahead.
It does not translate that arithmetic into a win-probability claim until the
probability model is calibrated. The production monitor recomputes this margin
identity and fails if the API or UI-facing summary drifts.

When MLB schedule acquisition succeeds but one or more pitchers have no posted
probable start, Sandlot returns a labeled `known_opportunities_lower_bound`
instead of suppressing every hitter and lineup recommendation. Those pitchers
contribute zero until MLB publishes a player-specific opportunity; the payload
reports their count and win probability remains uncalibrated. A failed schedule
read still blocks projection.

### No-action explanations

When no option clears the legal and meaningful-value gates, the API does not
discard the work it already performed. `no_action.alternatives` exposes up to
three concise rejected options, ordered by comparable projected impact and
closeness to actionability. Each item names the proposed move, preserves its
exact lineup chain when available, and explains the failed threshold,
provenance, deadline, movability, or post-add gate.

Today renders these under **Best alternatives checked**. This distinguishes a
real evidence-backed no-action result from missing recommendation data and lets
the manager see whether the best rejected move was harmless noise, blocked by
freshness, or dominated by a better no-transaction plan.

### Lineup actions

Lineup actions reuse `sandlot_matchup`'s eligibility, remaining-game,
multi-slot-chain, movability, and game-start checks. Sandlot applies the best
legal chain to a copy of the roster, re-projects it, and repeats so independent
gains become one ordered multi-move plan instead of competing alternatives.
Only movable actions with exact deadlines enter that bundle. Locked or
uncertain actions become monitoring items instead.

Current Fantrax roster payloads prove destination legality with
`eligibleStatusIds` and `eligiblePosIds`; the older
`scorer.disableLineupChange` flag is no longer present. Sandlot normalizes the
status and position IDs, checks every step against its exact destination, and
still lets a started MLB game hard-lock the participant. A missing destination
mapping stays unknown rather than being assumed movable.

### Waiver actions

Waiver actions are not ranked from season FP/G subtraction alone. Sandlot:

1. validates trusted FP/G, age, drop protection, free-agent schedule, and the
   move-out player's current Fantrax Drop action;
2. constructs a hypothetical post-add roster;
3. proves either a direct eligible replacement or a complete bench-to-active
   lineup chain;
4. recomputes the post-add matchup projection; and
5. ranks the action only when that projection improves remaining-week points.

After the required add-to-lineup path is applied, Sandlot runs the same
sequential optimizer used by lineup-only plans. A waiver plan can therefore
include independent lineup fixes rather than being undervalued as a pickup in
isolation.

The post-add plan must also beat the best currently legal lineup-only plan.
Sandlot suppresses a waiver transaction that improves on doing nothing but is
dominated by a free bench-to-active move, because the transaction and dynasty
cost would be unnecessary.

This allows a lower-FP/G streamer with more remaining games to outrank a better
season-rate player when the weekly math supports it. The normal waiver board
retains its existing positive-rate filter; expanded streaming candidates are
used only inside Win This Week and must pass exact post-add simulation.

For latency, Sandlot first ranks a bounded candidate frontier by a cheap
schedule-backed weekly-points ceiling. Only the best eight candidates receive
the expensive full post-add roster and sequential lineup simulation. The
ceiling is a pruning tool only; it is never exposed as the final action impact.

Waiver legality remains `provisionally_legal` until a fresh Fantrax preflight
confirms that the player is still available and transaction locks have not
changed.

Fantrax's current roster client maps action type `3` to Drop and type `4` to
Trade. Sandlot preserves that action metadata from each raw roster row and
requires an explicit type-`3` Drop action plus a not-yet-started MLB game before
a move-out player is treated as available for current-week planning.

## Dynasty Safety

- Aaron Judge is a named never-drop anchor.
- Minor-league, IL/IR, explicitly protected, untrusted-age, and young dynasty
  assets remain excluded by the waiver move-out guard.
- Every waiver action labels dynasty cost separately from short-term points.
- Lineup-only actions have no dynasty cost because no player leaves the roster.

## Monitoring

The plan emits non-additive monitoring actions when it needs fresh schedule,
movability, or deadline evidence. The primary action also gets a preflight
reminder because MLB lineups, injuries, Fantrax availability, and locks can
change after the stored snapshot.

Today schedules a silent snapshot refetch for the primary action's exact
deadline. If a tab is throttled or misses that timer, the panel still detects a
past deadline locally, replaces the action label with `Refresh required`, and
hides the normal action handoffs until a new plan arrives. The production
monitor also rejects expired action deadlines and any drift between the plan
embedded in `/api/snapshot/latest` and `/api/win-this-week/latest`.
For a `no_action` state, it additionally requires a reason plus the structured
alternatives list, and validates any displayed point estimate as comparable.

## Safety Boundary

Win This Week never invokes Fantrax. The API payload, every ranked action, and
the production read-only monitor require writes to remain disabled. Any future
executor handoff is a separate confirmation contract and is not implied by an
action being ranked first.

## Verification

- deterministic unit tests cover cross-surface ranking, lower-rate streaming,
  missing schedule provenance, unknown deadlines, locked move-outs, completed
  matchups, and Aaron Judge protection;
- the production monitor checks ranks, positive comparable impact, deadlines,
  dynasty cost, legal-path state, calibrated-probability boundaries, protected
  anchors, and read-only flags;
- local mobile Playwright verifies loading, empty, stale, expired, error,
  success, Today hierarchy, and the Skipper handoff;
- an authenticated, non-persisted live Fantrax run on 2026-07-11 verified the
  browser → API → fresh snapshot → recommendation flow. Today rendered a legal
  three-step lineup plan worth an estimated +3.3 remaining-week points, its
  MLB-start deadline, lower-bound projection caveat, disabled mutation control,
  and the exact read-only Skipper handoff;
- a route-parity regression test and the production monitor require the
  embedded and dedicated Win This Week plans to match exactly.
