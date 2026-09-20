# Sandlot analysis audit — outside-in read

Worktree: `.../scratchpad/main-audit` @ `bd54135` (origin/main).
Verification run: full suite green — **583 tests, 2 skipped** (`python -m unittest discover -s tests -p "test_*.py"`), matching the maintainer's claim.

Notation: **VERIFIED** = read directly from the code at the cited line, or reproduced by executing the code. **SUSPECTED** = strong inference I could not close without production data or a Fantrax API call.

Headline: the deterministic engineering discipline here is genuinely unusual — provenance, fail-closed gates, hash-bound immutable evidence, leakage-aware dataset contracts. The *statistical* content of the models is thinner than the surrounding machinery implies. The gap between "how carefully this is plumbed" and "how much the numbers know about baseball" is the single most important thing in this report. Two of the evaluation gates are, as written, structurally unreachable.

---

## 1. How the analysis actually works

### 1a. The matchup projection (what Today shows)

Entry point `sandlot_matchup.compute_projection()` (`sandlot_matchup.py:87`). Pipeline:

1. **Gate.** `data_quality["projection_ready"]` must be true (`sandlot_matchup.py:92`); computed in `sandlot_data_quality.snapshot_data_quality()` (`sandlot_data_quality.py:25`, flags at `:119-123`).
2. **Completed matchup** → probability is a hard 0/1 from the final score (`:104-130`, `_deterministic_prob` at `:2549`).
3. **Open matchup** → per-side `_team_projection()` (`:2277`):

```python
    for row in _active_rows(rows):
        games = _projection_opportunities(row, period_end, period_start)
        fppg = _row_fppg(row)
        delta = fppg * games
        mean_delta += delta
        variance += max(1.0, abs(fppg)) * games
        games_remaining += games
    return current_score + mean_delta, variance, round(games_remaining, 4)
```
(`sandlot_matchup.py:2286-2296`)

4. **Opportunities** (`_projection_opportunities`, `:2482`):

```python
    exact = float(_games_remaining(row, period_end, period_start))
    if not _is_pitcher_row(row):
        return exact
    evidence = row.get("pitcher_opportunity_estimate")
    expected = sandlot_pitcher_opportunities.valid_projection_estimate(evidence, period_end)
    if expected is None:
        return exact
    return max(exact, expected)
```

`_games_remaining` (`:2173`) counts rows in `row["future_games"]` inside `[period_start, period_end]`, and for pitchers requires a player-specific probable-start marker (`_has_pitcher_specific_appearance`, `:2211`). `future_games` is attached at refresh by `sandlot_future_games.enrich_snapshot_future_games()` (`sandlot_future_games.py:31`); the window starts at `max(matchup.start, now)` (`:341`) and `mlb_stats.fetch_team_schedule` excludes games already started (`mlb_stats.py:279-282`). Hitters get every remaining team game; pitchers get only rows where MLB has posted them as probable (`sandlot_future_games.py:198-209`).

5. **Win probability** (`_win_prob`, `:2299`) — normal approximation on the margin:

```python
    total_var = var_my + var_opp
    if total_var <= 0:
        return _deterministic_prob(mu_my - mu_opp)
    z = (mu_my - mu_opp) / math.sqrt(total_var)
    return max(0.0, min(1.0, 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))))
```

6. **Everything the user reads** — current margin, projected margin, rest-of-period swing, game-volume edge, risk level, prose summary — is derived arithmetic in `_drivers()` (`:2308`) / `_driver_summary()` (`:2388`) / `_risk_level()` (`:2352`). No model text.

**Cadence supplement** (`sandlot_pitcher_opportunities.py`): for active SP rows only, expected starts = `min(future_team_games, max(posted_probables, starts_recent/team_games_recent * future_team_games))` (`:244-253`), gated on ≥2 verified starts, latest start ≤14 days old, starter-majority usage (`:233-242`). Relievers are explicitly `_unmodeled` (`:141`).

**Where the intelligence lives:** entirely in the code. There is no LLM anywhere in the projection path. The AI layer (`sandlot_waivers.py:266-304`, `sandlot_trades.py:1309/1397`) only writes cached prose *about* an already-chosen deterministic result, under system prompts that forbid changing rank/delta/confidence (`sandlot_waivers.py:66-73`). That architectural discipline is real and correctly implemented.

### 1b. The Monday lineup optimizer

`scripts/run_monday_lineup.py` (GH Action, `0 8 * * 1` = 4am ET Monday, `.github/workflows/monday-lineup.yml`).

1. Read latest successful snapshot, **read-only connection** (`run_monday_lineup.py:171-172`). Refuse if any roster row has untrusted `slot_source` (`:147-163`).
2. Week = coming Mon..Sun (`coming_week`, `:40`).
3. Pull MLB schedule: next-week team games (`:200`), 30-day recent team games (`:202`), posted probables (`:203`), first game time (`:201`).
4. Per player: resolve MLB id, fetch game log, score every game with **league-exact weights** (`sandlot_scoring.py:19-44`; IP 3.0, QS 3, HLD 3.5, SV 4, W 2, L −2, hitter K −0.5). IP is parsed correctly from MLB's `6.2` notation (`mlb_stats.py:802-814`) — a detail many implementations get wrong.
5. Project (`sandlot_lineup.project_week`, `:95`), hitting and pitching as separate components.
6. Assign exactly-optimally: bitmask DP per side, brute-force enumeration of two-way side assignments (`sandlot_lineup.propose`, `:171`; DP in `sandlot_autopsy._max_assign`, `:143`). This is genuinely exact, not greedy.
7. Emit markdown + `monday_lineup.json` + one immutable receipt (`sandlot_receipts.build_monday_lineup_receipt`, `:251`), SHA-256 `input_hash` over decision-time evidence, `expires_at` = midnight ET after week end (`:345`).

**No AI in this path either** — the docstring's claim (`sandlot_lineup.py:19`) is accurate. VERIFIED.

### 1c. The two projection models disagree with each other

This is the most important structural observation in section 1. Sandlot has **two independent player projections that never reconcile**:

| | in-week matchup (`sandlot_matchup`) | Monday optimizer (`sandlot_lineup`) |
|---|---|---|
| rate | Fantrax `fppg` (season-to-date, per game *played*) | 0.55·recent-10 + 0.45·season, league-scored from MLB logs |
| hitter opportunities | **all remaining team games** (`:2173`) | team games × playing-time share (`sandlot_lineup.py:91`) |
| SP opportunities | posted probables, or frozen cadence estimate | `starts_recent/team_games_recent × future` |
| RP opportunities | **0, always** | `games_recent/team_games_recent × future` (`:88`) |
| recent form | not modeled (`docs/…-model.md:81`) | 55% weight |

Nothing checks that they agree. The Today card's "+9.1 hot swap" and the Monday receipt's "+14.2 projected gain" are produced by different math on different inputs, and the counterfactual label only ever scores the second one.

---

## 2. Is the projection model sound?

### 2.1 Weights: mostly absent, and where present, arbitrary

There are essentially no weights in the matchup projection — it is `rate × opportunities`, which is defensible as a baseline. The arbitrary constants live in the *ranking* layers:

- `sandlot_lineup.blended_rate` (`:51-60`): `0.55 * recent_avg + 0.45 * season_avg`, with a hard cliff at `recent_n >= 3 and season_n >= 3`. Round numbers, no justification, never fit.
- Waiver `sort_score` (`sandlot_waivers.py:450-461`):

```python
    fallback_penalty = 0 if add["true_fpg"] else -2.0
    loose_penalty = -0.7 if fit == "loose" else 0
    ...
    sort_score += 1.0 if weak_fit else 0
    sort_score += 0.7 if fit == "direct" else 0
    sort_score += 0.5 if move["is_bench"] else 0
    sort_score += 0.8 if move["status_issue"] else 0
    sort_score += loose_penalty + fallback_penalty + dynasty_penalty
```
These are magic numbers. In their favor: they're in the same unit as `net_delta` (FP/G), they're small, and they're inspectable. Against: nobody has ever checked whether "direct positional fit is worth 0.7 FP/G" is true.
- Trade letter grades (`sandlot_trades.py:51-61`): `A+ ≥ 4.0, A ≥ 2.0, A− ≥ 1.0, B+ ≥ 0.5, B ≥ 0.0, …` FP/G. Pure convention.
- Confidence/risk bands (`sandlot_matchup.py:963-987`, `sandlot_waivers.py:811-827`): thresholds at 5/2 points, 0.05/0.02 probability, 0.08/0.18 probability edge. Conventional.

**Verdict:** the *core* projection is honestly weightless (good). The *decision* layers are hand-tuned constants that have never been validated against anything. That's fine for a v1 as long as nobody mistakes them for calibrated — and the code does not currently mistake them.

### 2.2 Normalization: the units are consistent; the *semantics* are not

Everything is in league fantasy points, so nothing incomparable is literally summed. But two comparisons are semantically wrong:

**(a) FP/G × team games double-counts playing-time.** VERIFIED. `_row_fppg` (`:2464`) reads Fantrax `fppg` = points per **game played**. `_games_remaining` (`:2173-2190`) counts **team** games with no playing-time factor. For a strong-side platoon hitter, `fppg` is inflated *precisely because* he sits against same-handed pitching, and then the projection assumes he plays every game. The error compounds in the same direction.

Concrete scenario: bench hitter with 7.5 FP/G over 40 starts (team has played 100 games, he plays ~55%) vs. an active everyday hitter at 8.5 FP/G. Six remaining team games → bench projects 45.0, starter projects 51.0. A 6-point gap looks like "no action." Reality is ~24.8 vs ~51.0. Now flip it: give the platoon guy 8.8 FP/G (very common) and the swap projects **+1.8 points and fires a Hot Swap card** that is actually −26 points of expected value. This is the single most consequential modeling defect I found, because the bench is *by construction* full of part-time players, so the bias points directly at the recommendation surface. `sandlot_lineup.expected_games` (`:91`) already has the fix (`share = games_recent / team_games_recent`); `sandlot_matchup` never adopted it.

**(b) Cross-role FP/G comparison in waivers.** VERIFIED. `_pair_card` computes `net_delta = add.fpg - move.fpg` (`sandlot_waivers.py:431`), and `_same_position_group` (`:661-667`) treats **all pitchers as one group**. An SP's FP/G is per start (~1/week); an RP's is per appearance (~3/week). Comparing them as rates is wrong by ~3×. STATUS.md:141-144 records the autopsy's finding that "holds RPs + two-start SPs are the mispriced waiver assets" — and the production waiver board still ranks on the exact metric that mis-prices them. `sandlot_win_week._weekly_candidate_ceiling` (`sandlot_win_week.py:1075`) partially compensates by re-sorting on `fpg × countable_games`, but only inside the Win-This-Week surface; `/api/waiver-swaps/latest` still uses raw `net_delta`.

### 2.3 Sample size: **no shrinkage anywhere**. A .400-over-10-AB does rank like a .400-over-300.

VERIFIED across three surfaces:

- **Waivers:** `_add_candidate` (`sandlot_waivers.py:318-337`) reads FP/G from the Fantrax stat cell and validates only that it is 0.5–25.0 (`MIN/MAX_PLAUSIBLE_FPG`, `:28-29`). There is no games-played field anywhere in the module (`grep -n "games_played\|\bGP\b"` → no hits). A 3-game callup at 18 FP/G tops the board over an established 9 FP/G regular.
- **Trades:** `_compute_deltas` (`sandlot_trades.py:799-813`) sums raw `fppg`. Same exposure.
- **Monday optimizer:** `blended_rate` (`sandlot_lineup.py:51-60`) is the *only* place any sample logic exists, and it is a cliff, not shrinkage: at `recent_n = 3` it applies full 55% weight to a 3-game sample; below 3 it falls back to the season mean, which for a September callup is itself a 12-game sample used at full weight.

There is no prior, no regression to a positional mean, no replacement level, no minimum-exposure filter. This is the largest *statistical* gap in the system and it is not disclosed in `docs/sandlot-matchup-projection-model.md` (which lists "recent form not modeled" but not "small samples not shrunk").

### 2.4 Not modeled at all

Confirmed absent from every module I read: **park factors, opponent pitcher handedness/quality, bullpen/rotation strength, lineup slot, weather, day/night, travel, rest days, injury-return ramp, aging curves, positional scarcity, replacement level, roster-slot opportunity cost.** `_weak_positions` (`sandlot_waivers.py:512-530`) is the closest thing to positional scarcity and it is *wrong in a systematic way*: it averages your own roster's raw FP/G per position and takes the three lowest. Because catchers and relievers score less than outfielders *league-wide*, this will report C/RP/SS as "weak" for essentially every roster in the league regardless of relative strength. It needs a league-relative baseline to mean anything. VERIFIED.

Schedule density *is* modeled (game counts drive everything). Opponent quality is modeled only as "the opponent's rows × their game volume" — the doc is honest about this (`docs/…-model.md:83`).

### 2.5 Pitchers: how much of the projection is estimated rather than modeled?

Quantified by executing the code (probe with 11 hitters / 6 SP / 3 RP per side, one posted probable each SP, 7-day period):

```
completeness: known_opportunities_lower_bound
unmodeled: 6   estimated: 0
proj: 744.0 / 728.0   win_prob: 0.6747
release_eligible: False
```

- **Relievers are structurally unmodelable.** `sandlot_future_games._enrich_row` gives pitchers only probable-start rows (`:199-207`); `_games_remaining` requires a pitcher-specific marker (`:2187`); `sandlot_pitcher_opportunities` refuses relievers by design (`:140-142`). Any active RP therefore lands in `pitchers_without_opportunity_model` (`sandlot_matchup.py:2525-2526`). With 3 RP slots per side, **≥6 unmodeled pitchers on every open matchup, forever.**
- STATUS.md's "11 cadence-estimated plus six unmodeled" decomposes exactly: 6 = the two rosters' RP slots; 11 = SPs whose posted probable was missing and who cleared the cadence gate. So on the production #285 dry run, **of ~17 active pitchers across both rosters, 0 were fully modeled from posted probables alone** — 11 were fractional estimates and 6 contributed zero.
- The size of the estimate's effect is in STATUS.md:72-73: adding cadence moved the projection from **268.9–274.8 to 409.2–393.4**. That is ~140 points per side, i.e. **~34% of the final projected total on each side comes from an estimated (not observed) opportunity count**, and the remaining reliever contribution is a known-zero floor. The projected margin flipped sign (−5.9 → +15.8) as a result. Any user reading "409–393, you're ahead" is reading a number whose sign is set by an unvalidated cadence heuristic.

### 2.6 The variance model is wrong by roughly 2.5–3× in standard deviation

`variance += max(1.0, abs(fppg)) * games` (`sandlot_matchup.py:2294`) asserts per-opportunity variance ≈ per-opportunity mean — a Poisson assumption applied to a quantity that is not a count. Measured on the probe roster: model σ(margin) = **35.3** on 624 remaining points per side.

Reality check: a hitter's single-game FP in this scoring (1/1B, 2/2B, 3/3B, 4/HR, 1/R, 1/RBI, 1/BB, −0.5/K) has σ ≈ 6–8 against a mean of ~8, so per-game variance ≈ 40–60, not 8. Aggregating 11 hitters × 6 games plus 6 starts gives σ(team-week) ≈ 60–70 and σ(margin) ≈ 85–95. **The model's uncertainty is ~2.5–3× too small.** Consequence: projected margin +16 → model says p = 0.675, a correctly-scaled model says ≈ 0.57; +50 → model 0.92 vs ≈ 0.70. Every probability is pushed toward the extremes.

The maintainer withholds probability in product, so no user is currently misled — but the same variance feeds `win_probability_delta`, which is a **hard gate** on lineup recommendations: `_evaluate_move_chain` rejects any chain with `win_probability_delta < 0` (`sandlot_matchup.py:1025`), and `_clears_meaningful_threshold` (`:948`) would require ≥0.01 probability gain once `probability_calibrated` flips true. With the current variance, that threshold is ~2.5× easier to clear than it should be. The fix is cheap and the data exists: estimate per-player weekly variance from the same MLB game logs the Monday runner already fetches.

### 2.7 Bugs and edges

**B1 — `_deterministic_prob` can emit a 0.0/1.0 forecast on a *live* matchup.** VERIFIED reachable. `compute_projection` returns early only if `my_games + opp_games <= 0 AND not (unmodeled_pitchers or known_zero_schedule)` (`:164-165`). If all remaining opportunities are unmodeled relievers, `total_var == 0` and `_win_prob` falls through to `_deterministic_prob` (`:2301-2302`), returning exactly 1.0 or 0.0. That row is then logged by `projection_log_payload` (`:249`) and enters the Brier average in `_calibration_metrics` (`:434`) as a certainty. Trigger: late Sunday of a period when every hitter's games are done and only relievers remain. Impact is bounded (release cohort excludes it — see B2), but it poisons the diagnostic Brier. No test covers it (`tests/test_sandlot_matchup.py` has no zero-variance case).

**B2 — the probability-release gate is unreachable as written.** VERIFIED by execution. `_release_forecast_eligible` requires `drivers.opportunity_completeness == "complete"` (`:491-496`); `compute_projection` sets `"complete"` only when there are zero estimated **and** zero unmodeled pitchers (`:151-158`); relievers guarantee unmodeled ≥ 1. Therefore `release_eligible_matchups` is always empty, `release_readiness.state` is permanently `"collecting"` with `no_complete_provenance_eligible_forecasts` (`:374-378`), and `MIN_BAND_READY_MATCHUPS = 40` (`:32`) is decoration. This is *not* "waiting for evidence" — it is waiting for a condition that cannot occur. The calibration tests fabricate rows with `"opportunity_completeness": "complete"` (`tests/test_sandlot_calibration.py:74,88,102,166`), so the suite cannot detect it. **Fix: model reliever appearance cadence (the code is a near-copy of the SP path — `sandlot_lineup.expected_games:88` already implements it), or redefine the release cohort to "no *unmodeled* opportunities" and treat cadence estimates as a labelled cohort rather than a disqualifier.**

**B3 — Monday runner's recent-exposure denominator includes unplayed games.** VERIFIED. `team_game_counts(recent_start, today)` (`run_monday_lineup.py:72-94, 202`) counts every scheduled game with no status filter, including today's (Monday's) unplayed slate and any postponed games in the window. The numerator (`games_recent`, `starts_recent`) counts only completed game-log rows. Systematic ~4% under-projection of every hitter's playing-time share and every starter's cadence. Note `sandlot_pitcher_opportunities` fixed exactly this bug with an explicit comment — `history_end = now.date() - timedelta(days=1)` and `fetch_completed_team_game_counts` (`sandlot_pitcher_opportunities.py:86-90`, `mlb_stats.py:312-317`) — but the Monday runner was never updated. Observable effect: `projected_total`, `current_total` and therefore the receipt's `projected_gain` (the sole decision-science feature) are all scaled ~0.96, and are not comparable to `sandlot_matchup`'s numbers.

**B4 — unbounded exponential over two-way players.** SUSPECTED (low severity). `for combo in range(1 << len(two_way))` (`sandlot_lineup.py:215`; same at `sandlot_autopsy.py:255`) with no cap. Realistically `len(two_way) ≤ 2`. But `eligibility_tokens` (`sandlot_autopsy.py:50`) unions `positions` + `all_positions` and falls back to `slot`; a scrape regression that stamped a pitcher token onto many rows would produce a hang inside a 15-minute GH Action. `_MAX_POOL_PER_SIDE = 20` caps the DP but nothing caps this loop. One-line fix: cap at the 6 highest-projection two-way players and place the rest greedily.

**B5 — two-way pitcher-slot value includes hitting points.** SUSPECTED, load-bearing. `run_monday_lineup.py:256-257`:
```python
        hitter_proj = round(component_points.get("hitting", 0.0), 1)
        pitcher_proj = round(hitter_proj + component_points.get("pitching", 0.0), 1)
```
This makes a two-way player strictly ≥ as valuable in a pitcher slot as a hitter slot, always. It is justified only by a docstring assertion — "Fantrax repeats hitter categories under the pitching scoring group for two-way players" (`sandlot_scoring.py:10-12`) — with no test and no captured Fantrax evidence. If that rule is wrong, every Ohtani-class assignment is over-valued by a full week of hitting. Meanwhile `fantrax_data` models hitter and pitcher as *separate scoring entities* with separate `period_fpts` (`fantrax_data.py:757`, roles `"10"→hitter, "20"→pitcher` at `:765`) and rejects a player active in both roles (`:521-523`), which is at least in tension with the docstring. **Worth one manual box-score check; it is cheap to verify and expensive to be wrong about.** Note `tests/test_sandlot_analytics.py:220-238` pins `projected_for_slot` semantics with hand-made fixtures but never exercises the producer at `run_monday_lineup.py:257`.

**B6 — `_max_assign` never selects a negative-value assignment** (`sandlot_autopsy.py:193`, `best_val = 0.0`). Correct for `propose` (an empty slot scores 0, better than a negative pitcher). For the autopsy it means `optimal ≥ 0` always while `actual` can be negative, so `points_left` is inflated and `efficiency` can go negative on bad days. Documented as an upper bound (`sandlot_autopsy.py:16-18`), so: acknowledged, not a bug, but the aggregate "mid-pack lineup efficiency" claim in STATUS.md:139-141 inherits it.

**Div-by-zero / None:** I found none live. `expected_games` guards `exposure > 0` (`:86,88,91`); `blended_rate` guards `season_n` (`:58`); `_number` returns None on non-finite (`:2564`); `_active_rows_have_valid_fppg` (`:2478`) blocks the projection before `_row_fppg` can silently 0-fill. `_win_prob` guards `total_var <= 0` — see B1 for the consequence. Period date math checked: `coming_week` (`:40-45`) is correct on Mondays and non-Mondays; `_parse_date` truncates to 10 chars and returns None on garbage (`:2573`); `_completed_period_participation` rejects `(end - start).days > 13` (`fantrax_data.py:711`).

### 2.8 Would this beat a well-informed human eyeballing the same data? My real read.

**Split verdict, and the split is the point.**

**Where it beats the human, decisively:**
- The exact 20-slot assignment (`_max_assign`, `sandlot_autopsy.py:143`). Humans reliably leave slots empty and mis-solve multi-position constraint chains. This is a genuine, repeatable edge and it is correct.
- Never forgetting to check schedule volume. "This guy has 7 games, that guy has 4" is exactly the thing humans skip and the machine never does.
- Two-start-SP detection via posted probables. Real, and worth points every week.
- Arithmetic hygiene: league-exact scoring weights, correct IP notation, correct QS definition, correct completed-vs-scheduled game filtering in the schedule fetch.

**Where the human beats it, also decisively:**
- **Playing time.** A human knows Player X is the short side of a platoon. The matchup model does not (§2.2a). This is the failure mode most likely to produce a *wrong recommendation*, not just a noisy one.
- **Small samples.** A human discounts a 5-game hot streak instinctively. The waiver board ranks on it (§2.3).
- **Injury and role news.** DTD-but-playing, a closer who just lost the job, a starter on a rehab ramp, a September callup with a real role. None of this is modeled; the only signal is a Fantrax status token.
- **Anything about the future beyond "he's been good."** No aging, no regression, no ROS forecast. The doc concedes this (`docs/ARCHITECTURE.md:108-110`: "current snapshot FP/G is the only fully modeled horizon").

**Honest bottom line:** on *lineup assignment given a set of projections*, it is better than a human and should be trusted. On *the projections themselves*, a well-informed human with the same Fantrax page beats it — not because the human is smarter, but because FP/G × team-games is a weaker projection than "what a person who watches this team knows." Netting it out: this is a very good **constraint solver** wrapped around a **mediocre forecaster**. The good news is that those are separable, and the forecaster is the part you can buy off the shelf (§4).

The documentation is mostly honest about this. `docs/sandlot-matchup-projection-model.md:3` — "not a truth machine" — is exactly right. The two things the docs *don't* say and should: (i) no sample-size shrinkage exists anywhere, and (ii) hitter opportunities ignore playing time while the Monday optimizer does not.

---

## 3. Does the learning loop close?

**No. It is fully built and it does not run.** The architecture is genuinely complete end-to-end; every stage is gated by an AND that currently fails. Here is the trace.

### The chain

```
Monday 4am ET GH Action
  → receipt (monday_lineup_v2), period = calendar Mon..Sun     [sandlot_receipts.py:281,356-357]
Refresh (2×/day, Railway)
  → fantrax_data.extract_completed_lineup_evidence(latest_completed)   [fantrax_data.py:420]
  → sandlot_db.archive_lineup_period_evidence                          [sandlot_refresh.py:234-242]
  → sandlot_db.receipts_missing_outcome_evaluation(...)                [sandlot_db.py:913-937]
  → sandlot_receipts.build_counterfactual_lineup_evaluation            [sandlot_receipts.py:669]
  → recommendation_outcome_evaluations (counterfactual_lineup_v1)
Offline
  → sandlot_db.list_lineup_decision_science_rows                       [sandlot_db.py:867]
  → sandlot_decision_science.build_report                              [:269]
```

### Where it breaks — five independent gates, in order

**G1 — the archive could not exist before 2026-07-12.** VERIFIED. `extract_completed_lineup_evidence` raises unless `VERIFIED_WEEKLY_LINEUP_POLICIES[(league_id, season)]` exists (`fantrax_data.py:534-536`), and that dict has exactly one entry, `verified_at: "2026-07-12"` (`:193-206`). Every completed period before that date raised, was swallowed into `snapshot["errors"]` (`fantrax_data.py:2549-2552`), and is **unrecoverable** — the collector only ever attempts `matchup.latest_completed` (`:2536-2543`), so once the next period completes the previous one is gone forever. This alone explains "0 of 8 scored": the eight periods in the denominator predate the only date on which archiving became possible. *The doc's claim is verified, and the reason is more banal and more fixable than the doc implies.*

**G2 — the evidence extractor is a long AND-chain over 7–14 live Fantrax calls.** VERIFIED. `_completed_period_participation` (`fantrax_data.py:697`) issues one `getLiveScoringStats` per day of the period and raises on *any* of: response date/period mismatch, a day not final (`:761`), an unknown scoring group, a duplicate role, `set(found) != set(expected)` (`:785`), participation changing inside a Monday window (`:790`), daily credited totals not summing to the observed team score (`:546`), or active-player total not matching (`:549`). A single raise loses the whole period. Two of these are load-bearing in normal play:
- `:785` requires the daily scorer map to be *exactly* the end-of-period roster. SUSPECTED: any add/drop transaction during the period breaks this, and this is a league where the product's whole waiver surface encourages weekly adds.
- `:549` requires end-of-period active players' period FPts to sum exactly to the team score — which fails if an active player was dropped mid-period after scoring.

**G3 — the period must be a single Monday window.** VERIFIED. `single_window = len(participation["windows"]) == 1` (`fantrax_data.py:544`), windows keyed by `day - timedelta(days=day.weekday())` (`:784`). `counterfactual_capability.eligible = single_window` (`:588-596`), and `receipts_missing_outcome_evaluation` filters on `l.evidence->'counterfactual_capability'->>'eligible' = 'true'` (`sandlot_db.py:934`). A 14-day Fantrax period → 2 windows → permanently ineligible. STATUS.md:17-19 confirms the July 13–26 period is exactly this case.

**G4 — the receipt/evidence join is on exact period dates.** VERIFIED, and this is the gate I think the maintainer may not have noticed. `receipts_missing_outcome_evaluation` joins `l.period_start = r.period_start AND l.period_end = r.period_end` (`sandlot_db.py:918-920`). The receipt's period is *calendar* Mon..Sun from `coming_week()` (`run_monday_lineup.py:40-45` → `sandlot_receipts.py:356-357`). The evidence's period is the *Fantrax scoring period* (`fantrax_data.py:552-555`). For an extended period (Jul 13–26) the receipt says Jul 13–19 and the evidence says Jul 13–26: **no join row at all.** That receipt is not "pending" and not "ineligible" — it is invisible, and `coverage_report` classifies it as `completed_period_evidence_missing` (`sandlot_decision_science.py:163`), which reads like a data-collection failure rather than a period-alignment mismatch. Every non-7-day and every non-Monday-aligned Fantrax period is silently unscoreable.

**G5 — snapshot retention.** VERIFIED. `prune_successful_snapshots(keep=30)` keeps the **30 most recent successful snapshots by count**, not 30 days (`sandlot_db.py:2204-2221`, called at `sandlot_refresh.py:117`). At 2 refreshes/day that is **~15 days of roster history**. The autopsy explicitly walks "every Monday-to-Sunday scoring week we have snapshots for" (`scripts/run_autopsy.py:6`) — so it can never see more than ~2 weeks. The archived `lineup_period_evidence` and `recommendation_receipts` tables survive pruning (that is the whole point of receipts), but the autopsy track does not use them.

### `sandlot_calibration.py` — what is calibrated, against what

It is a 28-line CLI wrapper (`sandlot_calibration.py:13-16`) over `sandlot_matchup.calibration_report` (`:290`). It calibrates **matchup score/margin/probability forecasts** against completed matchup finals — a *different* loop from the counterfactual lineup loop, with a different sample unit (unique matchup) and different gates.

What it measures (`_calibration_metrics`, `:409-462`): score MAE, margin MAE, margin bias, Brier, Brier skill vs. even, margin skill vs. the naive-zero baseline, and margin error bucketed by game-volume-edge sign. The choice of baselines (naive p=0.5 and naive margin=0) is exactly right, and picking the **earliest** forecast per matchup as the sample (`_independent_forecast_checkpoints`, `:470-488`) correctly avoids treating 14 daily forecasts of the same week as 14 samples. Refusing to log completed matchups (`:237-241`) closes a real leak. This is well-designed evaluation code.

**Is it meaningful yet? No, and it cannot become meaningful without a code change.** See B2: the release cohort is structurally empty because relievers exist. Separately, `MIN_BAND_READY_MATCHUPS = 40` unique matchups at ~22–24 matchups/season means **two full seasons minimum** even if B2 were fixed — for the coarse *band* release, with precise probability explicitly deferred to a later contract (`docs/…-model.md:110-114`).

### `sandlot_autopsy.py` — what the post-hoc analysis actually measures

Exactly one thing, correctly and narrowly: **"how many points did I leave in reserve slots, holding my slot template and my roster fixed, with perfect hindsight?"** (`team_day`, `:274-286`). It is not decision quality — the docstring says so plainly (`:16-18`). It excludes IL/OUT players from the optimal pool (`_candidate_pool`, `:200`), protects dynasty MIN assets (`:210`), holds the manager's own slot template fixed rather than the full league template (`slot_template`, `:88-96`), and computes an exact assignment.

Three limits worth naming: (i) points are **reconstructed** from MLB logs via `sandlot_scoring`, not read from Fantrax's credited FPts, so any scoring-rule transcription error is invisible and consistent (`run_autopsy.py:13-14`, coverage-gated at `MIN_TRUSTED_COVERAGE = 0.90`, `:44`); (ii) it holds the *observed* template, so it never charges you for leaving a slot empty — the opposite convention from `sandlot_lineup.propose`, which optimizes the *full* template (`sandlot_lineup.py:38-44`); (iii) it is capped at ~15 days by G5.

### Does the system ever learn whether its advice was right?

**Not yet, on any surface.**
- Lineup counterfactual: 0 labels, blocked by G1–G4.
- Matchup probability: forecasts are logged, but the release cohort is empty by construction (B2).
- Waivers: no outcome label exists at all. `docs/decision-science.md:87-92` is explicit that reusing the weekly lineup label here would be a modeling error, and it's right.
- Trades: `trade_static_package_asset_points_v1` is fully implemented (`sandlot_trade_outcomes.py`, `sandlot_refresh.py:367-374`) but is a *retrospective package-points delta*, not a recommendation-quality label, and `docs/decision-science.md:117-124` correctly warns the sample is selected on what Zach chose to review.
- Autopsy: measures the manager, not the recommender, and only over ~2 weeks.

### The statistical problem behind the plumbing problem

Even if G1–G5 were all fixed tomorrow, `rolling_affine_gain_v1` (`sandlot_decision_science.py:203-219`) is **not estimable at this sample size**, and this is the deeper issue.

The label is `proposed_total − baseline_total` over full 20-player lineups (`sandlot_receipts.py:793`). The two lineups typically overlap in 15–17 of 20 players, so the label is a difference over ~4 swapped players, each with weekly σ ≈ 15–25 points → σ(label) ≈ 50–60. The feature (`projected_gain`) varies across weeks with σ(x) ≈ 10. Slope standard error ≈ σ(label)/(σ(x)·√n) ≈ 5.6/√n. To pin the slope to ±0.3 you need **n ≈ 350 weeks ≈ 16 seasons**. `MIN_TRAIN_SAMPLES = 8` and `MIN_EVALUATION_SAMPLES = 4` (`:28-29`) will let the report *declare readiness* at n=12 — with a slope whose 95% CI spans roughly ±3.2. The gates are gates on *plumbing sufficiency*, not on statistical power, and nothing in the report surfaces a confidence interval on the fitted slope. If it ever runs, it will confidently report "beats_baseline" or "doesn't" from noise.

**The fix is a change of unit, not a change of model.** Every ingredient for a 40× larger dataset is already persisted: the receipt's `projection_inputs` block holds a per-player projection for the **whole roster**, not just the starters (`sandlot_receipts.py:342`, `_normalized_entry` at `:1029`), and the archive holds per-player realized `period_fpts` (`fantrax_data.py:507`). Joining those gives ~37 (projected, realized) pairs per week — **~800/season** — with which you can actually estimate bias, shrinkage, and heteroskedastic variance. See §4.1.

---

## 4. Where ML genuinely helps, and where it is theater

Grounded in what exists. Realistic 2026-season sample: **~22 team-weeks, ~24 matchups, ~800 player-weeks, ~5,000 player-games** (the MLB game logs the Monday runner already pulls). Team-week is the binding constraint; player-game is not scarce at all. Ranked by value ÷ effort.

### DON'T BUILD — use these instead

**D1 — Do not build a player projection system. Use one.** This is the most valuable sentence in this report. A from-scratch model would have to beat Marcel — `(5·yr1 + 4·yr2 + 3·yr3)/12`, regressed toward league mean by a playing-time-dependent constant, with a flat age adjustment. Marcel is ~15 lines of code, has no scouting input, and is *hard* to beat; Steamer/ZiPS/THE BAT beat it by only a few percent of RMSE. **Sandlot's current projection — season FP/G with no shrinkage, no aging, no park, no regression — is materially worse than Marcel.** Two concrete options:
- *Cheapest real win:* implement Marcel-style shrinkage on the existing per-game logs. `blended_rate` (`sandlot_lineup.py:51`) becomes `(w_r·recent·n_r + w_s·season·n_s + k·league_mean_for_position) / (w_r·n_r + w_s·n_s + k)`, with `k` fit once on last season's data. ~30 lines, kills the entire §2.3 failure class, no new dependency.
- *Best accuracy per unit effort:* ingest a public rest-of-season projection (FanGraphs Depth Charts / Steamer RoS is downloadable as CSV; THE BAT X is on FanGraphs), convert the projected rate stats through `sandlot_scoring.game_points`, and use it as the rate. You get playing-time projections, park factors, aging and regression for free. The work is name→`player_id_map` matching, which `mlb_stats.resolve_player_identity` already largely solves. **This single change fixes §2.2a, §2.3, replacement level, park factors, and aging simultaneously.**

**D2 — Do not build a trade valuation model.** ~a handful of graded offers per season. Even the outcome label the code already computes (`trade_static_package_asset_points_v1`) is explicitly acknowledged as structurally selected (`docs/decision-science.md:117-124`). Use dynasty consensus rankings (FantasyPros / KeepTradeCut export) as a prior and keep the deterministic FP/G delta as a *this-week* component. Fixing the 2-for-1 flaw (§2.1) is a heuristic change, not an ML problem: value the *marginal* players displaced from the roster, i.e. `Σ get − Σ give − Σ(replacement-level for slots consumed)`.

**D3 — Do not build a win-probability model from logged matchups.** 24 samples/season against a 40-matchup gate. The right move is analytic: fix the variance term (§2.6) from player game-log variance, which is a ~10,000-sample estimation problem you can solve today, and let the normal approximation do the rest. A learned win-prob model would need thousands of matchups.

**D4 — Do not fit `rolling_affine_gain_v1` at the team-week level.** §3 shows it needs ~16 seasons. Keep the contract (it is good) but change the unit.

**D5 — Do not add an LLM anywhere in the numeric path.** The current boundary — deterministic decides, model explains, output cached and hash-bound — is correct and is the main reason this codebase is trustworthy. `sandlot_api.py:663-786` + STATUS.md:52-61 record a real incident where the model invented weekly/ROS totals from FP/G and the response was to *constrain the model further*, not to trust it more. Keep doing that.

### BUILD — ranked by value ÷ effort

**#1 (highest value, lowest effort) — Player-week projection calibration.** Join receipt `projection_inputs` to archived `period_fpts`; you get ~800 pairs/season. Measure bias by position, by projection magnitude, by exposure bucket, by injury state. Fit a one-dimensional isotonic or affine shrinkage on projected → realized. This is enough data to *actually estimate*, it directly improves every downstream number, and it reuses the existing lineage/leakage machinery. Prerequisite: unblock G1–G4.

**#2 — Per-player weekly variance from game logs.** ~5,000 player-games already fetched. Replace `max(1.0, abs(fppg)) * games` (`sandlot_matchup.py:2294`) with a per-role empirical variance. Fixes §2.6, unblocks meaningful probability *and* makes the `win_probability_delta` recommendation gate mean something. Two days of work; no new data.

**#3 — Playing-time share in the matchup model.** Not ML — port `sandlot_lineup.expected_games`'s `share` (`:91`) into `sandlot_matchup._games_remaining`. Fixes the most consequential wrong-recommendation path (§2.2a). Half a day.

**#4 — Reliever appearance cadence.** Not ML — `expected_games` already implements it for non-starter usage (`sandlot_lineup.py:88`). Porting it into `sandlot_pitcher_opportunities` unblocks B2 and removes the permanent 6-unmodeled-pitcher floor. Half a day, and it is the *only* thing standing between the calibration harness and being able to collect a release cohort at all.

**#5 — Two-start-SP and reliever-role detection as an explicit waiver feature.** The autopsy already found this is where the league's mispricing is (STATUS.md:141-144). Deterministic: `expected_starts ≥ 2` from posted probables + cadence, and `hold/save opportunity rate` from game logs. Rank waivers on **expected week points** (`rate × expected appearances`) rather than FP/G — `sandlot_win_week._weekly_candidate_ceiling` (`:1075`) already does this in one surface; promote it into `sandlot_waivers.build_waiver_cards`.

**#6 — Injury/news classification from text.** Genuinely ML-shaped, genuinely useful (it's the biggest human advantage per §2.8), and `docs/decision-science.md:80-81` already sketches the right contract. But it's a data-acquisition project first (you have no news feed), and a small fine-tuned classifier over MLB transaction text is a weekend of work only *after* the feed exists. Defer.

**#7 — Anything else learned.** Not until #1 has produced a season.

### What "theater" would look like here
A gradient-boosted model on 22 team-weeks. A neural net on 800 player-weeks with 6 features. An LLM asked to "estimate this player's rest-of-season value." An embedding of player names. Any model whose training set is smaller than its hyperparameter search. The repo has avoided all of these; `docs/…-model.md:59-63` explicitly says so. Keep that.

---

## 5. What is actually holding autonomy back

### (a) Deliberate safety gates the maintainer chose — these are correct, keep them

- `writes_enabled: False` asserted at every boundary (`sandlot_win_week.py:215`, `sandlot_matchup.py:1547+`, `sandlot_execution.py:203,397`).
- `SANDLOT_EXECUTION_DRY_RUN_ENABLED` feature flag + distinct SHA-256 owner/runner credential digests (`sandlot_execution.py:85-102`).
- `prepare_dry_run_request` refuses any contract not marked `executable: False, writes_enabled: False` (`:124`).
- Movability: Fantrax `disableLineupChange` **plus** MLB game-start timing, with unknown → blocked (`sandlot_matchup.py:1294,1685-1750`).
- Protected assets: IL/IR/MIN never droppable (`sandlot_waivers.py:56-62,728`), a hard-coded never-drop list (`:39`).
- Fail-closed slot provenance: no advice at all unless `slot_source` is trusted (`sandlot_matchup.py:2167`, `run_monday_lineup.py:147-163`).
- Owner bearer never reaches the browser (loopback-only bridge, `sandlot_owner_bridge.py`).

All of these are proportionate. None of them is what is actually blocking autonomy.

### (b) Missing data / feedback — **this is the real blocker**

1. **Zero outcome labels.** G1–G4 (§3). Nothing has ever been scored.
2. **~15 days of roster history** (G5, `sandlot_db.py:2204`). The system structurally cannot accumulate the thing it needs.
3. **No playing-time data in the matchup model** (§2.2a) — the input most likely to make an autonomous move wrong.
4. **No news/injury feed.** The most common reason a lineup decision is wrong is information that isn't in the snapshot.
5. **6+ pitchers per matchup contributing a known-zero floor** (§2.5).

### (c) Genuine technical gaps

1. **The Selenium write path does not work.** STATUS.md:151 — "the Selenium layer failed safe (`player_row_not_found`) and needs a click-flow rewrite." PR #63 is still a draft. There is currently **no working code that can change a Fantrax lineup**, so "autonomy" is not one flag away.
2. **Railway tokens unset** (STATUS.md:228) — executor endpoints 503.
3. **Multi-step chains are unproven.** `_lineup_bundle` marks multi-change plans research-only because Fantrax atomicity is unknown (`sandlot_win_week.py:677-681`). A partially-applied 3-move chain is a genuinely bad state and nothing can currently roll it back.
4. **B2 makes the probability gate unsatisfiable**, so the gate that was supposed to license probability-based action deltas can never open.
5. **G4 (period-date join)** silently drops any non-Monday-aligned Fantrax period from the learning loop.

### (d) Trust / evaluation gaps

1. No demonstrated skill on **any** metric. Not "insufficient sample" — literally zero scored samples.
2. The `rolling_affine_gain_v1` gates measure plumbing, not power (§3), so passing them would not establish skill.
3. The two projection systems have never been reconciled against each other (§1c), and B3 guarantees they disagree by ~4% before any modeling difference.
4. Even the *autopsy* — the one thing with real production output — reports the manager's efficiency, not the recommender's.

### What would have to be measurably TRUE before an unsupervised lineup change

I'd set these, in order, and I'd make the first three prerequisites for even discussing the rest:

1. **Labels exist.** ≥12 consecutive Fantrax periods scored end-to-end with no manual intervention, and `coverage_report.label_coverage_rate ≥ 0.90` (`sandlot_decision_science.py:175`). Requires fixing G1–G5.
2. **Player-week calibration.** On ≥400 player-weeks: |mean bias| < 5% of mean projection, and no bucket (by position, by exposure decile, by projection decile) biased more than 10%. This is the check that catches §2.2a and §2.3, and it is achievable in one season.
3. **Variance honesty.** Predicted team-week σ within 25% of realized σ over ≥20 team-weeks. Without this, no probability statement and no probability-gated action is meaningful.
4. **Directional skill on the actual decision.** Over ≥20 scored periods, realized counterfactual gain positive in ≥70% of weeks where projected gain ≥ +10, with the sign test reported alongside a confidence interval. Note this is a *much* weaker and more achievable claim than fitting a slope, and it is the right claim for an autonomy gate.
5. **Reconciliation.** `sandlot_matchup` and `sandlot_lineup` agree within 5% on the same roster/week. Today nothing checks this.
6. **Execution proof.** ≥20 consecutive supervised writes with post-write verification and zero unintended mutations. This is what PR #63 was for and it has never been achieved.
7. **A bounded, reversible action class.** See below.

### Blast radius, honestly

**Small, and the maintainer is over-indexed on it relative to (b) and (c).** In a weekly-lineup league, a wrong autonomous *lineup slot change* costs the difference between two rostered players for one week — realistically 5–25 fantasy points against a ~250-point weekly total, i.e. maybe 2–8% of one matchup, in a 12-team league where a season is ~22 matchups. A bad week costs you roughly one expected win over a season. It is also *fully reversible until the player's game starts* (`player_lock_before_scheduled_game: "0:05"`, `fantrax_data.py:199`), and a subsequent refresh would detect the divergence via `reconcile_lineup_receipt` (`sandlot_receipts.py:399`).

The genuinely irreversible actions are **drops** (a dropped dynasty prospect can be claimed by anyone) and **trades** (permanent). The code already draws exactly this line — phased vocabulary `move_to_il`/`change_slot` first, adds later, drops maybe never (STATUS.md:396-400). That instinct is right.

So: the risk asymmetry argues for letting *slot changes* go autonomous much earlier than the current posture implies, provided (b) and (c) are fixed — while keeping drops and trades human-gated permanently. The thing that should scare the maintainer is not "a wrong slot change" but "a slot change made from a projection that thinks a platoon hitter plays every day," which is a *model* problem, not a *permissions* problem.

### Is the dry-run/receipt architecture the right foundation?

**Yes, with three amendments.** The immutable, hash-bound, versioned receipt with a separate outcome ledger is exactly the right primitive, and the insistence that owner intent, observed ownership transfer, and verified execution are three different facts (`docs/decision-science.md:102-105`) is the kind of distinction most systems never make. Keep it. Amendments:

1. **Decouple receipt periods from calendar weeks.** Bind the receipt to the *Fantrax period identity* (number + start + end), not `coming_week()`. This fixes G4, makes extended periods scoreable, and removes an entire class of silent misses. This is the single highest-leverage change in the whole report and it is small: `run_monday_lineup.py` already has the snapshot; read `matchup.period_number`/`start`/`end` from it instead of computing Mon..Sun.
2. **Make the evidence extractor incremental and retryable.** Today one raise anywhere in `_completed_period_participation` (`fantrax_data.py:697-790`) loses a period permanently, because only `latest_completed` is ever attempted (`:2536-2543`). Persist per-day participation rows as they succeed, keep a backlog of unarchived completed periods, and let failures retry. Without this, labels will keep evaporating even after G1 is behind you.
3. **Raise snapshot retention for the analytics path.** Either keep 200 snapshots (~3 months) or, better, archive the fields the autopsy needs into a durable table the way receipts already are. `keep=30` counted-not-dated (`sandlot_db.py:2204`) is quietly capping the entire learning program at two weeks.

One more: the execution ledger and the receipt ledger are deliberately separate (`docs/ARCHITECTURE.md:74-77`), which is correct — but for autonomy you will eventually need a *third* fact recorded, "Sandlot initiated this write," distinct from both intent and observation. Design that slot now, while nothing is writing, rather than retrofitting it later.

---

## Appendix — claim/evidence index

| # | Claim | Status | Evidence |
|---|---|---|---|
| 1 | No LLM in any projection/optimizer path | VERIFIED | grep of `sandlot_skipper` imports; `sandlot_matchup.py`, `sandlot_lineup.py` have none |
| 2 | Variance = mean assumption; σ ~2.5–3× too small | VERIFIED (code) / reasoned (magnitude) | `sandlot_matchup.py:2294`; probe → σ(margin)=35.3 |
| 3 | Hitter opportunities ignore playing time | VERIFIED | `sandlot_matchup.py:2173-2190` vs `sandlot_lineup.py:91` |
| 4 | No sample-size shrinkage anywhere | VERIFIED | `sandlot_waivers.py:318-337`; `sandlot_trades.py:799-813`; `sandlot_lineup.py:51-60` |
| 5 | Relievers permanently unmodeled → completeness never `"complete"` | VERIFIED (executed) | `sandlot_matchup.py:151-158,2525`; probe output |
| 6 | Probability release gate structurally unreachable | VERIFIED (executed) | `sandlot_matchup.py:491-496`; probe `release_eligible: False` |
| 7 | Live matchup can log a 0.0/1.0 probability | VERIFIED (code path) | `sandlot_matchup.py:164-165, 2301-2302`; no test |
| 8 | Trade grade = raw FP/G sum, no package-size/slot normalization | VERIFIED | `sandlot_trades.py:799-813, 51-61` |
| 9 | `_weak_positions` has no league-relative baseline | VERIFIED | `sandlot_waivers.py:512-530` |
| 10 | SP and RP compared as one rate group | VERIFIED | `sandlot_waivers.py:431, 661-667` |
| 11 | Archive impossible before 2026-07-12 | VERIFIED | `fantrax_data.py:193-206, 534-536` |
| 12 | Receipt/evidence join on exact period dates | VERIFIED | `sandlot_db.py:918-920` vs `sandlot_receipts.py:356-357` |
| 13 | Multi-window periods permanently ineligible | VERIFIED | `fantrax_data.py:544, 588-596`; `sandlot_db.py:934` |
| 14 | One raise loses a period forever | VERIFIED | `fantrax_data.py:2536-2552`, only `latest_completed` attempted |
| 15 | Retention = 30 snapshots ≈ 15 days | VERIFIED | `sandlot_db.py:2204-2221`; `sandlot_refresh.py:117` |
| 16 | Monday runner recent-exposure denominator includes unplayed games | VERIFIED | `run_monday_lineup.py:72-94, 202` vs `mlb_stats.py:312-317` |
| 17 | `pitcher_proj` includes hitting points | VERIFIED (code); SUSPECTED (correctness) | `run_monday_lineup.py:256-257`; `sandlot_scoring.py:10-12` |
| 18 | Affine gain calibration needs ~16 seasons | Reasoned from label construction | `sandlot_receipts.py:793`; `sandlot_decision_science.py:28-29` |
| 19 | Unbounded `1 << len(two_way)` | VERIFIED | `sandlot_lineup.py:215`; `sandlot_autopsy.py:255` |
| 20 | No working Fantrax write path exists | VERIFIED (documented) | STATUS.md:151, 228 |
| 21 | Full test suite green, 583 tests | VERIFIED (executed) | `unittest discover` in worktree |
| 22 | CLAUDE.md model order is stale vs code | VERIFIED | `sandlot_skipper.py:33-34` (deepseek primary / kimi fallback) vs CLAUDE.md |
