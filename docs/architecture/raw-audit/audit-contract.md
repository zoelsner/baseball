# Sandlot audit — API↔UI contract, test coverage, frontend state, security, maintainability

Worktree: `.../scratchpad/main-audit` @ `bd54135` (origin/main).
All paths below are relative to that worktree root.

---

## Correction to the brief (read this first)

The brief describes a no-bundler, in-browser-Babel frontend with `window.*` globals and mock-data
fallbacks. **`main` no longer works that way.** Verified:

- `package.json:6` — `build:sandlot` runs esbuild: `esbuild web/sandlot/main.jsx --bundle --minify --format=iife --outfile=web/sandlot/app.js`.
- `web/sandlot/index.html:59` — a single `<script src="app.js?v=frontend-build" defer>`. No CDN React, no `@babel/standalone`.
- `web/sandlot/main.jsx:1-6`, `v2-pages.jsx:4-11`, `atoms.jsx:113-117` — real ES `import`/`export`. Zero `window.X = ...` assignments.
- `AGENTS.md` "Frontend Rules" and the worktree `CLAUDE.md` both document the bundler; CI enforces bundle freshness (`.github/workflows/ci.yml:69-71`, `git diff --exit-code web/sandlot/app.js`).

So the toolchain constraint the brief asked me to respect no longer exists, and none of my findings
depend on it. Two stale artifacts remain from the old world and are worth deleting:

- `tests/playwright/fixtures/sandlot.ts:4-6` — comment still says "Sandlot is a no-bundler SPA: index.html pulls React + Babel from CDNs and transpiles every .jsx file in-browser".
- `tests/playwright/specs/smoke.spec.ts:57-59` — console-error filter still whitelists `/babel/i`. Harmless, but it means a real Babel-named error would be swallowed.

Only two CDN `<script>`-class resources remain, both Google Fonts stylesheets
(`index.html:10-12`), no SRI. Assessed in §4.

The bundler migration landed in `0f137ff` "cleanup: add sandlot frontend build (#54)". Mock data was
removed separately in `e2ac912` "[codex] Remove production mock data paths (#52)", and
`data.jsx`/`data2.jsx` no longer exist — so the "compare mock data against the real API" sub-task is
moot as stated. The residual fallback paths I did find are in §1c, and one of them (§1b-3) is a live
defect.

**Where the bad brief cost me nothing:** I read `AGENTS.md` and the worktree `CLAUDE.md` first, saw the
contradiction immediately, and audited the tree as it is. No finding below is premised on the
Babel/`window.*` model. **Where it cost me something:** I spent ~10 minutes chasing `data.jsx`/`data2.jsx`
before confirming they were deleted, and I initially treated the CDN font links as a bigger surface
than they are. Discount nothing; §6 (committed-bundle drift) is the item the corrected brief added and
it is now covered.

---

## 1. The API↔UI contract

Method: extracted every snake_case property read in `web/sandlot/v2-pages.jsx` (129 distinct keys),
cross-checked each against the producing Python module, then traced nesting by hand for every
`fetch()` consumer. Nine endpoints are consumed by the UI; six more exist and are consumed only by
external agents or not at all (see §2).

### 1a. Verified-correct contracts (no action needed)

These were traced on both sides and match by name, nesting and type. Listing them so the next
reviewer doesn't redo the work.

| Surface | JSX reads at | API sets at | Status |
|---|---|---|---|
| `/api/snapshot/latest` envelope (`snapshot_id`, `taken_at`, `freshness`, `team_id`, `team_name`, `roster`, `roster_meta`, `standings`, `matchup`, `win_this_week`, `data_quality`, `player_index`) | `v2-pages.jsx:194-229` | `sandlot_api.py:1286-1304` | **VERIFIED OK** |
| Standings row (`team_id`, `team_name`, `owner`, `rank`, `fantasy_points`, `win`, `loss`, `tie`, `streak`) | `v2-pages.jsx:197-207` | `fantrax_data.py:1786-1797` | **VERIFIED OK** |
| Matchup block (`my_score`, `opponent_score`, `margin`, `opponent_team_name`, `period_number`, `start`, `end`, `days`, `complete`, `latest_completed`) | `v2-pages.jsx:773-818` | `fantrax_data.py:1995-2014`, `latest_completed` at `fantrax_data.py:1873` | **VERIFIED OK** |
| Projection (`projected_my`, `projected_opp`, `win_probability`, `probability_calibrated`, `complete`, `drivers.*`, `pitchers_with_cadence_estimate`, `pitchers_without_opportunity_model`) | `v2-pages.jsx:820-855, 875-876, 5127-5159` | `sandlot_matchup.compute_projection` | **VERIFIED OK** (see 1b-6 for the completed-matchup branch) |
| `win_this_week` plan (`state`, `actions[]`, `no_action.alternatives`, `handoffs.lineup.{url,label,method,read_only}`, `summary.{headline,outlook,projection_caveat,projected_margin_before_action,projected_margin_after_action,win_probability_excluded_reason}`, `planning_horizon.{mode,period_number}`, `monitoring_actions`) | `v2-pages.jsx:1222-1440` | `sandlot_win_week.py:209-241`, `_summary` at `:~1160`, `_fantrax_handoffs` at `:390` | **VERIFIED OK** |
| Action `review` contract (`state`, `proposal_id`, `input_hash`, `snapshot_id`, `target_period.*`, `slot_moves[]`, `contract.confirmation.expected`) | `v2-pages.jsx:1481-1551` | `sandlot_win_week.py:587-616` | **VERIFIED OK** |
| Replacement card (`move_in`/`move_out.{name,positions,team,from_slot,to_slot,fppg,remaining_games,slot_source}`, `projected_benefit.{points,probability_calibrated}`, `movability.{state,label,reason}`, `execution`, `blocked_reason`, `proposal.safety_checks[]`, `provenance.*`, `risk_label`, `short_term_outlook`) | `v2-pages.jsx:2504-2596, 2730-2777` | `sandlot_matchup.py:1160-1226` (`_lineup_replacement_card`), `_player_card_summary`, `_lineup_swap_proposal` at `:1245-1291` | **VERIFIED OK** |
| Waiver card (`cards[].{id,rank,net_delta,confidence,add,move_out,evidence_chips,why,risk,dynasty_note,fills_position}`, player `fpg`) | `v2-pages.jsx:3403-3477` | `sandlot_waivers.py:476-501` | **VERIFIED OK** — note it is `fpg`, not `fppg`, on both sides |
| `data_quality` (`projection_ready`, `recommendations_ready`, `lineup_recommendations_ready`, `lineup_slots.{state,reason}`, `*_reasons`, `reasons`) | `v2-pages.jsx:272-302` | `sandlot_data_quality.py:105-136` | **VERIFIED OK** |
| Player profile (`player.*`, `mlb.{available,mlb_id,reason}`, `group`, `season`, `trend.direction`, `sparkline`, `games[]`, `take.{text,error}`, `profile_cache.take.{state,pending}`) | `v2-pages.jsx:4427-4534, 4602-4740` | `player_service.py:74-93, 109-126, 277-291`; game rows at `mlb_stats.py:533-590` | **VERIFIED OK** |
| Game-log row (`date`, `opponent`, `home`, `line`, `avg_game`, `fpts_estimated`, `ab/h/hr/rbi/bb/k/sb`, `ip/er/win/save`) | `v2-pages.jsx:4711-4733, 4950-4969` | `mlb_stats.py:558-565, 579-589` | **VERIFIED OK** |
| Receipt (`receipt_id`, `input_hash`, `period.{start,end}`, `evaluation.projected_gain`, `baseline_assignment`, `proposed_assignment`, `decision_state`, `expires_at`, `reconciliation.*`, `trade.{give,get}`, `action_type`, `writes_enabled`, `fantrax_changed`) | `v2-pages.jsx:1726-1868, 3847-3933` | `sandlot_api.py:1504-1583` | **VERIFIED OK** |
| Learning report (`summary.{scored,accepted_and_observed,average_counterfactual_gain}`, `evidence_checkpoint.requirements[].{key,current,required,passed}`, `autopilot.state`, `autopilot_eligible`) | `v2-pages.jsx:1975-2048` | `sandlot_api.py:1641-1708` | **VERIFIED OK** |
| Trade grade (`analysis.{recommendation,recommended_counter,roster_fit,horizons,skipper_prompt}`, `letter_grade`, `fairness`, `my_delta`, `their_delta`, `age_delta`, `headline`, `my_weakest_position`, `rationale`, `model`, `cached`, `no_counter_reason`, `counters[]`, `receipt`, `my_give`, `my_get`) | `v2-pages.jsx:3768-4021` | `sandlot_trades.py` (all 22 keys present) | **VERIFIED OK** |
| Incoming trades (`snapshot_id`, `freshness`, `offers[].{trade_id,proposed_by,give,get,gradeable,blocked_reasons,includes_draft_pick,manual_review_reason,manual_review,status}`, `read_only`, `writes_enabled`, `fantrax_changed`) | `v2-pages.jsx:4091-4187` | `sandlot_api.py:673-776` | **VERIFIED OK** |
| Skipper history (`messages[].{role,content,metadata.sources,metadata.sources_available,metadata.web_search_requested}`) and SSE frames (`token`/`replace`/`sources`/`done`/`error`) | `v2-pages.jsx:5344-5478` | `sandlot_api.py:817-831, 947-1055` | **VERIFIED OK** |
| Team roster (`rows`, `team_name`) | `v2-pages.jsx:3040-3062` | `sandlot_api.py:869-883` | **VERIFIED OK** (shape only — zero tests, see §2) |

### 1b. Contract defects

| # | Field | JSX reads at | API sets at | Status / symptom |
|---|---|---|---|---|
| **1** | `snapshot_freshness.state` (player sheet) vs `freshness.state` (top bar) | `v2-pages.jsx:4427-4431` | `player_service.py:535-547` (30 min / 24 h) vs `sandlot_api.py:42-43, 1454-1467` (18 h / 36 h) | **VERIFIED — same input, two answers.** Both derive from `snapshots.taken_at`, both render as a coloured dot in the same app. With the documented cron cadence (`0 13,21 * * *` UTC, twice daily), the snapshot is >30 min old for ~97% of the day, so the **player sheet shows an amber "stale" dot essentially always, while the Today top bar shows green "Healthy" for the identical snapshot.** At 25 h the sheet goes red "old" while Today still says amber "stale". |
| **2** | `freshness` on `/api/waiver-swaps/latest` | not read by JSX | `sandlot_waivers.py:894-905` (30 min / 24 h) | **VERIFIED — third freshness implementation.** Unread by the UI, but it *is* embedded in the AI refresh-brief prompt (`sandlot_waivers._refresh_prompt_context`, `"freshness": payload.get("freshness")`). Skipper is therefore told "stale" while the UI says "fresh". Three implementations, two threshold sets, one source of truth. |
| **3** | `data.media.items` / `data.clips` → placeholder clips | `v2-pages.jsx:4531`, `4761-4763` | `player_service.py:85, 126` | **VERIFIED reachable — fabricated content.** `V2ProfileClips` falls back to `V2_PROFILE_PLACEHOLDER_CLIPS` (`v2-pages.jsx:4755-4759`) whenever `clips` is not an array. `data.clips` is a name the API has *never* produced (naming drift). The API currently always sets `media.items`, **but the repo's own test proves the fallback path is live**: `tests/playwright/specs/player-sheet.spec.ts:41-53` mocks `/api/player/...` without a `media` key, so that test renders three invented items to the user — including a fake Dave Roberts quote (`"He's our guy back there."`) and a fake highlight ("RBI double vs SF"). Any API response that omits `media` (an older cached payload, a partial 200, a future refactor) puts fabricated quotes on screen in a product whose entire posture is evidence-honesty. |
| **4** | `model.leagueName` | `v2-pages.jsx:638, 642` | never produced | **VERIFIED — visible cosmetic defect.** `leagueName` is hardcoded `''` at `v2-pages.jsx:83` and `:218`; `_snapshot_payload` returns `league_id` but no league name. The League tab eyebrow renders `` `${model.leagueName} · ${model.leagueTeams.length} teams` `` → a stray leading " · 12 teams". The Settings eyebrow renders an empty string. |
| **5** | `payload.snapshot` on a 200 `/api/refresh` | `v2-pages.jsx:414` | `sandlot_api.py:593` (`_snapshot_payload(row) if row else None`) | **VERIFIED — silent false-green.** `v2NormalizeSnapshot(undefined)` does not throw; it returns a fully-formed empty model whose `sync.state` defaults to `'fresh'` (`v2-pages.jsx:211`) and `sync.label` to `'fresh'` (`:239`). `acceptSnapshot` then replaces a good model with it. User sees: green "Healthy" dot, `0 players`, and the garbled string `"Snapshot fresh old."` (`v2-pages.jsx:2161`). Trigger: refresh succeeds but `latest_successful_snapshot()` returns None on the follow-up read. Narrow, but the fix is one guard in `v2FetchRefresh`. |
| **6** | `projection.opportunity_completeness` / `pitchers_*` on a completed matchup | `v2-pages.jsx:875-876` | `sandlot_matchup.compute_projection`, completed branch omits them | **VERIFIED benign.** The `complete: True` return omits every `pitchers_*` key. `v2Number(undefined)` → `0` (`v2-pages.jsx:231-235`), and `v2ProjectionContext` is only reached when `showProjection` is true, which requires `!complete`. No symptom today; it is a live tripwire if anyone relaxes that gate. |
| **7** | `t.points_for`, `matchup.days_left` | `v2-pages.jsx:202, 793` | never produced | **VERIFIED dead fallbacks.** Both are `??`/`||` alternates behind keys the API does produce (`fantasy_points`, computed `daysLeft`). No symptom; noise. |
| **8** | Unguarded deep access | — | — | **Swept, clean.** Every `a.b.c` chain in the 5,705-line file either uses `?.` or is preceded by a truthiness guard. The one raw `.split()` on an unguarded string is `Avatar` (`atoms.jsx:66`, `name.split(' ')`), and all five call sites pass a defaulted value (`v2-pages.jsx:2792, 2937, 3467, 4556`). No `NaN` reaches the screen: every numeric render goes through `v2Number` (`:231`), `v2Signed` (`:3508`, returns `'—'` for non-finite) or `v2FormatMetric` (`:767`). |

### 1c. Mock/fallback divergence

There is **no mock-data module** any more (`data.jsx`/`data2.jsx` are gone; `AGENTS.md` forbids them).
The remaining fallback data is:

- `V2_PROFILE_PLACEHOLDER_CLIPS` (`v2-pages.jsx:4755`) — defect 1b-3 above.
- `V2_SKIPPER_MODELS` / `V2_SKIPPER_DEFAULT_MODEL` (`v2-pages.jsx:43-49`) — a hardcoded copy of the model list that `/api/skipper/options` returns (`sandlot_api.py:791-796`). Currently identical. Used silently when `/api/skipper/options` fails (`v2-pages.jsx:109, 5315`). **SUSPECTED drift risk:** when the server changes primary model, the client keeps offering the old list on any options fetch failure, and `chatModel` state initialises to the *hardcoded* default (`:5270`) before the fetch resolves.
- The real divergence risk has moved into the **Playwright route mocks** — see §2.

---

## 2. Test coverage — where the 573-test suite is blind

### 2a. Suite shape (measured)

- 40 Python `unittest` files, ~500 `def test_` methods, 15,956 LOC.
- 12 Playwright specs, 47 `test(...)` blocks, 2,902 LOC.
- CI: `ci.yml` runs import-smoke + the full unittest suite with a real Postgres service. `playwright.yml` runs two jobs.

### 2b. Routes with **no test at all**

Traced by grepping every route literal across `tests/*.py` and `tests/playwright/specs/*.ts`.

| Route | Coverage | Note |
|---|---|---|
| `GET /api/team/{team_id}/roster` (`sandlot_api.py:856`) | **NONE** | The League→team drill-down. Zero Python tests, zero Playwright. `league.spec.ts:27` opens the overlay but does not mock or assert the response. |
| `POST /api/player/{fantrax_id}/refresh` (`sandlot_api.py:851`) | **NONE** | The "Sync" button in the player sheet. |
| `GET /api/matchup-probability-readiness` (`sandlot_api.py:285`) | **NONE** | ~75 lines of arithmetic (`int(current.get(...) or 0)`, `max(0, ...)`) with no test. |
| `GET /api/action-proposals/{proposal_id}` (`sandlot_api.py:396`) | **NONE direct** | `_latest_reviewed_action` is exercised indirectly via `/api/execution-requests` in `test_sandlot_execution.py`. |
| `DELETE /api/skipper/messages` (`sandlot_api.py:897`) | **NONE** | |
| `GET /api/skipper/options` (`sandlot_api.py:787`) | **NONE server-side** | Playwright mocks it in 5 places; the handler itself is never called. |
| `POST /api/refresh` (`sandlot_api.py:536`) | **NONE server-side** | Playwright mocks it in 6 places. The 409/502 fallback-payload branches (`:543-576`) — the most fragile error contract in the app, and the one behind defect 1b-5 — are never executed by any test. |
| `GET /api/health` (`sandlot_api.py:124`) | **NONE** | The only no-DB-friendly probe, and the readiness gate for the whole Playwright deploy job (`playwright.yml:56-70`). `tests/test_sandlot_readonly_monitor.py:97` uses `"/api/health"` as a fixture dict key, not a request. |
| `GET /api/player/{fantrax_id}` (`sandlot_api.py:834`) | **mock-only** | `player-sheet.spec.ts:41` fully intercepts it. The handler's background-task logic (`:836-848`) never runs under test. |

Covered: `/api/snapshot/latest`, `/api/attention`, `/api/hot-swaps/latest`, `/api/win-this-week/latest`,
`/api/waiver-swaps/latest`, `/api/trades/{grade,incoming}`, `/api/recommendation-*`,
`/api/execution-requests*`, `POST /api/skipper/messages`.

### 2c. Is the frontend meaningfully tested?

**Yes — considerably better than the brief assumed.** This is not a happy-path smoke suite. The four
large specs are route-mocked deterministic behaviour tests with real assertions on user-visible copy
and state:

- `today-attention.spec.ts` (779 LOC, 13 tests) — queue ordering by consequence; extended-period label; empty state; rejected-alternative explanations; **expired-deadline blocking**; **stale-snapshot waiver blocking**; **old-snapshot handoff blocking**; period-mismatch pause; future-period plan labelling; deadline-triggered silent refetch; untrusted-slot-provenance pause; missing-readiness pause.
- `recommendation-receipt.spec.ts` (502 LOC, 13 tests) — bridge-offline honesty; partial reconciliation; empty learning gate; hindsight-not-lift labelling; sanitized Skipper draft; failure announcement; **two explicit response-ordering races** ("does not let an older receipt response overwrite a newer refresh", "does not let an in-flight decision read revert a committed acceptance"); stale-decision 409 handling.
- `trade.spec.ts` (497 LOC, 11 tests) — one-click exact incoming offer; draft-pick manual review; dynasty-policy block copy; **queued-research cancellation on navigation**; **in-flight grade discarded on snapshot refresh**; expired/mismatched receipt blocking.
- `today-projection.spec.ts` (196 LOC, 7 tests) — high/low probability rendering, completed-matchup hiding, uncalibrated labelling, daily-pace anchoring, cadence-estimated pitcher disclosure.
- `today.spec.ts` (7 tests) — unavailable-snapshot honesty, non-green dot on old data, focus refetch uses GET, age ticking, single-flight refresh, scroll reset.
- `skipper-web-fallback.spec.ts` (4 tests) — `web_search` flag propagation, toggle hiding, source provenance restored from history, unverified labelling.

That is genuinely good coverage of the *decision-safety* surface. Two structural gaps remain:

1. **The strong specs assert against hand-written mocks, so they are a second copy of the contract, not a check on it.** `player-sheet.spec.ts:41-53` is the proof: its mock omits `media`, so the test renders the fabricated placeholder clips (defect 1b-3) and asserts nothing about them.
2. **On a pull request, almost none of this runs against the real app.** `playwright.yml:80-90`: PRs run *only* `specs/attention-api.spec.ts` against the deploy; the full suite runs on push-to-main and the daily 14:30 UTC cron. The `local-frontend-e2e` job (`:100-176`) serves the static bundle with `python3 -m http.server` — **no API server at all** — and explicitly excludes the two real-API Today tests via `--grep-invert`. So PR-time frontend validation is entirely mock-driven; real-API regressions are caught after merge.

### 2d. Does anything test the API↔UI contract?

**Only for two routes the UI does not consume.** `tests/playwright/specs/attention-api.spec.ts` is a
proper structural contract test — enumerated `kind`/`severity` values, monotonic `priority`, chip
count bounds, exhaustive key checking on action payloads (`:44-46`), `writes_enabled === false`
invariants. It is the best test in the repo. It covers `GET /api/attention` and
`GET /api/hot-swaps/latest`, **neither of which appears in any `fetch()` in `v2-pages.jsx`.**

There is no equivalent for `/api/snapshot/latest`, which is what the UI actually renders. Python-side,
`_snapshot_payload` is called in four tests (`test_sandlot_matchup.py:572`, `test_sandlot_win_week.py:688,750`,
`test_sandlot_data_quality.py:509`, `test_sandlot_projection_logging.py:420`) but each asserts one
sub-branch, not the envelope shape the frontend depends on.

One dangling reference: `attention-api.spec.ts:41` asserts items are "ready to submit to
`POST /api/actions` as-is", and `sandlot_api.py:169` says "Writes stay in POST /api/actions". **That
route does not exist.** Aspirational, not a bug, but it will mislead.

### 2e. Skipped / conditional tests

`STATUS.md`'s claim checks out and is slightly understated.

- **Python:** exactly one skip marker — `tests/test_sandlot_receipts.py:2096`,
  `@unittest.skipUnless(os.environ.get("SANDLOT_TEST_DATABASE_URL"), "requires disposable Postgres")`,
  gating class `RecommendationReceiptPostgresConcurrencyTests` with **two** tests
  (`test_two_outcome_workers_commit_one_identical_result`, `test_waiting_decision_rechecks_wall_clock_expiry_after_row_lock`).
  These *do* run in CI — `ci.yml:15-30` provisions a Postgres service and sets the env var. Locally
  they silently skip. No `expectedFailure`, no `xfail`, no other skip in the Python suite.
- **Playwright:** 12 `test.skip(...)` calls, and these are the ones that matter. Nine are **data-conditional
  self-skips against the live deploy**:
  - `roster.spec.ts:14` — skips if the snapshot has no roster rows.
  - `league.spec.ts:13,41` — skips if standings are empty / no opponent.
  - `adds.spec.ts:36` — skips if the top waiver card has no `add.name`.
  - `today.spec.ts:76` — skips if there is no opponent.
  - `attention-api.spec.ts:14,15,59,60` — skips on 404 **and on 503** ("no successful snapshot (or no DB)").

  **This is the highest-leverage coverage gap in the repo:** the suite goes green precisely in the
  scenarios where production is broken. If the Fantrax scrape starts returning an empty roster, or the
  DB is down so every route 503s, the daily cron Playwright run reports success. The one job whose
  entire purpose is "catch drift between Fantrax scrape runs (e.g. cookie expiry)" (`playwright.yml:36-38`)
  cannot detect exactly that failure.

  Two more are environment gates: `adds.spec.ts:6` (skips the deployed-API test in the local job) and
  `recommendation-receipt.spec.ts:83` (`SANDLOT_EXPECT_RECEIPT !== '1'`). One is a migration guard:
  `league.spec.ts:64`.

### 2f. Honest read on test quality

I sampled `tests/test_sandlot_receipts.py` (2,277 LOC, 66 tests) and spot-checked
`test_sandlot_api_player_index.py`, `test_sandlot_execution.py`, `test_sandlot_win_week.py`.

**These are not fixture-shaped tautologies.** `RecommendationReceiptApiTests` (`:1551`) drives the real
FastAPI app through `TestClient` with only the DB layer patched, and asserts *negative* invariants
that would actually catch a regression: `assertNotIn("recommendation", payload)`,
`assertNotIn("projection_inputs", json.dumps(payload))`, `writes_enabled is False`,
`fantrax_changed is False` (`:1588-1596`). The persistence tests assert idempotency, hash stability
under input reordering (`:366`), fail-closed on non-finite numbers (`:440`), fail-closed on duplicate
player names (`:463`), conflict-on-replay (`:1357`), and "decided receipt cannot be superseded"
(`:1391`). The two Postgres tests exercise genuine two-worker concurrency and post-row-lock wall-clock
re-checks. This is the work of someone who has thought about what breaking looks like.

The one systemic weakness: **every test constructs its own snapshot dict by hand.** There is no
captured-from-production fixture anywhere in `tests/` (no fixture directory, no JSON). So the suite
proves "the code handles the shape the author believed Fantrax emits". If `fantrax_data.py` starts
emitting a different shape — the single most likely real-world failure, since it scrapes a third-party
site — nothing in 500 Python tests notices, and the Playwright tests that *would* notice self-skip.

**Verdict on whether green CI justifies confidence:** green CI justifies high confidence in the
*decision-safety invariants* (no-write boundaries, receipt immutability, provenance gating, stale-data
pausing) — that layer is genuinely well tested and the tests are honest. Green CI justifies **low**
confidence that the app renders correct data, because (a) the only real-API contract test covers two
routes the UI never calls, (b) the frontend tests assert against mocks that are a second copy of the
contract, and (c) the live-data tests skip themselves when data is missing. The suite is strong
where the author feared being wrong and blind where they didn't.

---

## 3. Frontend state and navigation

### 3a. State map (`V2App`, `v2-pages.jsx:418-620`)

Five independent pieces of navigation state:

| State | Line | Values | Cleared by |
|---|---|---|---|
| `page` | `:419` | today / roster / league / fa / trade / skipper / settings | `setPage` |
| `detail` | `:420` | player id or null → `V2PlayerSheet` | close button, backdrop, Escape |
| `leagueTeam` | `:421` | team object or null → `V2TeamRoster` | `setPage(next)` when `next !== 'league'` (`:433`) |
| `authed` | `:422` | bool → `V2Auth` gate | `V2Settings` sign-out only |
| `skipperDraft` | `:425` | `{id,text,autoSend,...}` | `onDraftConsumed` |

Plus `model` / `syncState` and four refs used for concurrency control
(`snapshotReadInFlightRef`, `refreshInFlightRef`, `snapshotRequestSeqRef`, `syncAgeAnchorRef`).

**Unreachable states — dead UI shipped in the bundle:**
- `page === 'settings'` is **unreachable**. `setPage` is never called with `'settings'` anywhere
  (`v2-pages.jsx:579, 592, 602, 686` are the only call sites) and `V2TabBar` (`:674-680`) has no
  settings item. `V2Settings` (`:4234-4283`, ~50 lines) is dead.
- Because Settings is the only caller of `onSignOut`, `authed` can never become false, so `V2Auth`
  (`:701-745`, ~45 lines — a fake magic-link screen with a "(demo) Continue →" button) is also dead.
  `main.jsx:6` renders `initial={{page:'today'}}` with no `auth` key, so it never shows on boot either.
- `page === 'trade'` is reachable only from the League tab (`:602`), never from the tab bar. Intentional per `AGENTS.md`.
- Also dead: `V2MatchupRecommendationCard` (`:922`), `V2HealthSummary` (`:2858`), `V2HealthSection` (`:2906`),
  `V2HealthPlayerRow` (`:2927`, only used by the dead `V2HealthSection`), `V2DecisionCard` (`:2955`),
  `V2PositionCard` (`:3131`). ~340 lines of unreferenced components in a shipped, minified bundle.

**Can states combine inconsistently?** Only one combination matters: `openPlayer` (`:577-581`) sets
`page='roster'` **and** `detail=id` together, so a player link from Skipper chat silently reassigns the
active tab underneath the sheet. Close the sheet and you are on Roster, not where you were. That
matches the documented "#37 not yet built" note in `CLAUDE.md`, so it is intentional-but-jarring, not
a bug. The sheet itself renders outside `pages[page]` (`:617`) as `position:absolute; inset:0; zIndex:10`,
which covers the tab bar, so the user cannot navigate underneath an open sheet. No other inconsistent
combination is reachable.

### 3b. Fetch/navigation races

**Well handled (verified):**
- Snapshot reads and refreshes share a monotonic sequence ref (`:429, 450, 464, 495, 499, 502`), plus
  two in-flight booleans. A silent focus-triggered load is suppressed while a refresh is running (`:448`).
- `V2RecommendationReceipt` uses `receiptReadSeqRef` with cleanup-increment (`:1762, 1769, 1772, 1780, 1794`).
- `V2PlayerSheet` uses `requestSeqRef` on both load and sync (`:4331, 4342, 4352, 4400, 4409`).
- `V2TradeGrader` uses `gradeRequestRef` and discards in-flight grades when `model.snapshotId` changes (`:4072-4089, 4110, 4128`).
- `V2TeamRoster`, `V2FreeAgents`, `V2Skipper` history/options/brief all use `cancelled` flags.
- Two of these races have explicit Playwright tests (`recommendation-receipt.spec.ts:419, 453`).

**Two real defects:**

- **VERIFIED — `V2RecommendationReceipt` polls `/api/recommendation-receipts/latest` once a minute,
  forever.** `v2-pages.jsx:1792-1795`: the effect depends on `sync?.label`. `tickSyncAge`
  (`:525-544`) runs on a 60-second `setInterval` and rewrites `label` via `v2SyncLabel` (`:237-245`),
  which changes every minute for any snapshot under an hour old. So every 60 s the effect tears down
  and refetches while the Today page is mounted. **The team already identified and fixed this exact
  bug class for the sibling component** — `V2RecommendationLearning` depends on `[snapshotId]`
  (`:2021`) and there is a dedicated regression test,
  `recommendation-receipt.spec.ts:283` "does not refetch learning evidence when only the freshness
  label changes". The fix was simply not applied to the receipt. One-word change: `sync?.label` →
  `model.snapshotId` (or lift the snapshot id into the props).

- **VERIFIED — `V2PlayerSheet`'s take-poll has an infinite loop; the 12-attempt cap never applies.**
  `v2-pages.jsx:4365-4396`. `attempts` is a `let` local to the effect body; the effect's dependency
  array is `[id, data]` (`:4396`) and the poll body calls `setData(next)` (`:4383`). Every successful
  poll therefore replaces `data` with a fresh object reference → the effect tears down and re-runs →
  `attempts` resets to 0 → a new 2,500 ms timer. As long as `stillGenerating` stays true
  (`take.text` null, `take.error` null, `profile_cache.take.state === 'missing'`), the sheet polls
  `/api/player/{id}` **indefinitely at ~2.5 s intervals**.
  Amplification: each of those GETs schedules a background
  `player_service.refresh_cached_profile(..., generate_take=True)` task server-side
  (`sandlot_api.py:840-847`). Reachable whenever take generation fails silently — e.g. `OPENROUTER_API_KEY`
  unset or the model erroring in a way that leaves no cached row: `player_service` returns
  `{"text": None, ..., "error": None}` (`:369`) → `_take_cache_state` returns `{"state": "missing"}`
  (`:592`) → the client keeps polling. Result: ~24 req/min and ~24 queued OpenRouter calls per minute
  while a single sheet is open.

### 3c. What the user actually sees on 503/500

Enumerated every failure branch. Error handling is **better than average** — no silent blank screens
from API failures:

| Failure | User sees | Line |
|---|---|---|
| `/api/snapshot/latest` 503/404 | `sync.state='failed'`, red dot, matchup card replaced by the error string ("Snapshot failed (503)"), Attention Queue empty state with the same reason. No auto-retry (deliberate — `:480`). | `:456-489, 2315-2326, 2827-2856` |
| `/api/refresh` 502 with fallback | Last good snapshot restored + amber `V2Caution` "Heads up" with `fallback_reason` | `:503-517, 2226-2228` |
| `/api/recommendation-receipts/latest` error | `V2Caution` "Lineup receipt unavailable" | `:1835-1837` |
| `/api/recommendation-learning` error | `role="alert"` `V2Caution` "Learning report unavailable" | `:2024-2026` |
| `/api/waiver-swaps/latest` error | Full-page "Waiver swaps unavailable" + message | `:3341-3343` |
| `/api/team/{id}/roster` error | `V2Caution` "Roster unavailable" | `:3084-3086` |
| `/api/player/{id}` 500 | "Couldn't load player" card **with a Retry button** | `:4473-4482` |
| `/api/trades/incoming` error | "Incoming Fantrax offers could not be checked. You can still build an offer below." | `:4183-4184` |
| Skipper stream error | Error text, empty AI bubble dropped | `:5469-5478` |
| `/api/skipper/options` failure | **Silent** — falls back to the hardcoded model list | `:5315` |

**The one structural gap: there is no React error boundary anywhere.** No `componentDidCatch`,
`getDerivedStateFromError` or `<ErrorBoundary>` in `main.jsx`, `atoms.jsx` or `v2-pages.jsx`. Any
render-time throw unmounts the whole tree, leaving a blank `#efe8dc` rectangle
(`index.html:36`) with no message and no recovery path except a manual reload. I could not find a
*likely* trigger — the numeric and deep-access hygiene is good — but with 5,705 lines of hand-rolled
render logic consuming an unvalidated third-party scrape, "no likely trigger today" is a weak guarantee.
An error boundary around `pages[page]` and around `V2PlayerSheet` is ~20 lines and converts the worst
case from "blank screen" to "this page failed, reload".

---

## 4. Frontend security surface

Assessed for this app's real threat model: single user, single tenant, unauthenticated read routes by
design, no multi-user data, no session cookie worth stealing.

### 4a. HTML/DOM injection

**Clean.** Zero `dangerouslySetInnerHTML`, zero `innerHTML`, zero `eval`, zero `new Function`, zero
`document.write` in `web/sandlot/*.jsx`. Scraped player and team names reach the DOM only as React
text children, which escapes them. `v2RenderSkipperMarkdown` (`:5049-5115`) parses LLM markdown into
**React elements** (`<p>`, `<ul>`, `<li>`, `<strong>`), never HTML strings — the right design.

One thing worth noting as *correct*: `v2BuildFallbackRegex` (`:4992-4999`) builds a `RegExp` from
scraped player names but escapes every metacharacter first (`V2_REGEX_ESCAPE_RE`, `:4988`), so a
player named `A.J. Pollock` cannot break the pattern.

### 4b. `javascript:` URL in Skipper web sources — the one real finding

**VERIFIED, low-likelihood / trivially fixable.**

`v2-pages.jsx:5692`:
```jsx
<a key={`${source.url}-${index}`} href={source.url} target="_blank" rel="noreferrer" ...>
```

`source.url` originates in `sandlot_skipper._extract_url_citations` (`sandlot_skipper.py:1248-1273`),
which copies the provider's `url_citation.url` verbatim with **no scheme validation**:

```python
url = _obj_get(citation, "url")
if not url:
    continue
sources.append({"url": url, ...})
```

It is then persisted into `chat_messages.metadata.sources` (`sandlot_api.py:1033-1039`) and replayed
from history on every page load (`v2-pages.jsx:5353-5358`). React does not sanitize `href`; it warns
on `javascript:` URLs but still renders them. Web search is **on by default**
(`SkipperMessageIn.web_search: bool = True`, `sandlot_api.py:784`).

Threat model honestly: the attacker must control the provider's citation annotations — either
OpenRouter/the upstream model, or (for providers where the model itself emits annotations) a poisoned
web page the model cites. That is not nothing given web search is on by default, but it is not a
casual attack, and the payload only fires if the single user clicks the link. **Severity: low. Cost to
fix: one line.** Filter at the boundary in `_extract_url_citations`:
`if not str(url).lower().startswith(("http://", "https://")): continue`, and mirror it in
`V2WebSources`.

Note `rel="noreferrer"` (`:5692`) does imply `noopener` in all current browsers, so reverse-tabnabbing
is covered; the other three external anchors correctly use `rel="noopener noreferrer"`
(`:1386, 1686, 1961`). The Fantrax handoff URLs are server-constructed against a hardcoded
`https://www.fantrax.com/` prefix with `quote(..., safe='')` on both ids
(`sandlot_win_week.py:399-402`) — not injectable. `V2ClipViewer`'s `href={url}` (`:4907`) comes from
the MLB API via `player_service`, same unvalidated-scheme caveat but a far less reachable source.

### 4c. `localStorage`

**Exactly one key, read-only, never written by the app.**

- `sandlot_refresh_token` — read at `v2-pages.jsx:397` inside a `try/catch`, sent as the
  `x-refresh-token` header on `POST /api/refresh`. Referenced in the 401 error copy at `:387`.

The #36 removal effort **held**: `v2-pages.jsx:55-60` documents it, and there is no `setItem` anywhere
in the source. Skipper model choice, reasoning toggle and web-search toggle all reset to server
defaults on every load (`:5265-5273, 5303-5317`). Verified by grep across all three `.jsx` files.

Exposure: the refresh token is a manually-set config value in a single-user browser. It gates a route
that is itself optional-guard (`sandlot_api.py:1718-1727` returns early if `SANDLOT_REFRESH_TOKEN` is
unset). Any XSS would already own the page. **Acceptable as designed** — the inline comment's
reasoning ("reading a manually-set config value, not persisting app state") is sound.

### 4d. CDN resources and SRI

Two Google Fonts stylesheets, `index.html:10-12`, no SRI hashes:
```html
<link href="https://fonts.googleapis.com/css2?family=Inter:...&display=swap" rel="stylesheet">
```
SRI is not usable here — the `css2` endpoint returns UA-dependent content, so a fixed hash would break
on browser updates. That is a real constraint, not laziness. There is **no CSP header** on the
FastAPI responses (`sandlot_api.py:1750-1759` sets only `Cache-Control`).

React/ReactDOM are now bundled from `node_modules` and pinned exactly in `package.json:11-12`
(`"react": "18.3.1"`, `"react-dom": "18.3.1"`) with a committed lockfile, and CI rebuilds and
byte-compares `app.js` (`ci.yml:69-71`) — that supply-chain surface is in good shape and is a real
improvement over the CDN era. **My honest assessment: for a single-user fantasy-baseball app with no
credentials worth stealing, the Google Fonts dependency is an acceptable risk and adding a CSP would
be over-engineering. Do not spend time here.** The `javascript:` URL in §4b is the only item in this
section worth a commit.

### 4e. Skipper LLM output rendering

Markdown → React elements, never HTML (§4a). Player links go through `V2PlayerLink` (`:4972-4985`),
which renders a `<span onClick>` with an id that came from `[[name|id]]` tags or a fuzzy match against
the snapshot's own `player_index` (`:5013-5033`) — so the id is always one the server already knows.
No LLM-controlled URL reaches an `href` except the citation list in §4b. **This is the right design
and it is implemented correctly.**

---

## 5. Maintainability — duplicated logic that has already drifted

The brief asked for instances of drift, not general risk. Here are four, ranked by consequence, with
both copies cited.

### 5a. Snapshot freshness — three implementations, two threshold sets (**live user-visible drift**)

| Implementation | Thresholds | Consumed by |
|---|---|---|
| `sandlot_api._freshness` (`sandlot_api.py:1454-1467`, constants `:42-43`) | ≤18 h fresh, ≤36 h stale, else old | `/api/snapshot/latest`, `/api/attention`, `/api/hot-swaps/latest`, `/api/trades/incoming`, `/api/health` → the **top bar and Today page** |
| `player_service._snapshot_freshness` (`player_service.py:535-547`) | ≤30 min fresh, ≤24 h stale, else old | `/api/player/{id}` → the **player-sheet dot** (`v2-pages.jsx:4427-4431`) |
| `sandlot_waivers._freshness` (`sandlot_waivers.py:894-905`) | ≤30 min fresh, ≤24 h stale, else old | `/api/waiver-swaps/latest` → the **AI refresh-brief prompt** |

Symptom, verified: with the documented refresh cadence (`0 13,21 * * *` UTC), a normal snapshot is
between 30 minutes and 12 hours old for almost the entire day. In that window the Today top bar shows
a **green "Healthy"** dot and the player sheet shows an **amber "stale"** dot — for the same snapshot,
in the same session, one tap apart. And Skipper's brief prompt is told "stale" while the UI says
"fresh".

There is a fourth partial copy on the client: `v2FreshnessStateForAge` (`v2-pages.jsx:247-252`)
reimplements the 18 h/36 h boundaries in JS for the local minute-ticker, using `<` where Python uses
`<=` — an off-by-one at exactly 1080/2160 minutes. Cosmetic, but it is a fourth place to update.

**Fix spec:** delete `player_service._snapshot_freshness` and `sandlot_waivers._freshness`; import
`sandlot_api._freshness` — or better, move it to `sandlot_config` (which already exists at 27 lines and
is the natural home) and have all three import it. Then export the two threshold constants in the
`/api/snapshot/latest` payload so `v2FreshnessStateForAge` reads them instead of hardcoding.

### 5b. `short_reason` vs `v2QualityReason` — a hand-port that diverged in four ways

Same user-facing sentence ("why is this advice paused?"), computed independently on both sides.

- Python: `sandlot_data_quality.short_reason` (`sandlot_data_quality.py:276-297`), used by
  `sandlot_api._hot_swap_payload` (`sandlot_api.py:1342`) for the external-agent surface.
- JS: `v2QualityReason` (`v2-pages.jsx:272-287`) + `v2LineupQualityReason` (`:294-302`), used for the
  in-app copy.

Divergences (all **VERIFIED** by reading both):

1. **Selection semantics.** Python: `data_quality.get(key) or data_quality.get("reasons") or []` — a
   *fallback*. JS: `reasonKeys.flatMap(...).concat(dataQuality.reasons || [])` — a *concatenation*.
   Since `reasons` is the deduped union of all four lists (`sandlot_data_quality.py:97-104`), the JS
   version can surface a *projection* reason as the explanation for why *lineup* advice is paused, and
   its "plus N more" count is inflated by the union.
2. **Key list for `lineup`.** Python: `["lineup_recommendation_reasons"]`. JS:
   `['lineup_recommendation_reasons', 'recommendation_reasons']`.
3. **Suffix wording.** Python: `f"{first}, plus {n} more issue{'s' if ...}"`. JS:
   `` `${first}, plus ${n-1} more` `` — **no "issue(s)" noun at all**. Identical conditions render as
   "…, plus 2 more issues" via the API and "…, plus 2 more" in the UI.
4. **Trailing-period handling.** Python `.rstrip(".")` strips all trailing dots; JS `.replace(/\.$/, '')`
   strips one.

Plus asymmetric extra branches: JS has a `lineup_slots.reason` fallback Python lacks; Python has an
`add_drop_recommendations_ready` branch JS lacks.

**Fix spec:** stop porting. Have `_snapshot_payload` include a precomputed
`data_quality.short_reasons: {projection, recommendation, lineup, add_drop}` map produced by
`short_reason`, and delete `v2QualityReason`/`v2LineupQualityReason`'s reason-selection logic in favour
of a lookup. ~25 lines removed from the JSX, one source of truth for the sentence.

### 5c. Slot-classification sets — five definitions of "bench", five of "inactive" (**latent**)

| Set | Members | Where |
|---|---|---|
| bench (matchup) | BN, BE, BENCH, RES, RESERVE | `sandlot_matchup.py:17` |
| bench (win_week) | BN, BE, BENCH, RES, RESERVE | `sandlot_win_week.py:23` |
| bench (attention) | BN, BE, BENCH, RES, RESERVE, **MIN, MINORS** | `sandlot_attention.py:114` |
| bench (waivers) | BN, BE, BENCH, RESERVE, RES, **MIN, MINORS** | `sandlot_waivers.py:735` |
| bench (JSX) | BN, BE, BENCH, RES, RESERVE, **MIN, MINORS** | `v2-pages.jsx:51` |

Inactive-slot sets diverge similarly across `sandlot_matchup.py:15`, `sandlot_data_quality.py:14`,
`fantrax_data.py:515,715`, `sandlot_attention.py:32` and `v2-pages.jsx:50`.

Concrete consequence I traced in `sandlot_win_week`, where `MIN` is *not* bench:
- `_weekly_candidate_ceiling` (`sandlot_win_week.py:1082-1086`): `if move_slot not in BENCH_SLOTS:` →
  a minors-slot drop candidate is charged as a **forgone active starter**, subtracting his FP/G from
  the swap's value and pushing it down or off the ranked plan.
- `_post_add_snapshot` (`:1020-1031`): a minors-slot move-out falls through to
  `player_can_play_slot(add_row, "MIN")`, and on failure the whole waiver action is rejected as
  "unproven" with the nonsense message *"X cannot directly fill MIN"* — so the Adds board would show a
  swap that Win This Week silently drops.

**Marked SUSPECTED/latent, not live**, because `sandlot_waivers.PROTECTED_MOVE_OUT_SLOTS = {"IL","IR","MIN","MINORS"}`
(`sandlot_waivers.py:53`) currently prevents any MIN player from becoming a move-out candidate
(`_protect_move_out`, both the primary and fallback paths). The safety of `sandlot_win_week` therefore
depends on a guard in a different module that nothing links to it. `MIN` is a real slot in this
league — `fantrax_data.py:1219-1222` normalizes MIN/MINORS/MINOR/"MINOR LEAGUE" → `"MIN"`, and this is
a dynasty league per `PRODUCT.md`.

Also note that after `_normalize_slot_label` (`fantrax_data.py:1227-1232`) the only slots that ever
reach these sets are `BN`, `RES`, `IR`, `IL`, `MIN` — every `BE`/`BENCH`/`RESERVE`/`MINORS`/`INJ`
alias in all ten sets is dead weight that makes the real difference (`MIN`) hard to see.

**Fix spec:** one module (`sandlot_slots.py`, ~15 lines) exporting `BENCH_SLOTS`, `INACTIVE_SLOTS`,
`PROTECTED_SLOTS` post-normalization only; every module imports it; the JSX receives them in the
snapshot payload rather than redeclaring.

### 5d. Period-language ("Win This Week" vs "Win This Matchup") — computed twice

- Python `_period_language` (`sandlot_win_week.py:~1225`) derives `day_count` from
  `planning_horizon.start/end` and picks the surface label + "remaining-week"/"remaining-period"
  wording, which is baked into `summary.headline`, `summary.outlook` and
  `summary.win_probability_excluded_reason`.
- JS `v2WinPeriodLanguage` + `v2InclusivePeriodDays` (`v2-pages.jsx:1160-1176`) recompute the same
  thing from the same fields to pick the panel heading.

Python's `day_count` uses `date.fromisoformat` on the horizon; JS uses `new Date(\`${start}T00:00:00Z\`)`.
They agree today. But the label is **not** in the payload, so the panel can say "Win This Week" while
the headline inside it says "remaining-period" (or vice versa) the moment either parser changes.
**Fix:** add `plan.period_language: {surface_label, remaining_label, day_count}` to the payload;
delete the JS copy.

### 5e. Natural seams (lower priority)

`v2-pages.jsx` at 5,705 lines. The seams are already clean — the file is organised in labelled
sections and every page is a top-level function. Splitting on the existing boundaries costs almost
nothing now that ES modules are in play:

| Proposed file | Contents | Lines |
|---|---|---|
| `format.js` | `v2Number`, `v2Signed`, `v2FormatMetric`, `v2ShortDate`, `v2SyncLabel`, `v2FreshnessStateForAge`, `v2SyncTone`, `v2BriefLines`, `v2QualityReason` | ~120 |
| `controls.jsx` | `V2Segment`, `V2Primary`, `V2Caution`, `V2StatRow`, `V2Eyebrow`, `Legend`, `V2MiniInsight`, `V2ReasonLine`, `useV2DialogFocus` | ~180 |
| `today.jsx` | `V2Today`, `V2MatchupStatusCard`, `V2WinThisWeekPanel`, `V2ActionReviewSheet`, `V2HotSwapsPanel`, `V2AttentionQueue*`, `V2LineupHotSwap*`, `V2RecommendationReceipt`, `V2RecommendationLearning` + their helpers | ~1,900 |
| `roster.jsx` | `V2Roster`, `V2TeamRoster`, `V2RosterSlot`, `V2League`, `V2TeamRow`, `V2LeagueTradeDesk` | ~350 |
| `adds.jsx` | `V2FreeAgents`, `V2Waiver*` | ~230 |
| `trade.jsx` | `V2TradeGrader` and the eight `V2Trade*`/`V2PlayerPicker`/`V2ManualTradeReview` components | ~700 |
| `player.jsx` | `V2PlayerSheet`, `V2Profile*`, `V2Clip*`, `V2BarSparkline`, season computers | ~620 |
| `skipper.jsx` | `V2Skipper`, `V2Bubble`, `V2WebSources`, `V2SkipperRefreshBrief`, `V2MatchupProjectionCard`, markdown/link renderers | ~700 |
| `app.jsx` | `V2App`, `V2TopBar`, `V2TabBar` + a new error boundary | ~250 |

Delete first, split second: **~340 lines of the file are unreachable** (§3a) — `V2Settings`, `V2Auth`,
`V2MatchupRecommendationCard`, `V2HealthSummary`, `V2HealthSection`, `V2HealthPlayerRow`,
`V2DecisionCard`, `V2PositionCard`, `V2_PROFILE_PLACEHOLDER_CLIPS`.

`sandlot_api.py` at 1,759 lines is ~55% route handlers and ~45% payload shapers. The obvious seam is
`sandlot_public_payloads.py` for `_public_recommendation_receipt`, `_public_recommendation_outcome`,
`_public_recommendation_learning`, `_public_trade_target_period`, `_snapshot_payload`, `_hot_swap_payload`,
`_player_index`, `_freshness`, `_run_summary` (~470 lines) — which also happens to be the exact set a
snapshot-payload contract test would need to import.

---

## 6. Committed bundle drift — `web/sandlot/app.js`

`web/sandlot/app.js` is a 341 KB minified build artifact, tracked in git (`git ls-files web/sandlot/`
lists it), not gitignored, and **it is what production serves**. Inspection only — I did not run a build.

### 6a. Is the committed bundle currently in sync?

**Yes, to the strongest confidence available without running esbuild.** Four independent lines of
evidence:

1. **Commit discipline is perfect post-migration.** `app.js` was introduced in `0f137ff` (#54). Since
   then, 37 commits have touched `web/sandlot/`. I enumerated every one and checked whether it modified
   a `.jsx` file without also modifying `app.js`: **zero such commits.** (35 jsx-only commits exist in
   history, but all of them predate `0f137ff` — i.e. they are from the pre-bundler era when no artifact
   existed.)
2. **Same head commit.** `git log -1 -- web/sandlot/v2-pages.jsx` and `git log -1 -- web/sandlot/app.js`
   both return `1619250` ("Reconcile lineup receipts with Fantrax state (#158)"), and the previous seven
   commits touching each file are identical in the same order.
3. **Content spot-check against the three most recent frontend commits.** Seven distinctive string
   literals added by #158/#154/#153/#151 — `"Latest Fantrax snapshot confirms all "`,
   `"Both rosters: "`, `"Sandlot trade-analysis evidence:"`, `"Partially applied"`,
   `"Lineup recommendation readiness is not explicitly trusted"`, `"Review on this Mac"`,
   `"cadence-estimated"` — are all present in `app.js`. (Two initially read as absent; that was a
   false negative from esbuild's default `charset=ascii` escaping `·` U+00B7 as `\xB7`. Confirmed by
   locating `Partially applied \xB7 ${...}` in the bundle. Worth knowing before anyone else greps it.)
4. **Legacy markers are gone.** `Object.assign(window`, `sandlot_mock`, `MOCK_ROSTER`, `data2.jsx` — all
   absent from `app.js`, consistent with a rebuild after #52/#54 rather than a stale carry-over.

**Limitation, stated plainly:** none of this proves byte-identity. Only re-running
`npm ci && npm run build:sandlot && git diff --exit-code web/sandlot/app.js` proves that, and I was
asked not to. What I can say is that a stale bundle would have had to survive CI on every one of the
37 PRs that touched the frontend, which is not plausible.

### 6b. Is there a CI check, and does it block a merge?

**The check exists and is correct. Whether it blocks is not determinable from the repo.**

`.github/workflows/ci.yml:56-71`, job `frontend-build`:
```yaml
- run: npm ci
- run: npm run build:sandlot
- name: Verify committed app bundle is current
  run: git diff --exit-code web/sandlot/app.js
```
Triggers are `pull_request: {}` (every PR, any base) and `push: branches: [main]` (`ci.yml:4-8`). The
check itself is sound: `npm ci` is lockfile-deterministic (esbuild is pinned to exactly `0.25.12` at
`package-lock.json:461`), so a rebuild on a correct tree produces an identical artifact and the diff is
empty.

What I **cannot** verify from the tree: whether `frontend-build` is configured as a **required status
check** under GitHub branch protection. There is no `CODEOWNERS`, no rulesets file, no
settings-as-code, and branch protection lives in the GitHub API, not the repo. The project's own
`CLAUDE.md` describes the merge discipline as *"open PR, wait for CI green, merge with
`--squash --delete-branch`"* — i.e. a **human/agent convention, not necessarily an enforced gate**. Given
the repo has agent-driven workflows merging PRs, "the agent is supposed to wait for green" is a weaker
guarantee than "GitHub refuses the merge."

**Recommendation (one-time, ~2 minutes):** make `Frontend build` a required status check on `main`,
alongside `Python import smoke`. `gh api -X PUT repos/zoelsner/baseball/branches/main/protection/required_status_checks ...`.

Two secondary observations on the check:

- **It is a single point of failure.** The other job that would notice a bad bundle,
  `local-frontend-e2e` (`playwright.yml:100-176`), runs `npm run build:sandlot` at step 3 — which
  *overwrites* the committed `app.js` — and then serves `web/sandlot` statically. So it tests the
  freshly built bundle, **not the committed one**, and would pass happily over a stale artifact. The
  deployed Playwright job does test the real artifact, but on PRs it runs only
  `specs/attention-api.spec.ts` (`playwright.yml:80-82`), which is a pure API test with no browser. Net:
  **`git diff --exit-code` is the only PR-time defence.**
- **`package.json:15` declares `"esbuild": "^0.25.5"` (caret) while the lockfile pins `0.25.12`.** CI uses
  `npm ci`, so CI is safe. But a contributor who runs `npm install` instead will resolve a newer esbuild,
  produce byte-different minified output, and commit a bundle that CI then reports as "not current" —
  a confusing failure that looks like stale source when it is actually a toolchain-version mismatch.
  Pinning to `0.25.12` exactly costs nothing and removes the trap.

### 6c. Failure mode if it drifts

**Production silently serves stale JavaScript, with no user-visible or operator-visible symptom, until
the daily Playwright cron happens to notice — and it may not.**

Chain, verified:

1. **Railway serves the committed artifact directly; nothing rebuilds it.** `Procfile` defines only
   `web: uvicorn sandlot_api:app ...` and `cron: python sandlot_cron.py` — no build step. There is no
   `nixpacks.toml`, `railway.json` or `Dockerfile` in the repo, and `package.json` has **no `build`
   script** (only `build:sandlot`), so a Nixpacks auto-detected `npm run build` would not fire either.
   `sandlot_api.py:1759` mounts `web/sandlot` as static files. The bytes in git are the bytes in prod.
2. **Cache-busting works correctly, which makes drift *more* invisible, not less.** `sandlot_api.py:1750-1756`
   reads `index.html`, computes `sha256(app.js)[:12]`, and rewrites `app.js?v=frontend-build` →
   `app.js?v=<digest>`; `NoCacheStaticFiles` (`:110-115`) additionally sets `Cache-Control: no-store` on
   `app.js` and `index.html`. So the browser reliably fetches whatever is committed. There is no
   "hard-refresh fixed it" symptom to tip anyone off — the digest simply tracks the stale file. Drift
   presents as *"my fix didn't work"*, not as *"the page is cached."*
3. **The source is now lying.** Every subsequent reader — human or agent — reads `v2-pages.jsx`,
   sees the fix, and reasons about behaviour the user is not getting. This is the expensive part: it
   silently invalidates code review, and it invalidates my own §1–§5 findings, which are all derived
   from the `.jsx` sources.
4. **Detection is delayed and partial.** Post-merge, the full Playwright suite runs on push-to-main
   (`playwright.yml:8`) against the deploy, so a *behavioural* regression in a covered flow would be
   caught — but after merge, and only for the flows those 47 tests cover. Anything outside them
   (e.g. the League tab, the team-roster overlay, Settings) would ship stale indefinitely.
5. **Recovery is trivial once noticed** — `npm run build:sandlot && git commit` — which is exactly why
   the gate matters more than the fix.

**Assessment:** the practice is currently working (6a) and the check is correctly written (6b). The
residual risk is entirely about enforcement, and it is concentrated in one place: if `frontend-build` is
not a required status check, a single merged PR that skipped it produces a failure mode with no
symptom, no alarm, and a stale source-of-truth. Make it required; pin esbuild exactly. Everything else
here is fine.

---

## Priority order

1. **`V2PlayerSheet` infinite take-poll** (§3b) — cost/rate-limit bug, ~24 background OpenRouter calls/minute. Fix: drop `data` from the dep array, hoist `attempts` to a ref.
2. **Freshness triplication** (§5a) — live user-visible contradiction, and the fix is deletion.
3. **Placeholder clips fabricating player quotes** (§1b-3) — delete `V2_PROFILE_PLACEHOLDER_CLIPS` and the `|| data.clips` fallback; render nothing when `media.items` is absent.
4. **Data-conditional Playwright self-skips** (§2e) — make the daily cron job fail, not skip, when the live snapshot is empty or every route 503s. That job exists to catch scrape breakage and currently cannot.
5. **Make `frontend-build` a required status check; pin esbuild to `0.25.12` exactly** (§6b) — ~2 minutes, removes the only unenforced link in the source→production chain.
6. **`V2RecommendationReceipt` minute-polling** (§3b) — the fix already exists on the sibling component.
7. **Contract test for `/api/snapshot/latest`** (§2d) — model it on `attention-api.spec.ts`, which is already the right shape.
8. **`javascript:` URL filter on citations** (§4b) — one line each side.
9. **Error boundary** (§3c) — ~20 lines, converts blank-screen to recoverable.
10. Delete dead code (§3a/§5e), then split the two large files.

## Open questions

- Is the twice-daily cron the real cadence in production, or is manual refresh dominant? It changes how bad §5a's mismatch feels day to day, not whether it is wrong.
- Was `V2Settings`/`V2Auth` deliberately parked for a future multi-user story, or genuinely abandoned? Deleting is right either way (git remembers), but I would not do it silently.
- `POST /api/actions` is referenced by both `sandlot_api.py:169` and `attention-api.spec.ts:41` but does not exist. Planned, or leftover?
- I could not determine from the repo whether any roster row in this league actually sits in a `MIN` slot at present. That is the difference between §5c being latent and live.
