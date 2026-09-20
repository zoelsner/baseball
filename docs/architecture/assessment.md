# Independent Assessment: Analysis Quality, ML, and Autonomy

**Audited commit:** `bd54135` (`origin/main`, 2026-07-14) · **Date:** 2026-07-24

This is an outside-in read. It deliberately does not defer to `STATUS.md` or
`docs/decision-science.md`, which are the maintainer's own account. Where this
document disagrees with those, the disagreement is the point; where it agrees,
that is corroboration rather than repetition.

Companion documents: [risk-register.md](risk-register.md) for defects,
[doc-drift.md](doc-drift.md) for documentation accuracy,
[orientation.md](orientation.md) for the map.

---

## 1. How the analysis actually works

**No LLM touches a number.** Every figure the UI displays is computed
deterministically; the model only writes prose about numbers that already exist.
Every user-facing sentence is deterministic string formatting.

This deserves to be said plainly because it is unusual and it is right. Most
projects that describe themselves as AI-powered have the model somewhere in the
numeric path, which makes the output unreproducible and unauditable. Sandlot
does not. The architecture is sound and the discipline behind it is visible.

Two independent pipelines produce projections:

**Matchup projection** (`sandlot_matchup.py`)

```
projected_total = current_score + Σ(fppg × remaining_opportunities)
win_probability = normal_approximation(μ_mine − μ_opp, σ²_mine + σ²_opp)
```

Hitters receive every remaining team game. Pitchers receive only posted
probables, plus a frozen starts-per-team-game cadence estimate for starters
(`sandlot_pitcher_opportunities.py:244-253`).

**Monday lineup optimizer** (`sandlot_lineup.py`, `sandlot_autopsy.py`)

League-exact scoring of MLB game logs (`sandlot_scoring.py:19-44`), a 0.55/0.45
recent/season blend (`sandlot_lineup.py:51`), an explicit playing-time share
(`:91`), then an exact bitmask dynamic-programming slot assignment
(`sandlot_autopsy.py:143`).

These two never reconcile. See §2.

---

## 2. Is it any good?

**A very good constraint solver wrapped around a mediocre forecaster.**

The assignment step is genuinely strong. An exact bitmask DP over slot
eligibility solves a problem humans reliably get wrong — people greedily fill
the obvious slots and strand a multi-eligible player in the wrong place. That
component beats a careful human.

The projections feeding it do not. Three verified defects, in descending order
of impact:

### The two models disagree structurally

The Monday optimizer models playing time (`sandlot_lineup.py:91-93`):

```python
    share = min(1.0, games_recent / team_games_recent) if team_games_recent else 0.0
    return team_games_next * share
```

The matchup projection does not (`sandlot_matchup.py:2288-2290`):

```python
        delta = fppg * games
```

Fantrax's FP/G is per game *played*; `games` is the player's *team's* remaining
games. For an everyday starter these coincide. For a platoon bat or an
injury-returnee, they do not, and the error is **directionally biased toward
over-projection of part-time players** — precisely the direction that
manufactures a false swap recommendation.

The right fix is not to port the formula. It is to notice that one quantity
should not have two implementations. ([R4](risk-register.md#r4))

### Variance equals the mean

```python
        variance += max(1.0, abs(fppg)) * games
```

A Poisson assumption applied to a heavy-tailed sum. Measured σ(margin) ≈ 35
against a realistic ≈ 90. Every displayed win probability is pushed toward the
extremes — a true 65% renders as 85%.

This compounds badly with §3: the probability has never been checked against a
single realized outcome, so there is no feedback path that would surface the
error. ([R5](risk-register.md#r5))

### No sample-size shrinkage anywhere

A three-game callup at 18 FP/G tops the waiver board. There is no games-played
term in the waiver path at all. Any projection system that has existed since
2004 handles this; Marcel does it in three lines. ([R9](risk-register.md#r9))

### Would it beat an informed human?

Honestly: **the assignment step yes, the rankings no.** A person who knows their
roster and glances at the schedule will beat the current waiver and matchup
output, mostly because they intuitively apply the shrinkage and playing-time
adjustments the code omits. They will lose to the bitmask DP on slot assignment.

That is a fixable gap, and the fixes are small. It is not a fundamental problem
with the approach.

---

## 3. Does the learning loop close?

**No. And the reason is not the one the documentation gives.**

`docs/decision-science.md` states that there are zero scored counterfactual weeks
and therefore fitting weights is unsupported. That is true. The natural reading
is *not enough time has passed*. That reading is wrong.

Five independent gates each block label collection. The one I verified directly
is a join (`sandlot_db.py:920-937`):

```sql
             AND l.period_start = r.period_start
             AND l.period_end   = r.period_end
```

Receipts carry calendar Monday–Sunday bounds (`sandlot_receipts.py:356-357`).
Evidence carries Fantrax's real period bounds. When Fantrax runs an extended
period — as it did July 13–26, documented in your own `STATUS.md` — the dates
differ and the row does not join. It does not appear as pending or errored. It
is simply absent.

**The count stays at zero not because time has not passed, but because rows are
being silently discarded.** Waiting another full season would not change it.

A second gate was reported structurally unreachable: relievers guarantee
`opportunity_completeness != "complete"`, so the probability release cohort is
always empty. Same shape — a gate that looks like it is accumulating evidence is
waiting on an impossibility. ([R10](risk-register.md#r10))

Retention compounds both: ~15 days of history caps any backfill.
([R16](risk-register.md#r16))

**The pattern worth naming: silent-empty is this system's dominant failure
mode.** It appears in the join, in the release gate, in the Playwright skips
that turn a broken scrape into a green run, and in the snapshot normalizer that
reports `fresh` for missing data. In every case a failure is represented as an
ordinary empty state. That is one architectural lesson, not four unrelated bugs,
and it is the most valuable thing this audit found.

---

## 4. Where ML helps, and where it does not

Grounded in the data that actually exists. Ranked by value-to-effort.

### Do not build

**A projection system.** The current model is worse than Marcel — a 2004
baseline that is three lines of arithmetic. Beating Steamer, ZiPS, or THE BAT
from scratch, on one season of one league's data, is not a realistic target.
Ingest a public rest-of-season projection through `sandlot_scoring` instead. This
single decision removes most of the modeling surface and makes the rest of the
roadmap tractable.

**A learned win-probability model.** You have no labels (§3). Fix the pipeline
first; revisit in a year.

**A trade model.** Trades are rare, heterogeneous, and strategically
adversarial. The sample will never support it. A deterministic surplus
calculation over good projections is the right tool, and the audit notes the
current grade is a raw FP/G sum with no package-size or slot normalization — so
every 2-for-1 grades well. Fix that arithmetic rather than reaching for a model.

**Anything fit at the team-week unit.** `rolling_affine_gain_v1` needs roughly
16 seasons at that granularity. Its 8/4 minimums gate plumbing, not statistical
power.

### Do build, in order

**1. Player-week calibration.** The highest-value item by a wide margin. The
receipt already stores whole-roster projections and the archive stores realized
period points. Scoring at the *player*-week unit instead of the *team*-week unit
yields roughly 800 observation pairs per season instead of 22. That is the
difference between an intractable problem and a routine one — and it requires no
new data collection, only a different join. Blocked on §3.

**2. Empirical per-player weekly variance.** Computed from game logs already
being fetched. Fixes the variance defect with data in hand and no new
dependencies.

**3. Port playing-time share into the matchup model.** Not ML. Listed here
because it will improve projection accuracy more than any model you could fit
this season, and the code already exists and is tested.

**4. Reliever cadence modeling.** Unblocks the calibration gate. The audit notes
the needed logic already exists at `sandlot_lineup.py:88`.

**5. Rank waivers on expected period points, not rate.** Combines shrinkage with
opportunity. Small change, immediately visible improvement.

Note that items 2–5 are arithmetic, not machine learning. **The honest headline
is that this project's analysis does not currently need ML. It needs its
existing arithmetic corrected and its feedback loop unblocked.** Once §3 is
fixed and a season of player-week pairs exists, item 1 becomes real modeling
work worth doing properly.

---

## 5. What is actually holding autonomy back

Sorted into the four categories that matter, because they have different
remedies.

### (a) Deliberate safety gates — not the blocker

Writes are fail-closed structurally, not by flag. Every Fantrax method in the
repo is a hardcoded read; `sandlot_execution.py` cannot import Selenium; the
dry-run contract re-derives proposals server-side and whole-object-compares
confirmations. An attacker owning the Railway environment would have to add code
to move a lineup.

These gates are proportionate and correctly placed. **None of them is what is
stopping you.** The instinct to build the safety architecture first was right.

### (b) Missing data — this is the blocker

Zero labels, ever (§3). Fifteen days of history. No playing-time data feed. No
news or injury feed. You cannot demonstrate skill because you have never
measured an outcome, and you have never measured an outcome because of a join
condition.

### (c) Technical gaps — real but secondary

No working write path exists; the Selenium executor remains unmerged. Railway
tokens are unset. Multi-step atomicity is unproven — if a three-move lineup
change half-applies, current behavior is undefined.

### (d) Trust and evaluation — downstream of (b)

No demonstrated skill on any metric, because no metric has ever been computed.

### What would have to be true

Before an unsupervised lineup change, in order:

1. §3 fixed and **at least one full period scored end-to-end**, with the realized outcome visible.
2. Calibration showing projections beat a naive baseline — last week's lineup unchanged — over a meaningful sample.
3. Win probabilities calibrated, not merely displayed. Currently they are known-overconfident.
4. Atomicity proven: a partially-applied multi-move plan must be detectable and reversible.
5. A kill switch verified by use, not by inspection.

### Blast radius, honestly

Small, and smaller than the architecture implies. A wrong slot change costs
roughly 5–25 points out of ~250 and is reversible until first pitch. The
irreversible actions are drops and trades — and the code **already draws exactly
that line**, permitting lineup dry-runs while withholding drop and trade
mutations.

That is the correct boundary. It means supervised lineup automation is a much
shorter step than the current gating suggests, once (b) is resolved.

### Is the foundation right?

Yes, with three amendments. The receipt-and-evidence architecture is the correct
shape for eventual autonomy — immutable decision-time evidence is exactly what
you need to evaluate a decision after the fact, and most projects reach this
point without it.

The amendments:

1. **Bind receipts to Fantrax period identity rather than calendar dates.** Highest leverage change in this document; small and local.
2. **Make evidence extraction incremental and retryable.** It currently raises on any of ~8 conditions across 7–14 live calls, and lost periods are unrecoverable because only `latest_completed` is ever attempted.
3. **Raise retention** before fixing (1), or the fix has nothing to operate on.

---

## Bottom line

The engineering is better than the analysis, and the safety work is better than
both. That is an unusual and recoverable position — it is far easier to improve
arithmetic inside a sound architecture than to retrofit safety onto a good model.

The single highest-value action is not modeling. It is fixing a join condition
so that outcomes start being recorded. Everything on the ML and autonomy roadmap
is downstream of that one change, and every period that elapses before it lands
is permanently unrecoverable.
