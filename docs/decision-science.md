# Sandlot decision science

Sandlot's first modeling boundary is an offline evaluation contract, not a
production prediction service. The current production learning report has zero
scored counterfactual weeks as of 2026-07-12, so fitting weights today would be
unsupported.

## Dataset contract

`lineup_decision_features_v1` joins one latest active Monday lineup receipt per
league/team/period to its immutable `counterfactual_lineup_v1` evaluation.
Only deadline-capable `monday_lineup_v2` receipts can enter model features.
Legacy v1 periods remain visible in coverage as
`legacy_deadline_unavailable_periods`; they are never silently backfilled with
an assumed lock time.
Features are constructed only from the decision receipt:

- projected gain
- baseline and proposed projected points
- count of players changed
- unfilled-slot count
- days from decision generation to the target period

The realized counterfactual gain is stored only in the label block. Receipt,
source-archive, and evaluation SHA-256 identities are retained in a separate
lineage block. Raw evaluation evidence never becomes a feature.

Every sample records `feature_cutoff_at` and `label_available_at`. A rolling
model may train on a prior row only when that row's label was available by the
new decision's cutoff. Ordering rows by period alone is not sufficient because
it can leak a late-arriving result into an earlier prediction.

The Monday producer also persists the exact earliest MLB game time returned by
the schedule API as `decision_deadline_at` with source
`mlb_schedule_first_game_v1`. Both the source snapshot and generated receipt
must precede that event. This permits the real Monday 4am ET workflow while
rejecting a refresh after scoring has begun; a calendar-midnight proxy is not
treated as a lineup lock.

## Baselines and gates

Run the offline report with:

```bash
python sandlot_decision_science.py
```

The report evaluates:

1. `projected_gain_identity_v1`: the deterministic projected gain is the
   realized-gain prediction.
2. `rolling_affine_gain_v1`: an interpretable intercept and slope fit on labels
   that were already available at each prediction cutoff.

The candidate needs at least eight eligible training rows and four subsequent
rolling evaluation horizons. At least 75% of all recommendation periods must
also have a scored label; pending, unavailable, and ineligible periods remain
in the denominator with reason counts. This prevents stable, easy-to-score
weeks from standing in for the volatile weeks where advice may matter most.
Periods whose midnight-ET close is after the report's explicit `as_of` time are
reported as not yet due and do not lower coverage. Pass `--as-of` to reproduce
a historical report exactly.
For mature unscored periods, the report joins archived lineup evidence and
separates structurally counterfactual-ineligible reasons from eligible rows
whose evaluation is missing and periods whose archive never arrived.
Even if it beats the comparable baseline, this first
contract always returns `eligible_for_product_use=false` and
`autopilot_eligible=false`. It measures a retrospective static-lineup
counterfactual, not causal lift or proof of execution.

## Next gates

- Accumulate real completed periods rather than synthesizing training rows.
- Add interval coverage and calibration once the sample supports it.
- Pre-register candidate features before reading their outcomes.
- Use rolling time splits across seasons and report performance by position,
  injury state, and evidence quality.
- Keep model fitting offline and CPU-only until a candidate materially beats
  the simple baseline out of sample.
- Treat Hugging Face news classifiers or embeddings as separately versioned,
  provenance-bearing input features; generated text never authorizes an action.

## Explicit scope boundary

This dataset calibrates projected gain for pre-period, static Monday lineup
choices only. It does not estimate causal execution effects and does not yet
learn waiver value, trade value, rest-of-season player value, prospect value,
or dynasty outcomes. Those require separate versioned labels—transaction
availability and realized roster value for waivers; decision-time market and
future production horizons for trades; and multi-season keeper/prospect
outcomes for dynasty analysis. Reusing this weekly label for those questions
would be a modeling error.

The trade path preserves exact incoming Fantrax offer identity and now freezes
a predeclared, first-complete-period measurement contract in
`trade_assessment_v4`. The contract binds assessment time, a decision-time
Fantrax/MLB calendar hash, a strict first-scoring-event deadline, verified
league scoring weights, an offer cluster, and exact Fantrax player-role scoring
entities. It can be explicitly ineligible without blocking the useful trade
review. This is measurement groundwork, not a trade model. Sandlot now archives
append-only league-scored player-period evidence for mature V4 trade receipts
using exact Fantrax period and role queries. Owner
intent, observed ownership transfer, and verified Fantrax execution must remain
three different facts. The future asset-production label must be calculated for
accepted, rejected, and undecided assessments alike so selection by owner intent
does not masquerade as recommendation quality.

The first label is named `static_package_asset_points_delta`. Package
sizes and each scorer-role contribution must remain visible because a raw
two-for-one package total does not account for the open roster slot,
replacement level, lineup usage, or opportunity cost. It must never be
presented as accept/reject value, weekly lineup impact, or dynasty value. Source
evidence is shared per entity-period; receipt evaluations remain separate, and
later analysis selects the earliest assessment per frozen offer cluster and
horizon without filtering on intent or realized production.

This evidence is structurally selected. V4 receipts exist only for packages
Zach chose to review, and today's deterministic grader admits player-only,
adult, current-rate-gradeable offers while routing draft picks, protected or
minor-league assets, players age 24 or younger, and missing age/FP/G evidence
to manual review. Results therefore cannot be generalized to all observed
offers or dynasty trades. Before any quality claim, report the full funnel:
incoming offers observed → reviewed → receipted → contract eligible → mature →
scored, with bounded exclusion reasons at every transition.
