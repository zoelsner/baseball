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
lineup was changed. Outcome scoring remains a separate future slice.

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
