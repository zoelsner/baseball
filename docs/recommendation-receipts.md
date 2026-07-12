# Recommendation receipts

Recommendation receipts are Sandlot's durable decision-time evidence. They
answer what the app recommended, from which inputs, for which scoring horizon,
before later code records Zach's decision or evaluates the outcome.

## Identity

Each builder has a versioned canonical evidence contract. The Monday lineup V1
builder hashes:

- league, team, season, and target scoring week
- snapshot identity, timestamp, source, and status
- exact current baseline assignment
- exact proposed assignment and unfilled slots
- every normalized player projection used by the optimizer, including slot
  provenance and the deterministic projection basis
- the comparable baseline, projected total, and projected gain

Dictionary and assignment order do not change the hash. Material projection,
assignment, provenance, or target-week changes do. Wall-clock generation time
and presentation prose are not decision inputs and are not hashed.

`scope_key` identifies one logical opportunity, including the target week.
Only one receipt can be active for a scope. A changed rerun supersedes the prior
pending receipt atomically; it never overwrites its evidence. A receipt with a
recorded decision cannot be superseded.

Snapshot pruning sets the typed foreign key to `NULL`, while the immutable JSON
retains the original snapshot identity and decision-time evidence.

## Lifecycle

- `active`: the latest recommendation for its exact scope
- `superseded`: replaced by changed decision-time evidence before a decision
- `expired`: no longer actionable for its horizon

Decision states are `pending`, `accepted`, and `rejected`. Outcome states are
`pending`, `scored`, and `unavailable`. `GET
/api/recommendation-receipts/latest` exposes only the latest active, unexpired
Monday receipt through a sanitized public projection; it never exposes the raw
projection inputs and returns `204 No Content` when no receipt is active. `POST
/api/recommendation-receipts/{receipt_id}/decision`
records one terminal owner intent with exact receipt/hash binding, DB-clock
expiry, and compare-and-swap semantics. An exact same-state replay is
idempotent; stale, expired, superseded, or conflicting decisions return a
conflict instead of changing history.

The Today card shows the projected gain and exact start/bench delta. Decision
controls are desktop-owner-only: the production browser sends the exact
receipt decision to the loopback owner bridge, which retains the bearer token
locally and revalidates the upstream identity and no-write boundary. Mobile or
bridge-offline sessions remain useful and read-only instead of showing dead
controls. Accepting means “I intend to use this plan”; it does not claim the
lineup was changed.

## Outcome telemetry

`team_result_v1` scores a receipt only when a later successful snapshot
contains a completed Fantrax matchup for the same league, team, and exact
period start/end. It copies the final observed team score and normalized source
identity into the receipt, then records the projected-total residual and
absolute error. Identical retries are no-ops; changed evidence conflicts
instead of silently rewriting history.

Missing evidence remains retryable through an eight-day finalization grace
window. Only after Fantrax exposes a newer authoritative completed period does
Sandlot terminalize the missed receipt as `unavailable`; one failed refresh or
one incomplete response never does so.

This first scorer is forecast telemetry, not decision uplift. Sandlot now
archives a versioned, immutable `fantrax_period_lineup_v1` record for each
completed period when Fantrax's exact BY_PERIOD roster rows reconcile to the
authoritative final team score. The archive preserves stable player IDs,
assigned-slot provenance, hitter/pitcher scoring role, exact decimal points,
request/response identity, and a canonical evidence hash. Identical refreshes
are no-ops; changed evidence for the same period conflicts instead of rewriting
history, and snapshot pruning leaves the archive intact.

Archival coverage alone is not decision uplift. Reserve-player points still
need to be proven as valid counterfactual values, and the historical slot must
be proven to represent the league's full lineup cadence. Until then Sandlot
always records:

- `measurement_scope: observed_team_total`
- `adherence_state: unverified`
- `counterfactual_state: unavailable`
- `actual_baseline: null`
- `actual_gain: null`
- `autopilot_eligible: false`

`GET /api/recommendation-outcomes/recent` exposes those labels with recent
scored receipts. Neither an accepted intent nor a small team-total error proves
that the proposed lineup was used. `counterfactual_lineup_v1` must pass those
remaining semantics and complete assignment-coverage checks before Sandlot may
calculate realized lineup gain or use outcomes to graduate an action toward
autopilot.

## Safety boundary

The Monday workflow refuses to persist a comparable-gain receipt unless every
roster row has trusted Fantrax slot provenance. Receipt persistence happens only
after deterministic optimization and before publishing the artifact. A
persistence failure fails the workflow rather than publishing an untracked
recommendation as trustworthy.

Receipts and decision recording do not execute Fantrax actions. An accepted lineup receipt may
be linked to an `execution_request`, but the execution request remains a
separate exact-confirmation, visible-preflight, and post-write-verification
control plane. Trade execution remains manual.
