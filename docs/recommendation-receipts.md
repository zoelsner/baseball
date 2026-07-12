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
`pending`, `scored`, and `unavailable`. The schema reserves explicit values,
versions, and evidence for future scoring; this first slice does not yet expose
a decision API or claim that any recommendation has been followed or scored.

## Safety boundary

The Monday workflow refuses to persist a comparable-gain receipt unless every
roster row has trusted Fantrax slot provenance. Receipt persistence happens only
after deterministic optimization and before publishing the artifact. A
persistence failure fails the workflow rather than publishing an untracked
recommendation as trustworthy.

Receipts do not execute Fantrax actions. A future accepted lineup receipt may
be linked to an `execution_request`, but the execution request remains a
separate exact-confirmation, visible-preflight, and post-write-verification
control plane. Trade execution remains manual.
