# Sandlot documentation audit

**Scope:** `origin/main` @ `bd54135` (2026-07-24), read-only worktree at
`<audit worktree>`.
**Method:** every named file / function / table / route / env var / CLI command in the doc set
grepped against the tree; safety assertions traced into the enforcing code paths; test and spec
counts computed statically.

**Could not verify:** the Python unit suite was not run. There is no `.venv` in the worktree, the
user's primary checkout has no `.venv` either, and system `python3` lacks `fastapi`/`psycopg`.
Per brief, no `pip install` was attempted. Static test counts are reported instead.

---

## Summary table

| Doc | Lines | Verdict | Divergences |
|---|---|---|---|
| `docs/ARCHITECTURE.md` | 303 | **Contains a section that was never true** | 5 (1 critical FALSE, 2 STALE, 1 MISSING, 1 imprecise) |
| `CLAUDE.md` | 70 | Mostly accurate; material omissions | 4 (2 STALE, 1 MISSING, 1 soft contradiction) |
| `AGENTS.md` | 97 | Accurate; inherits risk by pointing at ARCHITECTURE.md | 1 (trivial) |
| `STATUS.md` | 404 | **Unreliable as current state** | 7 (1 FALSE, 1 self-stale, ~40% UNVERIFIABLE, 1 internal contradiction) |
| `PRODUCT.md` | 85 | Accurate | 0 |
| `DESIGN.md` | 241 | Accurate | 1 (trivial) |
| `README.md` | 183 | **Actively misleading to a newcomer** | 4 (1 FALSE, 2 STALE framing, minor) |
| `docs/SANDLOT-HANDOFF.md` | 237 | **Wrong in nearly every quantitative claim** | 8 FALSE |
| `docs/recommendation-receipts.md` | 218 | Accurate on safety; stale on two versions | 2 STALE |
| `docs/decision-science.md` | 124 | Accurate; one load-bearing unverifiable | 1 UNVERIFIABLE |
| `docs/sandlot-railway-v1.md` | 136 | Accurate but incomplete | 2 (incomplete route + env lists) |
| `docs/sandlot-execution-dry-run.md` | 226 | **Fully verified** | 0 |
| `docs/sandlot-automation.md` | 51 | **Fully verified** | 0 |
| `docs/win-this-week.md` | 309 | Accurate | 0 (module never named) |
| `docs/sandlot-matchup-projection-model.md` | 136 | **Fully verified** to the constant | 0 |
| `docs/quality/*` | ~1,300 | Historical; unlabeled as such | 3 (orphan/status) |
| Orphan plan docs (5 files) | ~980 | Superseded; unlabeled | 3 FALSE-if-followed |
| **Undocumented modules** | ~8,000 LOC | — | 5 with zero doc mentions |

---

# Findings, ordered by propagation risk

## RISK 1 — `docs/ARCHITECTURE.md` describes a frontend architecture that has not existed since 2026-05-26, and never existed at the time the file was written

**Classification: FALSE (critical).**

`docs/ARCHITECTURE.md:254-265`:

> `web/sandlot/index.html` loads scripts in order:
> 1. `atoms.jsx` 2. `data.jsx` 3. `data2.jsx` 4. `v2-pages.jsx`
>
> There is no module system. Shared symbols must be exported through
> `Object.assign(window, ...)`. JSX validation requires Babel parser, not `node --check`.

and `docs/ARCHITECTURE.md:20`:

> Frontend: static `web/sandlot/index.html` plus in-browser Babel JSX files

Every clause is contradicted:

- `web/sandlot/index.html:50` — the file loads exactly one script:
  `<script src="app.js?v=frontend-build" defer></script>`. There are no `.jsx` script tags and no
  Babel CDN tag.
- `web/sandlot/main.jsx:1-3` — ES module imports (`import React from 'react'`).
- `package.json:6` — `"build:sandlot": "esbuild web/sandlot/main.jsx --bundle --minify ..."`.
- `data.jsx` / `data2.jsx` do not exist; deleted 2026-05-26 in `e2ac912` ("[codex] Remove
  production mock data paths (#52)").
- `grep -n "Object.assign(window" web/sandlot/*.jsx` → zero matches.
- Validation is `npm run build:sandlot`, and `.github/workflows/ci.yml:79` enforces it with
  `git diff --exit-code web/sandlot/app.js`.

**This is worse than stale.** `git log --diff-filter=A -- docs/ARCHITECTURE.md` shows the file was
created **2026-06-10** in `01227c6` (PR #60), fifteen days *after* the esbuild migration
(`0f137ff`, #54, 2026-05-26). The section was inaccurate the day it was committed. It survived an
edit as recently as 2026-07-13 (`a2f11c9`).

**Why this is the highest-risk finding:** `AGENTS.md:14` instructs every agent to read
`docs/ARCHITECTURE.md` "for current technical boundaries," and `AGENTS.md:31` makes checking it a
gate before any code edit. `docs/ARCHITECTURE.md:33` says "If these disagree, resolve the conflict
before implementation" — so an agent that notices the conflict with `CLAUDE.md:33-42` /
`AGENTS.md:59-63` is told to stop and adjudicate, with no tiebreaker. An agent that does *not*
notice writes `window.*`-global JSX that (a) is silently dropped by esbuild's module scope, and
(b) fails the `frontend-build` job.

**Correct behavior is stated in three other places** — `CLAUDE.md:33-42`, `AGENTS.md:59-63`,
`docs/SANDLOT-HANDOFF.md:36-44`. ARCHITECTURE.md is the lone dissenter, and it is the one AGENTS.md
elevates.

---

## RISK 2 — `STATUS.md` describes a `POST /api/actions` executor endpoint that does not exist

**Classification: FALSE.**

- `STATUS.md:150` — "`GET /api/attention` is live … Returns the ordered queue with status-safe
  `POST /api/actions` payloads where allowed."
- `STATUS.md:228` — "Railway tokens (`SANDLOT_ACTIONS_TOKEN`, `SANDLOT_REFRESH_TOKEN`) unset — **the
  executor endpoint is fail-closed (503)** until then."

There is no such route. `sandlot_api.py` declares 27 routes (`@app.get`/`@app.post`/`@app.delete`,
lines 124–1750); the complete list contains no `/api/actions`. `SANDLOT_ACTIONS_TOKEN` appears
nowhere in any `.py` file — the only occurrence in the repo is
`.github/workflows/sandlot-automation.yml:199`, where it is deliberately set to `""`.

The executor lives on **unmerged draft PR #63**. `docs/sandlot-automation.md:26-39` is explicit and
correct about this: the `executor-contract` job checks out `refs/pull/63/head`
(`.github/workflows/sandlot-automation.yml:208-211`), and "Draft PR #63 intentionally fails this
gate." Meanwhile `sandlot_attention.py:12`, `sandlot_attention.py:373`, and `sandlot_api.py:169`
all still carry docstrings describing payloads "for `POST /api/actions`" — so the phantom route is
reinforced from inside the code as well.

**Propagation risk:** `CLAUDE.md:5` tells every agent to read `STATUS.md` first, and describes it as
holding "the standing safety rules for the actions executor." An agent asked to work on execution
safety will look for a deployed, token-gated write endpoint. It will either waste a session hunting
for it, or — worse — reason about the system as though a write path is already in production.

---

## RISK 3 — `docs/SANDLOT-HANDOFF.md` instructs an agent to delete the four governing docs

**Classification: FALSE.** `docs/SANDLOT-HANDOFF.md:215-217`:

> Untracked scaffold files in the working tree (`AGENTS.md`, `PRODUCT.md`, `DESIGN.md`,
> `docs/ARCHITECTURE.md`, `.github/` templates) duplicate open PR **#60** — **remove the local
> copies when #60 merges.**

PR #60 merged as `01227c6` on 2026-06-10. All four files are tracked and committed
(`git log -1 -- AGENTS.md` → `6959d3f` 2026-07-11). Following this instruction literally destroys
the product, design, agent-rules, and architecture docs in one commit.

This doc self-identifies as "Orientation doc for an agent picking up the Sandlot web app"
(`docs/SANDLOT-HANDOFF.md:3`) with a stated snapshot date of 2026-06-10 @ `44f6d81`. It was last
edited 2026-06-19. Nearly every quantitative claim in it is now wrong:

| Claim | Doc line | Reality |
|---|---|---|
| "**6 tabs**"; "`main` still has all 6 tabs" | `:68`, `:89` | 5 tabs — `web/sandlot/v2-pages.jsx:674-680` = `today, roster, skipper, fa, league`. No `trade` tab. |
| "all 12 routes" + route table | `:117`, `:141-157` | 27 routes in `sandlot_api.py`. Table omits every receipt, outcome, learning, attention, hot-swap, win-this-week, execution-request, trades/incoming and readiness route. |
| `sandlot_api.py` 510 lines | `:117` | 1,759 |
| `sandlot_db.py` 595 | `:118` | 2,230 |
| `sandlot_matchup.py` ~1100 | `:122` | 2,583 |
| `fantrax_data.py` 624 | `:132` | 2,584 |
| `sandlot_trades.py` 362 | `:121` | 1,426 |
| "Kimi → Tencent fallback" | `:173` | `sandlot_skipper.py:33-34` = DeepSeek → Kimi. `tests/test_sandlot_skipper_config.py:24` asserts Tencent is *retired*. (The same doc gets it right at `:119`.) |
| PRs #61/#62 "DRAFT"; "merge #61 first" | `:101-109` | Both shipped; nav is already 5 tabs. |
| "a real `unittest` suite (75 tests)" | `:195`, `:233` | 589 `def test_` across `tests/*.py`. |
| Today = "Roster-health view" | `:72` | Contradicts `PRODUCT.md:58`, `DESIGN.md:225`, `docs/ARCHITECTURE.md:83` (Attention Queue). |

The one thing it gets right that others miss: `:66` and `:218` correctly flag that `V2PlayerProfile`
/ the `pushed` nav state is not built (issue #37) — matching `CLAUDE.md:37` and
`web/sandlot/v2-pages.jsx:419-420` (only `page` and `detail` state exist; `V2PlayerSheet` at
`:4324`, no `V2PlayerProfile` anywhere).

---

## RISK 4 — `CLAUDE.md` omits every execution-control environment variable

**Classification: MISSING.**

`CLAUDE.md:22-29` presents itself as the "Required env (`.env`)" list. It names 5 `SANDLOT_*`
variables. Code reads **25**:

```
SANDLOT_AI_MODEL_FALLBACK          SANDLOT_PROFILE_WARM_DISABLED
SANDLOT_AI_MODEL_PRIMARY           SANDLOT_PROFILE_WARM_ENABLED
SANDLOT_ALLOW_SELENIUM_LOGIN       SANDLOT_PROFILE_WARM_LIMIT
SANDLOT_BROWSER_ORIGIN             SANDLOT_PROFILE_WARM_PARALLELISM
SANDLOT_CAPTURE_ROSTER_DOM_SLOTS   SANDLOT_PROFILE_WARM_TAKES
SANDLOT_EXECUTION_DRY_RUN_ENABLED  SANDLOT_REFRESH_TOKEN
SANDLOT_FANTRAX_ROSTER_URL         SANDLOT_ROSTER_DOM_HEADFUL
SANDLOT_KEEP_SNAPSHOTS             SANDLOT_ROSTER_DOM_WAIT_SECONDS
SANDLOT_OWNER_ACTION_TOKEN         SANDLOT_RUNNER_TOKEN_SHA256
SANDLOT_OWNER_ACTION_TOKEN_SHA256  SANDLOT_SKIPPER_WEB_SEARCH_*
SANDLOT_URL                        SANDLOT_WAIVER_AI_WARM_*
```

The three that gate the entire execution control plane —
`SANDLOT_EXECUTION_DRY_RUN_ENABLED`, `SANDLOT_OWNER_ACTION_TOKEN_SHA256`,
`SANDLOT_RUNNER_TOKEN_SHA256` — are absent. `CLAUDE.md:52` alludes to "separate hashed owner and
runner bearer credentials" without naming them. They are documented correctly, but only in
`docs/sandlot-execution-dry-run.md:29-41`, which `CLAUDE.md` and `AGENTS.md` never reference.

An agent reasoning about "what turns writes on" from `CLAUDE.md` alone cannot find the kill switch.

**Related, in `.env.example:29`:** `# OpenRouter — Skipper chat (Kimi primary, DeepSeek fallback)`
— **inverted**. `sandlot_skipper.py:33-34`: `PRIMARY_MODEL = "deepseek/deepseek-v4-flash"`,
`FALLBACK_MODEL = "moonshotai/kimi-k2"`. `CLAUDE.md:25` and `:54` get this right; the file agents
actually copy gets it backwards. Same inversion at `docs/sandlot-skipper-chat-plan.md:11`.

---

## RISK 5 — `CLAUDE.md` misstates what CI covers

**Classification: STALE.** `CLAUDE.md:60-63` describes CI as two workflows / three jobs.

1. **Missing job.** `.github/workflows/playwright.yml` has **two** jobs: `e2e`
   ("E2E against Railway", `:16-17`) and `local-frontend-e2e` ("Local frontend E2E", `:90-91`).
   `CLAUDE.md:62` names only the first. The second is the branch-local browser regression suite that
   runs 9 specs against a locally built bundle — the one that actually catches a broken JSX change
   before deploy.
2. **Overstated PR coverage.** `CLAUDE.md:63` — "Green CI means … the Playwright assertions pass
   against prod." On `pull_request`, `playwright.yml:75-79` runs **only**
   `specs/attention-api.spec.ts` (2 tests). The full 68-test suite runs only on push-to-main,
   `workflow_dispatch`, and the 14:30 UTC cron.
3. **Undisclosed Postgres in CI.** `.github/workflows/ci.yml:13-28` provisions a
   `postgres:16` service and sets `SANDLOT_TEST_DATABASE_URL`. The two "disposable-Postgres" tests
   that `STATUS.md:49` reports as skipped **do run in CI**. No doc mentions this, so an agent
   reproducing CI locally will silently get less coverage than CI and not know why.
4. Minor: `CLAUDE.md:61` — "imports every Sandlot module." `ci.yml:41-59` names 17 modules and
   omits `sandlot_matchup`, `sandlot_attention`, `sandlot_receipts`, `sandlot_execution`,
   `sandlot_data_quality`, `sandlot_owner_bridge`, `sandlot_trades`, `sandlot_future_games`,
   `sandlot_pitcher_opportunities`. Most are reached transitively via `sandlot_api`
   (`sandlot_api.py:24-35`) and all are exercised by the subsequent unittest step, so real coverage
   is fine — the wording is what's wrong.

---

## RISK 6 — `README.md` is a repo map for a repo that no longer exists

**Classification: FALSE (file listing) + STALE (framing).**

`README.md:1-9` presents the project as two CLI scripts with Sandlot as a single bullet at `:7`.
`CLAUDE.md:3` and `AGENTS.md:18` say the opposite: Sandlot is the live product; the CLI is "not
actively used."

- **FALSE — `README.md:155-177`.** The "Files in this project" tree lists 13 files and presents
  itself as complete. It omits all ~30 `sandlot_*.py` modules, `web/`, `tests/` (52 test files),
  `scripts/`, `.github/`, `docs/`, `package.json`, `Procfile`. A newcomer building a mental model
  from this tree will not know the web app exists as code.
- **STALE — `README.md:20-110`.** Setup instructs Gmail app passwords, launchd plists, and a Claude
  Pro/Max CLI subscription. None of that is needed for the live product. Nothing about
  `npm run build:sandlot`, `DATABASE_URL`, Railway, or the unittest suite.
- **`README.md:43`** — `cd /Users/zach/Projects/fantrax-daily-audit`. Wrong path; the repo lives
  under iCloud Drive.

### On the "recommend-only / will never set a lineup" claim

`README.md:9` — "**Both are recommend-only.** The Fantrax library is read-only — these scripts will
never set a lineup, drop a player, or accept a trade for you." `README.md:181` — "Auto-execute
roster moves, drops, claims, or trades (the API is read-only and even if it weren't, no)."

**The conclusion is still correct. The stated mechanism is not.** Verified: no code path in this
tree writes to Fantrax. `sandlot_execution.py:1-6` — "This module does not import Selenium and
cannot write to Fantrax." `docs/sandlot-execution-dry-run.md:76` — "There is no execution state and
no route that calls Fantrax after preflight." The state machine
(`sandlot_execution.py:23`, `TERMINAL_STATES`) terminates at `preflight_passed`.

But the repo now contains a credentialed execution control plane
(`sandlot_api.py:421-535`, four routes behind SHA-256 owner/runner digests), a loopback owner
bridge (`sandlot_owner_bridge.py`, 683 lines), and a Selenium-capable local runner
(`scripts/sandlot_execution_runner.py`). The no-write guarantee is now maintained by *contract
assertions and feature flags* — `sandlot_execution.py:124` rejects any contract where
`writes_enabled is not False`; `sandlot_execution.py:157` bounds the preflight snapshot age — not by
"the API is read-only."

That distinction matters for propagation: an agent citing README's mechanism ("the library can't
write") will conclude a write is *impossible* and skip the safety review that
`AGENTS.md:50-54` and `PRODUCT.md:67-71` require. README should not be the file anyone consults on
this question, but it is the only one a newcomer opens first.

---

## RISK 7 — `STATUS.md` is structurally unable to convey current state

**Classification: STALE (self-declared) + ~40% UNVERIFIABLE.**

1. **Stale by its own contract.** `STATUS.md:4` — "Last updated: **2026-07-14**." HEAD is `bd54135`
   (2026-07-24). Four PRs merged after that line was written: `d24e805` (#160), `1619250` (#158),
   `551c26f` (#162), `bd54135` (#163). `CLAUDE.md:5` mandates "Update it whenever the plan changes."
   The mandate is not being met, so the file's own authority claim is the first thing that's wrong.
2. **Unverifiable production claims.** Roughly 40% of the file is production-database state that
   cannot be checked from the repo and carries no "as of" qualifier that would let a reader discount
   it: snapshot IDs 213/217/219/220/221/285 (`:250-329`), refresh runs 295/298/300/301, "37 rows,
   17 trusted, 20 untrusted" (`:219-220`), "0/8 scored and 0/4 accepted-and-observed" (`:80-82`),
   "TJ Friedl/Ildemaro Vargas … `+9.1`" (`:312-314`), "409–393" (`:21-22`),
   "`app.js?v=33b257ebc7c8`" (`:360`). These read as present-tense facts. Most are months-old
   observations.
3. **Append-only narrative with no resolution markers.** 20+ bullets, most opening "in progress,"
   spanning 2026-06-10 → 2026-07-14, in reverse-ish order. Test counts appear as `157` (`:195`),
   `173`/`174` (`:292`, `:306`), `177` (`:326`), `179` (`:349`), `381` (`:125`), `456` (`:104`),
   `461` (`:83`), `573` (`:49`) — each presented as "current verification" for its slice, with no
   indication which is live.
4. **Internal contradiction in the "non-negotiable" section.** `STATUS.md:400` — "no add/drop
   recommendations until #67 lands." `STATUS.md:303-307` reports #67's production gate cleared via
   raw `posId`. Whether the rule is retired is left unresolved, inside a block labeled
   "Safety rules (non-negotiable)."
5. **A pasteable prompt that redoes finished work.** `STATUS.md:402-404` ("Cloud session kickoff —
   paste this on your phone") directs an agent to work #67 and PR #63. `RESERVED_SLOTS` in
   `sandlot_attention.py:32` already contains `MIN`/`MINORS`; the `posId` provenance fix shipped.

### Numeric claims — what checks out

| Claim | Line | Result |
|---|---|---|
| "11 trade and 4 Skipper web mobile browser journeys" | `:50` | ✅ **Exact.** `tests/playwright/specs/trade.spec.ts` = 11 `test(`; `skipper-web-fallback.spec.ts` = 4. |
| "2 disposable-Postgres tests skipped" | `:49` | ✅ **Exact.** `tests/test_sandlot_receipts.py:2096` `@unittest.skipUnless(SANDLOT_TEST_DATABASE_URL)` guards `RecommendationReceiptPostgresConcurrencyTests` (`:2097-2256`), which has exactly 2 test methods (`:2098`, `:2172`). |
| "573 Python tests pass" | `:49` | ⚠️ **Drifted, not falsified.** Static count is 589 `def test_` across `tests/*.py` at HEAD; 4 PRs landed after `:49` was written. Consistent with `docs/quality/quality-loop-progress.md` reporting `553` on 2026-07-12. Suite not run — no venv available. |
| "20 relevant browser journeys" | `:29` | ❌ **UNVERIFIABLE.** No spec or grouping totals 20. `today.spec.ts` 8 + `today-projection.spec.ts` 7 = 15; + `today-attention.spec.ts` 14 = 29. Total suite = 68 tests / 12 specs. |
| "44 deterministic mobile browser tests" | `:84` | ❌ **UNVERIFIABLE** (historical, superseded). |

---

## RISK 8 — Two "source of truth" docs disagree on whether the counterfactual gate is still closed

**Classification: STALE (safety-adjacent).**

`docs/recommendation-receipts.md:190-198`:

> Until the separate append-only outcome-evaluation ledger and scorer ship, Sandlot **always**
> records: `measurement_scope: observed_team_total` … `counterfactual_state: unavailable` …
> `actual_gain: null` … `autopilot_eligible: false`

It shipped. `sandlot_receipts.py:25` — `COUNTERFACTUAL_LINEUP_SCORING_VERSION =
"counterfactual_lineup_v1"`. `sandlot_db.py:889` filters evaluations on it.
`sandlot_api.py:268-283` serves `/api/recommendation-learning`, which aggregates that ledger.
`docs/ARCHITECTURE.md:245-252` and `STATUS.md:91-104` both describe it as landed.

The *conservative* labels (`autopilot_eligible: false`, `counterfactual_gain_available: false`) are
still hard-coded at `sandlot_api.py:252-253`, so nothing unsafe is happening. But a reader of
`recommendation-receipts.md` — the canonical receipts doc — will believe a gate is closed that is
open, and may re-implement work that exists.

### Version-string drift: `fantrax_period_lineup_v2` → `v3`

Three docs name **v2**; code is on **v3**, and no doc mentions v3:

- `docs/ARCHITECTURE.md:65`, `docs/recommendation-receipts.md:167`, `STATUS.md:383` → `fantrax_period_lineup_v2`
- `fantrax_data.py:184` → `LINEUP_PERIOD_EVIDENCE_VERSION = "fantrax_period_lineup_v3"`
- `sandlot_receipts.py:26` → `COUNTERFACTUAL_LINEUP_SOURCE_EVIDENCE_VERSION = "fantrax_period_lineup_v3"`
- `sandlot_db.py:895` → `AND l.evidence_version = 'fantrax_period_lineup_v3'`

An agent writing an evidence consumer against the documented `v2` string gets zero rows.

---

## RISK 9 — `CLAUDE.md` "No localStorage" is absolute; the code has a documented exception

**Classification: soft contradiction.**

- `CLAUDE.md:38` — "**No `localStorage`.** Don't reach for it for new state."
- `AGENTS.md` — silent on the topic.
- `web/sandlot/v2-pages.jsx:397` — `token = window.localStorage.getItem('sandlot_refresh_token')`.
  `:387` — user-facing error copy telling the user to set it.
- `web/sandlot/v2-pages.jsx:54-60` — an in-code comment explains the carve-out: reading a
  manually-set config value is not persisting app state.
- `docs/sandlot-railway-v1.md:67-71` — actively instructs the operator to run
  `localStorage.setItem('sandlot_refresh_token', ...)`.

The rule as stated is defensible ("for new state"), but the exception exists only in a code comment
and an ops doc. An agent enforcing the absolute reading could remove a working, documented control.

---

## RISK 10 — `docs/ARCHITECTURE.md` "Current Stack" omits the largest modules

**Classification: MISSING.** `docs/ARCHITECTURE.md:15-23` lists 13 modules. Absent from that
section: `sandlot_matchup.py` (2,583 lines — the largest Sandlot module),
`sandlot_win_week.py` (1,388), `sandlot_receipts.py` (1,093), `sandlot_trade_outcomes.py` (853),
`sandlot_data_quality.py` (791), `sandlot_trade_evidence.py` (616), `sandlot_execution.py` (534),
`sandlot_attention.py` (488), `sandlot_pitcher_opportunities.py` (423),
`sandlot_future_games.py` (401), plus `sandlot_lineup.py`, `sandlot_autopsy.py`,
`sandlot_scoring.py`, `sandlot_calibration.py`, `sandlot_config.py`.

`docs/sandlot-railway-v1.md:102-111` has the same problem for routes: its "## API" section lists 8
of the 27 declared in `sandlot_api.py`, omitting every execution-request, receipt, outcome,
learning, attention, hot-swap, win-this-week, trades/incoming and readiness route.

---

# Safety properties: claimed vs. enforced

The brief flagged these as the most serious possible class of finding. **All four hold.** Traced
individually:

### 1. "The public API exposes only a sanitized latest active receipt" — `docs/ARCHITECTURE.md:56`

✅ **Sanitization holds.** `sandlot_api.py:1493-1560` (`_public_recommendation_receipt`) builds a
fresh dict from an explicit allow-list of keys; it never spreads the raw DB row. Raw projection
inputs are not among them. `sandlot_api.py:229-237` returns `204 No Content` with
`Cache-Control: no-store` when nothing is active, matching `docs/recommendation-receipts.md:60-62`.

⚠️ **"only … latest active" is now imprecise.** Two additional public receipt-derived routes exist:
`sandlot_api.py:240` `/api/recommendation-outcomes/recent` and `sandlot_api.py:268`
`/api/recommendation-learning`. Both are separately sanitized
(`_public_recommendation_outcome`, `_public_recommendation_learning`), so the *safety* property is
intact — but the surface is three routes, not one, and the doc says one. Also unstated:
`/latest` is Monday-lineup-only (`source: Literal["monday_lineup"]`, `sandlot_api.py:230`), so
`trade_assessment` receipts have no public read route at all.

### 2. "Owner decisions travel through the loopback-only `sandlot_owner_bridge.py`" — `docs/ARCHITECTURE.md:57`

✅ **Enforced, defense-in-depth.** `sandlot_owner_bridge.py:25` `DEFAULT_BIND = "127.0.0.1"`;
`:663` restricts `--bind` to `{127.0.0.1, ::1}` via `choices`; `:188-193` `_host_is_loopback()`
validates the `Host` header against `{localhost, 127.0.0.1, ::1}`; `:229-246` rejects any request
failing host or origin checks with 403. Upstream target is validated as an uncredentialed HTTPS
origin at `:47-60`; plain HTTP allowed only for loopback (`:67-68`).

### 3. "The production browser never receives the owner bearer" — `docs/ARCHITECTURE.md:57`

✅ **Enforced.** `grep -n "Bearer\|authorization" web/sandlot/v2-pages.jsx` returns no auth header
construction. All owner-scoped calls go to `V2_OWNER_BRIDGE_URL = 'http://127.0.0.1:8765'`
(`v2-pages.jsx:52`) at `:1452`, `:1508`, `:1532`, `:1803`, `:3877`, `:1853`. The bridge adds the
bearer only on its server-to-server hop (`sandlot_owner_bridge.py:79-88`), and requires the token be
≥16 chars locally (`:78-79`). Server-side, `sandlot_api.py:1738-1747` (`_require_hashed_role`)
compares against a SHA-256 digest env var and 401s without it — the plaintext never exists on
Railway.

### 4. "The bridge and API both assert `fantrax_changed=false` and `writes_enabled=false`" — `docs/ARCHITECTURE.md:58-59`

✅ **Enforced on both sides, and the API side is stronger than claimed.**
- Bridge: `sandlot_owner_bridge.py:431` and `:456` —
  `if body.get("fantrax_changed") is not False or body.get("writes_enabled") is not False:` → reject.
  `:619` additionally requires `mode == "dry_run"`.
- API: `sandlot_api.py:391-392` does not merely *assert* — it hard-sets
  `result["fantrax_changed"] = False; result["writes_enabled"] = False` on every decision response,
  so no upstream value can leak through. Same pattern at `:679-680`, `:1578-1579`, `:1706`.
- Contract layer: `sandlot_execution.py:124` refuses to build a dry-run request unless the source
  proposal already carries `executable is False` and `writes_enabled is False`.

### 5. `docs/sandlot-execution-dry-run.md` — verified end to end

Every checkable claim in this 226-line doc holds:

| Claim | Doc | Code |
|---|---|---|
| Request TTL ≤ 120 s | `:19` | `sandlot_execution.py:21` `REQUEST_TTL_SECONDS = 120` |
| Lease TTL ≤ 90 s, never requeued | `:19` | `sandlot_execution.py:22` `LEASE_TTL_SECONDS = 90` |
| Terminal states | `:69-74` | `sandlot_execution.py:23` `TERMINAL_STATES` — exact match |
| "no more than five minutes old" | `:22` | `sandlot_matchup.py:1602` `"preflight_snapshot_max_age_minutes": 5`, bounds-enforced `1..15` at `sandlot_execution.py:157` |
| Three-variable kill switch | `:29-35` | `SANDLOT_EXECUTION_DRY_RUN_ENABLED` / `..._OWNER_ACTION_TOKEN_SHA256` / `..._RUNNER_TOKEN_SHA256` all present in code |
| dry_run only; simple 2-player swap only | `:10-11` | `sandlot_execution.py:117-127` raises `ExecutionContractError` on any other mode/shape |
| "no route that calls Fantrax after preflight" | `:76` | Route list confirms: `sandlot_api.py:421/454/466/487` and nothing beyond |
| Runner script exists | `:132` | `scripts/sandlot_execution_runner.py` |

`docs/sandlot-automation.md` (51 lines) is likewise fully verified against
`.github/workflows/sandlot-automation.yml` (jobs `monitor` `:29`, `report-monitor-failure` `:69`,
`close-recovered-monitor-issue` `:148`, `executor-contract` `:186` checking out `refs/pull/63/head`
at `:211`) and `scripts/sandlot_readonly_monitor.py`.

**Conclusion on safety: no claimed safety property is unenforced.** The safety-critical docs
(`sandlot-execution-dry-run.md`, `sandlot-automation.md`, the safety paragraphs of
`recommendation-receipts.md` and `PRODUCT.md`) are the *best* documents in the set. The rot is in
the orientation and status docs.

---

# Internal contradictions between the governing docs

`docs/ARCHITECTURE.md:33` says: "If these disagree, resolve the conflict before implementation."
The disagreements it would trigger on:

| Topic | Doc A | Doc B | Winner |
|---|---|---|---|
| Frontend build pipeline | `docs/ARCHITECTURE.md:20, 254-265` — Babel, `window.*`, `data.jsx` | `CLAUDE.md:33-42`, `AGENTS.md:59-63`, `docs/SANDLOT-HANDOFF.md:36-44` — esbuild, ES modules | **B** (code) |
| Skipper model order | `.env.example:29`, `docs/sandlot-skipper-chat-plan.md:11`, `docs/SANDLOT-HANDOFF.md:173` — Kimi/Tencent first | `CLAUDE.md:25, 51, 54`, `docs/SANDLOT-HANDOFF.md:119` — DeepSeek first | **B** (`sandlot_skipper.py:33-34`) |
| Counterfactual scorer shipped? | `docs/recommendation-receipts.md:190-198` — not shipped | `docs/ARCHITECTURE.md:245-252`, `STATUS.md:91-104` — shipped | **B** (`sandlot_receipts.py:25`) |
| Lineup evidence version | `ARCHITECTURE.md:65` / `receipts:167` / `STATUS:383` — v2 | — | **Neither**; code is v3 |
| Tab count | `docs/SANDLOT-HANDOFF.md:68` — 6 | `AGENTS.md:66`, `DESIGN.md:208`, `PRODUCT.md:74` — 5 | **B** (`v2-pages.jsx:674-680`) |
| Today's identity | `docs/SANDLOT-HANDOFF.md:72`, `docs/today-page-roadmap.md` — roster-health dashboard | `PRODUCT.md:58`, `DESIGN.md:225`, `AGENTS.md:44` — Attention Queue | **B** |
| Executor endpoint | `STATUS.md:150, 228` — live, fail-closed 503 | `docs/sandlot-automation.md:26-39` — draft PR #63, not merged | **B** (no route exists) |
| `localStorage` | `CLAUDE.md:38` — none | `docs/sandlot-railway-v1.md:67-71` — set `sandlot_refresh_token` | Both partly right; carve-out undocumented in `CLAUDE.md` |
| Nav order | `AGENTS.md:66`, `DESIGN.md:208` — Today, Roster, Adds, League, Skipper | `v2-pages.jsx:674-680` — Today, Roster, **Skipper**, Adds, League | Set matches; order differs. `docs/quality/quality-loop-progress.md` already flags this as an open question. Low risk. |

---

# Orphaned and superseded documents

None of these carry a status header. A newcomer cannot distinguish binding from historical.

**Superseded plan docs (would cause harm if followed):**

- `docs/sandlot-v1-execution-plan.md:7-9` — "Do not build trade ML, add/drop execution, lineup
  execution, or Skipper chat yet." All four now exist.
- `docs/sandlot-skipper-chat-plan.md:11` — "Models: Kimi (Moonshot) primary, DeepSeek V4 Flash
  fallback." Inverted vs `sandlot_skipper.py:33-34`.
- `docs/today-page-roadmap.md:1-8` — describes Today as matchup donut + "In action / Idle /
  Injured," explicitly the design that `PRODUCT.md:58` and `DESIGN.md:225` replaced.

**Historical, harmless but unlabeled:** `docs/sandlot-player-card-v2-plan.md`,
`docs/sandlot-waiver-swaps-skipper-brief-plan.md`, `docs/quality/hot-swaps-data-readiness-plan-2026-06-22.md`,
`docs/superpowers/specs/2026-05-03-*.md` (3 files), `docs/mocks/*.html` (2 files).

**`docs/quality/second-opinion/` — 14 files, 7 prompt/result pairs, all 2026-06-21 → 2026-06-23.**

- **All are historical.** Every one reviews a Hot Swaps slice that has since shipped (PRs #85–#89
  per `STATUS.md:308-372`). None is a standing rule.
- **The only still-binding file in `docs/quality/` is `second-opinion-gate.md`** (48 lines) — a
  reusable prompt template with a live invocation, plus `sandlot-quality-loop.md` (the loop spec)
  and `user-story-inventory.csv` (the tracker).
- **Two records document that the external review never happened.**
  `hot-swap-movability-gate-2026-06-23-result.md:1-5` — "Blocked by environment privacy policy …
  No workaround attempted," followed by an *internal* skeptical review.
  `hot-swap-time-aware-contract-2026-06-23-result.md` is the same shape. `STATUS.md:322-324` and
  `:346-347` acknowledge this. A reader who assumes the directory contains genuine external second
  opinions would over-trust two of the seven.
- **The quality loop is stalled and invisible from `STATUS.md`.**
  `docs/quality/quality-loop-progress.md` ends with "Run Phase 2 against
  `user-story-inventory.csv`." That CSV has 58 rows: **56 `specified`, 2 `passed`** — Phase 2 was
  essentially never executed. `STATUS.md` never mentions the quality loop, so the agent told to
  read `STATUS.md` first will not learn an open QA tracker exists.

---

# Undocumented surface (MISSING)

Modules with **zero mentions by name in any `.md` file** in the repo:

| Module | Lines | Note |
|---|---|---|
| `sandlot_win_week.py` | 1,388 | `docs/win-this-week.md` documents the *feature* over 309 lines and never names the file. Grep-invisible. |
| `sandlot_receipts.py` | 1,093 | Same: `docs/recommendation-receipts.md` (218 lines) never names the module that implements it. Holds every builder/scoring version constant. |
| `sandlot_trade_outcomes.py` | 853 | Implements `fantrax_player_period_fpts_v1` / `trade_static_package_asset_points_v1` — both described at length in `ARCHITECTURE.md:200-223` without a file reference. |
| `sandlot_trade_evidence.py` | 616 | — |
| `sandlot_pitcher_opportunities.py` | 423 | Implements `verified_gs_cadence_v1`, described in `docs/sandlot-matchup-projection-model.md:15-24`. |

Modules named in **no** core doc (`docs/ARCHITECTURE.md`, `CLAUDE.md`, `AGENTS.md`, `PRODUCT.md`,
`README.md`) — only in `STATUS.md` or a plan doc, if at all:

| Module | Lines |
|---|---|
| `sandlot_matchup.py` | **2,583** — largest Sandlot module; holds `MODEL_VERSION` and the execution freshness policy at `:1602` |
| `sandlot_data_quality.py` | 791 — the slot-provenance fail-closed gate everything depends on |
| `diagnose_slot_provenance.py` | 542 |
| `sandlot_execution.py` | 534 — the entire dry-run contract layer |
| `sandlot_attention.py` | 488 — the Attention Queue, i.e. the product's core surface |
| `fantrax_dom.py` | 461 |
| `sandlot_future_games.py` | 401 |
| `sandlot_autopsy.py` | 334 |
| `sandlot_lineup.py` | 263 |
| `sandlot_scoring.py` / `sandlot_calibration.py` / `sandlot_config.py` | 89 / 28 / 27 |

Roughly 8,000 lines of the highest-consequence Python — the projection engine, the attention queue,
the data-quality gate, and the execution contract — are reachable only by reading `STATUS.md`
narrative or by grepping.

---

# Verdict

**No. This documentation set cannot currently be trusted as ground truth, but the failure is
localized and the repair is small.**

Three tiers:

**Trustworthy (verified against code, no divergences found):**
`docs/sandlot-execution-dry-run.md`, `docs/sandlot-automation.md`,
`docs/sandlot-matchup-projection-model.md`, `docs/win-this-week.md`, `PRODUCT.md`, `DESIGN.md`.
Every safety property these assert is enforced in code — several more strictly than claimed. The
maintainer's discipline on safety-critical documentation is genuinely high.

**Trustworthy with named exceptions:** `CLAUDE.md` (correct on models, freshness, nav states, build,
snapshot shape; wrong on CI scope, silent on 20 env vars), `AGENTS.md` (accurate, but its pointer at
`docs/ARCHITECTURE.md:14` is the delivery mechanism for the worst error),
`docs/recommendation-receipts.md` (two stale version claims),
`docs/sandlot-railway-v1.md` (accurate but lists 8 of 27 routes).

**Not trustworthy:** `docs/SANDLOT-HANDOFF.md` (wrong in nearly every number, and contains an
instruction that deletes four governing docs), `STATUS.md` (10 days and 4 PRs behind, asserts a
route that does not exist, ~40% unverifiable production trivia), `README.md` (describes a repo
that no longer exists), and the **Frontend Boundaries section of `docs/ARCHITECTURE.md`** (never
true since the file was created).

**The structural problem** is that the doc set has no staleness signal. Nothing distinguishes
`docs/sandlot-execution-dry-run.md` — verified accurate to the constant — from
`docs/SANDLOT-HANDOFF.md` — wrong in ~10 places and dangerous in one. Both sit in `docs/`, both read
as authoritative present tense. An agent has no basis for weighting them differently, which means
the good documentation provides no protection against the bad.

**Highest-leverage repairs, in order:**

1. Delete `docs/ARCHITECTURE.md:254-265` and fix `:20`. Single highest-risk item; it is the section
   `AGENTS.md` sends every agent to, and it was never correct.
2. Add a `> HISTORICAL — superseded, do not act on this` header to `docs/SANDLOT-HANDOFF.md`, or
   delete it. At minimum remove `:215-217` (the delete-the-governing-docs instruction).
3. Fix `STATUS.md:150` and `:228` to say PR #63 is an unmerged draft with no deployed route.
4. Add the three execution-control env vars to `CLAUDE.md:22-29`; fix `.env.example:29`'s inverted
   model order.
5. Global `fantrax_period_lineup_v2` → `v3` in the three docs; strike
   `docs/recommendation-receipts.md:190-198`'s "until … ship" framing.
6. Add a one-line status header to the five superseded plan docs and to
   `docs/quality/second-opinion/`.
