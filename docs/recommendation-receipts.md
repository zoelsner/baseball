# Recommendation receipts

Recommendation receipts are Sandlot's durable decision-time evidence. They
answer what the app recommended, from which inputs, for which scoring horizon,
before later code records Zach's decision or evaluates the outcome.

## Identity

Each builder has a versioned canonical evidence contract. The Monday lineup V2
builder hashes:

- league, team, season, and target scoring week
- snapshot identity, timestamp, source, and status
- exact current baseline assignment
- exact proposed assignment and unfilled slots
- every normalized player projection used by the optimizer, including slot
  provenance and the deterministic projection basis
- the comparable baseline, projected total, and projected gain
- the exact earliest scheduled MLB game time for the period, used as the
  versioned decision deadline; snapshot and receipt evidence must precede it

Legacy `monday_lineup_v1` receipts remain immutable and lack this deadline.
They can still be read as historical decisions, but cannot enter the v2
decision-science feature dataset.

Dictionary and assignment order do not change the hash. Material projection,
assignment, provenance, or target-week changes do. Wall-clock generation time
and presentation prose are not decision inputs and are not hashed.

Trade assessment v3 uses the same ledger and hashes the exact league, team,
snapshot, give/get player identities and decision-time player facts,
deterministic current-rate grade, explicitly supported or unavailable horizon
states, and the manual-execution guardrail. Trade scopes include the snapshot
ID and both offer sides, so a refresh produces new evidence without
superseding or rewriting a decision recorded against an older snapshot. AI
rationale is presentation and is intentionally excluded from the identity.
Legacy v1 receipts remain readable but do not contain upstream offer lineage;
v2 receipts contain origin lineage but not a frozen outcome contract.
The receipt also retains versioned, normalized eligibility evidence for every
participant: side, slot, age and provenance, protected-player classification,
manual-dynasty-review classification, and FP/G validity. Raw snapshot blobs
and credentials are not copied into the receipt.

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

The League trade cockpit creates a sanitized `trade_assessment` receipt when an
exact offer is graded. With the local owner bridge, Zach can record “intent to
accept” or “pass” against that receipt. These are terminal decision labels for
future calibration, not Fantrax actions: the API and bridge require
`fantrax_changed=false` and `writes_enabled=false`, and trade acceptance remains
manual in Fantrax.

Trade assessment v2 introduced the frozen offer origin. V3 preserves that
contract and also freezes whether the exact package can be measured later.
Manually
entered packages are labeled `manual_entry` with no transaction identity.
Reviewed incoming offers are labeled `incoming_fantrax_offer` and bind the
sanitized Fantrax trade ID, counterparty team ID, source snapshot ID, and the
available proposed/scheduled-execution display labels inside the receipt hash.
The latter is explicitly unverified and is not an execution timestamp. A later
outcome scorer may use that identity as lineage, but it must not infer that the
trade executed merely because the owner recorded intent to accept.

Incoming Fantrax offers remain snapshot data, not receipts, until Zach reviews
one. The public incoming-offers projection excludes raw Fantrax payloads and
only marks a player-only offer gradeable when every player has an exact ID on
both sides. The resulting grade then creates the normal immutable trade
assessment receipt. Draft picks and incomplete identities require manual
review rather than a partial or guessed receipt.

`trade_assessment_v4` retains the v3 outcome contract and additionally requires
the exact `trade_eligibility_v2` participant evidence, including a true
current-rate availability proof for every player. It freezes the
assessment-availability cutoff, a canonical
regular-season Fantrax period calendar observed before the receipt, the exact
first MLB scoring event for the selected period, an offer-cluster key, verified
2026 league scoring rules, and each asset's exact Fantrax scorer ID plus one or
more hitter/pitcher scoring entities. Optional MLB IDs are copied from the same
snapshot when an exact name identity is available; they are not backfilled into
old receipts and do not gate Fantrax-scored evidence. Missing calendars or
ambiguous scoring roles make only `outcome_contract.eligible=false`; the trade
review and decision receipt still succeed with bounded reason codes.
The selected period embeds the normalized candidate game identities and times,
their hash, and the exact minimum so the “first scoring event” claim remains
auditable after the short-lived snapshot is pruned. Period maturity is the
Fantrax period close plus a fixed 24-hour correction grace.

`fantrax_player_period_fpts_v1` supplies an immutable arbitrary-player archive
for mature V4 receipts through exact targeted Fantrax period/role queries. The
first label, `trade_static_package_asset_points_v1`, is the give/get packages'
league-scored asset production in the first complete future scoring period. It
is not lineup lift, causal value, or proof of a completed trade. Rest-of-season
evidence must remain separate, and no numeric dynasty label is authorized by
the current age/current-rate inputs.

Targeted source collection is fail closed: Fantrax must echo the exact
`transactionPeriod`, role filter, `ALL` population, period-only timeframe, and
season selection, with one complete search page and one unambiguous FPts
column. A present `0` row is valid evidence; a missing scorer ID remains
retryable evidence pending and is never silently scored as zero. Collection
runs only after the receipt's frozen correction-grace maturity time and never
blocks a healthy refresh.

After an additional eight-day finalization grace, a fresh complete targeted
query may terminalize an exact-scorer absence only when the same snapshot proves
a newer authoritative Fantrax period is final. That terminal state is
`unavailable` with empty metrics and retained absence lineage—not a zero-point
player. Network, authentication, pagination, parse, and response-identity
failures never terminalize the label.

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
archives a versioned, immutable `fantrax_period_lineup_v2` record for each
completed period when exact daily credited team totals reconcile to the
authoritative final team score. BY_PERIOD roster rows provide potential player
FPts and the final assignment. The archive preserves stable player IDs,
assigned-slot provenance, hitter/pitcher scoring role, exact decimal points,
request/response identity, an optional single-window parent v1 hash, and a
canonical evidence hash. V2 additionally binds the verified league-season
weekly-Monday lineup policy and one final daily Fantrax ACTIVE/BENCH map for
every calendar date in the scoring period. Every day's player-role set must
exactly match the period roster and remain stable within its Monday lineup
window; only the final window must agree with the final period assignment.
Daily credited team totals—not a final-roster
shortcut—must sum to the completed matchup score. Identical refreshes
are no-ops; changed evidence for the same period conflicts instead of rewriting
history, and snapshot pruning leaves the archive intact.

Production Period 15 proved the key single-window semantics: 36 player-role
identities were stable on all seven dates (20 ACTIVE, 16 BENCH); reserve hitter Daylen Lile and
reserve pitcher Sean Burke remained BENCH all week while the period view still
reported their realized potential FPts. The archive is therefore sufficient
input for a static-lineup counterfactual scorer, but archival coverage alone is
not decision uplift. Multi-Monday periods are archived but explicitly
counterfactual-ineligible until player FPts can be attributed to each lineup
window. Until the separate append-only outcome-evaluation ledger
and scorer ship, Sandlot always records:

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
