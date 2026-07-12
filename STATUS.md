# STATUS

> Living next-steps file. Update this at the end of any session that changes the plan.
> Last updated: **2026-07-12** (recommendation receipt foundation in progress).

## Where things stand

- **Recommendation receipt foundation in progress (#92):** branch
  `feature/92-proposal-ledger` adds durable, versioned decision-time evidence
  for the Monday lineup optimizer. Receipts are scoped to an exact target week,
  retain projection inputs and baseline/proposed assignments after snapshot
  pruning, fail closed on untrusted Fantrax slot provenance, and supersede
  changed pending evidence rather than overwriting it. This slice does not yet
  expose accept/reject controls or outcome scoring; those remain the next
  receipt loops before any action type can earn autopilot.

- **One-click proposal safety check in progress:** the exact-action review
  sheet can connect to a loopback-only owner bridge and submit the immutable
  lineup proposal with one explicit confirmation click. The bridge retains the
  owner bearer locally, enforces exact Origin/Host/nonce/PNA guards, and proxies
  only sanitized request status. The separate visible runner still performs a
  zero-click, zero-write live preflight; no Fantrax mutation path is added.
  Current verification: 381 Python tests and the local mobile Today flow are
  green. Production remains disabled until the branch is reviewed and merged.

- **Zero-AI-cost maintenance loop prepared:** `.github/workflows/sandlot-automation.yml`
  checks read-only production contracts after the existing twice-daily Railway
  refresh and maintains one sanitized GitHub issue while failures persist. It
  does not call OpenAI, spend Codex credits, generate code, or publish PRs. A
  weekly/manual executor job first enforces exact-proposal binding, freshness,
  live preflight, post-write verification, and protected-asset safeguards, then
  runs PR #63's mocked guard tests with no Fantrax credentials or action token.
  Live writes remain local, headful, and explicitly approved per action.
- **Analytics track landed (2026-07-03, branch `claude/debug-railway-crash-AvUM7`):**
  league-exact scoring (`sandlot_scoring`), weekly lineup-efficiency autopsy
  (`sandlot_autopsy` + Actions workflow), Monday lineup optimizer
  (`sandlot_lineup`, exact 20-slot assignment, strict SP/RP eligibility, cron
  Mondays 4am ET once on main), and a Skipper model-eval harness
  (deterministic graders, #87). Findings: mid-pack lineup efficiency but a
  bottom-third roster ceiling; holds RPs + two-start SPs are the mispriced
  waiver assets (league pays HLD 3.5 / IP 3 / QS 3). Follow-ups filed as
  #90-#95. Needs repo secret `OPENROUTER_API_KEY` for the first eval run.
- **Fixes in the same branch:** Skipper mid-stream fallback no longer splices
  two models' replies (raises instead once tokens flowed); SSE tokens now
  stream live with a `replace` event for repaired refusals; `mlb_stats`
  player-index/team caches got TTLs + fail-open behavior; accented player
  names (Muñoz/Hernández) now resolve in MLB lookups.
- **`GET /api/attention` is live** ([#64](https://github.com/zoelsner/baseball/issues/64) / [PR #65](https://github.com/zoelsner/baseball/pull/65), merged + deployed). Returns the ordered queue with status-safe `POST /api/actions` payloads where allowed. Lineup hot-swap replacements now surface as blocked proposal cards, not ready-to-submit write payloads.
- **Executor [PR #63](https://github.com/zoelsner/baseball/pull/63) (draft):** reviewed, rebased onto main, CI green. First manual test run (2026-06-10): **all five deterministic guards PASS against prod, zero unintended writes** — but the Selenium layer failed safe (`player_row_not_found`) and needs a click-flow rewrite against the real Fantrax DOM. The DOM map (row anchor = headshot URL `hs{player_id}_`, two-click `lineup-btn` slot model, `remove`/`swap_horiz` icon actions) is in the PR comments.
- **Blocker — [#67](https://github.com/zoelsner/baseball/issues/67):** snapshot `slot` is the player's *position*, not their lineup slot (raw scrape never had it). Attention queue / roster health / waiver IL-protection all compute on wrong slots. Live truth as of 2026-06-10: Skubal + Woodruff already IR-stashed; **only Judge is IR in an active slot**; Condon/Montes are in dynasty `Min` slots (protected prospects).
- **#67 mitigation started:** `fantrax_data.extract_roster()` now carries
  `slot_source`; this branch adds a data-quality gate so lineup and add/drop
  recommendations fail closed when roster slot source is `position_fallback` or
  otherwise untrusted. This prevents confident hot-swap/waiver proposals while
  real lineup-slot extraction is still being proven.
- **#67 adapter hardening:** local `fantraxapi>=0.2.0` exposes
  `roster_info`, `row.pos`, and `row.fppg` rather than the newer-ish fields the
  scraper expected. The adapter now preserves raw roster responses for
  `statusId` slot provenance, tolerates fragile MLB future-game cells in
  `RosterRow`, recovers roster capacity from raw `statusTotals`, and keeps
  slot-based recommendations fail-closed when provenance is inferred.
- **Frontend Attention Queue gate:** the Today screen now enforces the same
  slot-provenance contract as the backend. It hides lineup/output/replacement
  advice unless `data_quality.lineup_slots.state == "ok"` and shows an explicit
  `Advice paused` state when active-slot data is inferred.
- **Skipper recommendation gate:** chat context and deterministic matchup
  replies now enforce the same contract. If
  `lineup_recommendations_ready`/`add_drop_recommendations_ready` is not
  explicitly true, Skipper omits stale recommendations, strips `slot` and
  `slot_source` from roster context, and says advice is paused instead of
  surfacing lineup/swap framing.
- **Explicit readiness contract:** lineup and add/drop recommendation surfaces
  now fail closed unless the action-specific readiness flag is explicitly
  `true`. `/api/attention`, matchup recommendations, waiver cards, Skipper,
  and the Today UI no longer fall back from missing
  `lineup_recommendations_ready`/`add_drop_recommendations_ready` to legacy
  `recommendations_ready`.
- **Lineup-only hot-swap card:** matchup recommendations now include a
  non-executable `replacement_card` with OUT/IN players, projected benefit,
  reason, short-term outlook, risk, confidence, provenance, and blocked
  `Propose swap` state. Today renders that as a richer Attention Queue card
  with `Ask Skipper` and `Deep research` handoffs. The card emits no add/drop,
  no `change_slot` payload, and no live Fantrax write path.
- **Opt-in refresh slot proof:** `sandlot_refresh` can now apply the read-only
  Fantrax roster DOM proof path during snapshot refresh when
  `SANDLOT_CAPTURE_ROSTER_DOM_SLOTS=1` is set and valid cookies are available.
  It captures page source only, parses `lineup-btn` slot text, applies trusted
  `dom.lineup-btn` overrides through `fantrax_data.apply_trusted_slot_overrides()`,
  and records top-level `slot_provenance` metadata. Capture errors are
  non-fatal and do not populate snapshot `errors`, so recommendations remain
  fail-closed when slot proof is unavailable.
- **Local verification:** Python unit suite is green on 2026-06-22
  (`157 tests`). The local rebuilt Sandlot bundle still builds with direct
  `esbuild`; this sandbox shell has no Node/npm/npx available for a same-run
  local Playwright rerun, so PR #81's GitHub `Local frontend E2E` job remains
  the branch-owned browser regression proof. Earlier local Playwright coverage
  verifies unsafe replacement cards are hidden when slot provenance is partial
  or explicit lineup readiness is missing, and the hot-swap card names OUT/IN
  players, keeps `Propose swap` disabled, and seeds Skipper with the proposed
  swap. Live read-only Fantrax verification is still blocked in this checkout
  because there are no local cookies/env credentials and Chrome cookie import
  times out on macOS keychain access.
- **CI split:** Railway Playwright remains a deployed-app smoke. PR #81 now
  adds a separate `Local frontend E2E` job for branch-only UI regressions that
  must run against the rebuilt local bundle before Railway has deployed it.
- **Slot proof diagnostic:** `diagnose_slot_provenance.py` is the repeatable
  read-only proof tool for #67. It can check a snapshot URL/file, inspect a
  saved raw Fantrax `getTeamRosterInfo` JSON file, inspect a saved Fantrax
  roster-page DOM file for `lineup-btn` slot text, perform a live Fantrax
  roster read once cookies/env are available, and optionally capture the live
  roster DOM with `--capture-roster-dom`. Raw-payload mode reports candidate
  slot fields plus the current scraper's normalized assignment coverage. DOM
  mode can overlay `dom.lineup-btn` slots onto a matching snapshot through the
  same `fantrax_data.apply_trusted_slot_overrides()` helper now used by the
  opt-in refresh integration. Standalone raw/DOM evidence still cannot satisfy
  `--require-trusted` until normalized roster rows carry trusted `slot_source`
  values. Current production still reports `fail_closed`: 37 rows, 17 trusted,
  20 untrusted, and all 20 active rows untrusted.
- **Cookie fallback:** if `import_chrome_cookies.py` hangs on macOS keychain,
  copy a logged-in Fantrax request `Cookie:` header locally and run
  `pbpaste | .venv/bin/python import_fantrax_cookies_manual.py --cookie-header -`;
  then run
  `.venv/bin/python diagnose_slot_provenance.py --capture-roster-dom --require-trusted`.
  The helper writes `.cookies/fantrax.json` with `0600` permissions and does not
  print cookie values.
- **Not yet done:** Railway tokens (`SANDLOT_ACTIONS_TOKEN`, `SANDLOT_REFRESH_TOKEN`) unset — the executor endpoint is fail-closed (503) until then. Zo Computer not wired.
- **PR #81 shipped:** [#81](https://github.com/zoelsner/baseball/pull/81) was
  marked ready and squash-merged into `main` as `fc366f7`. Main push checks
  passed: GitHub `CI #133` (Python import/unit smoke, frontend build) and
  `Playwright #157` (Local frontend E2E, Railway production smoke). Production
  verification after deploy: `/api/attention` now returns `0` items while
  `/api/snapshot/latest` still exits `fail_closed` with all 20 active rows
  untrusted, proving unsafe lineup/output/replacement advice is suppressed
  until trusted Fantrax slot data exists.
- **Next hot-swap proposal slice:** branch
  `feature/hot-swap-proposal-safety` adds a read-only `proposal` object and
  visible safety checklist to lineup-only hot-swap cards. It keeps `Propose
  swap` blocked and does not enable Fantrax writes, Zo writes, add/drop, or
  trade automation. It now also adds `GET /api/hot-swaps/latest`, a read-only
  proposal endpoint that returns paused/ready/none state from the same
  fail-closed Attention Queue gate.
- **Hot Swaps Today slice:** the same branch now makes hot swaps a first-class
  Today surface instead of burying them inside the generic Attention Queue.
  Today splits replacement items into a dedicated **Hot Swaps** section above
  the remaining queue items. Browser smoke against a mocked local snapshot
  verified the rendered order, OUT/IN card, blocked `Propose swap`, and
  `Ask Skipper` handoff prompt. No new write path was enabled.
- **Production roster scrape fix:** production snapshot `213` on Railway was
  fresh but unusable: `/api/snapshot/latest` normalized to `roster: []` and
  stored `errors: ["roster: 'Roster' object has no attribute 'positions'"]`.
  Branch `fix/production-roster-scrape` makes `getTeamRosterInfo` raw payloads
  the primary roster parser for my roster and all-team rosters, bypassing the
  fragile upstream object parser, and marks refreshes `failed` when my-roster
  rows are missing or the roster section errors. Hot Swaps remain read-only and
  slot-provenance fail-closed. Local verification so far: focused scraper /
  refresh / recommendation tests passed, full Python suite passed, import smoke
  passed, `git diff --check` passed, and direct `esbuild` rebuild passed. First
  production deploy reached Railway as commit `43c743e` and both Railway
  services reported success; manual refresh run `295` then failed safely
  instead of promoting another empty roster snapshot. It still exposed the same
  scraper error, so the active follow-up patch makes raw helper failures fall
  back to `FantraxAPI._request`, then to a direct authenticated `fxpa/req`
  call, before touching the upstream `Roster` parser. Verification for that
  patch: roster regression tests passed, focused scraper / refresh /
  recommendation tests passed, full Python suite passed, and import smoke
  passed. Final Railway production verification succeeded on commit `ffb2b32`:
  manual refresh run `298` stored successful snapshot `217` with 37 roster
  rows, `errors: []`, sane FPts/FP/G values, and `/api/attention` returned no
  unsafe items. The browser no longer shows `first snapshot was empty` or
  `Waiting for roster data`. Hot Swaps is now truthfully paused, not broken:
  `/api/hot-swaps/latest` reports `state: paused` because future-game coverage
  is missing and lineup-slot provenance is still partial. Claude Opus xhigh
  reviewed the checkpoint and agreed to frame this as data-integrity recovery
  plus honest pause, not Hot Swaps enablement.
- **Zo hot-swap safety issue:** [#82](https://github.com/zoelsner/baseball/issues/82)
  tracks the future Zo confirmation/protected-player action architecture.
- **Hot Swaps data-readiness loop:** branch `feature/hot-swaps-data-readiness`
  is scoped to the two production blockers still pausing Hot Swaps: future-game
  schedule provenance and trusted active-slot provenance. Claude Opus xhigh
  reviewed the focused plan and pushed one important architecture change:
  global future-game coverage should be diagnostic, while actual Hot Swap
  readiness should be proposal-participant scoped. The accepted plan is
  hitter-ready first, pitcher-safe by construction, and read-only throughout:
  pitchers cannot consume hitter team-game counts, and pitcher swap proposals
  stay blocked unless explicit probable-start provenance exists. Local
  implementation progress on this branch now adds MLB schedule/team-id
  normalization, refresh-time schedule enrichment for my roster and opponent
  rosters, provenance-aware future-game quality, lower-bound projection
  counting, proposal-participant hot-swap gates, and active-row DOM slot proof
  diagnostics. Verification so far: targeted backend tests passed (`73`
  tests), full Python suite passed (`173` tests), `git diff --check` passed,
  and direct `esbuild` rebuild passed. Production deploy/refresh/browser
  verification is still pending.
- **PR #84 merged + production checkpoint:** [PR #84](https://github.com/zoelsner/baseball/pull/84)
  was squash-merged into `main` as `01ab4dc`. A real Railway refresh run
  `300` stored snapshot `219` with 37 roster rows, `errors: []`, and
  future-game coverage `ok` for 40/40 players. Hot Swaps is still read-only
  and paused for one remaining reason: lineup-slot provenance is trusted for
  only 17/37 roster rows. Follow-up branch `fix/raw-posid-slot-provenance`
  treats active Fantrax raw rows with `statusId=1` and `posId` as trusted
  `raw.posId` lineup-slot evidence. Against production snapshot `219`, that
  upgrades the 20 active `position_fallback` rows and the local
  data-quality check changes lineup slots from `partial` 17/37 to `ok` 37/37.
  Verification on the branch: full Python suite passed (`174` tests) and
  `git diff --check` passed.
- **PR #85 merged + production Hot Swaps unpaused:** [PR #85](https://github.com/zoelsner/baseball/pull/85)
  was squash-merged into `main` as `940ee4f`. Railway refresh run `301`
  stored snapshot `220` with 37 roster rows, `errors: []`, lineup slots
  `37/37 ok`, and future-game coverage `40/40 ok`. `/api/hot-swaps/latest`
  returned `state: ready` with one read-only lineup proposal: move TJ Friedl
  out and Ildemaro Vargas in for a projected `+9.1` points. Production Today
  renders the OUT/IN card, source/provenance, safety checklist, Ask Skipper,
  and Deep research, while `Propose swap` remains blocked.
- **Hot-swap movability gate:** branch `feature/hot-swap-movability-gate`
  adds a read-only lock/movability layer before any future execution work.
  `raw.scorer.disableLineupChange === true` is treated as `locked`,
  explicit `false` as `movable`, and missing/non-boolean data as
  `unknown`. Locked or unknown proposals remain visible as recommendation
  candidates but never executable; the card and safety checklist now surface
  the movability state. Claude Opus xhigh review was attempted but blocked by
  environment privacy policy, so the same review questions were handled in
  `docs/quality/second-opinion/hot-swap-movability-gate-2026-06-23-result.md`.
  Verification so far: focused backend tests passed (`39` tests), full Python
  suite passed (`177` tests), `git diff --check` passed, direct native
  `esbuild` rebuild passed, and a production-shaped local check against live
  snapshot `221` labeled the current TJ/Ildemaro proposal `locked` because
  both rows have `raw.scorer.disableLineupChange: true`.
- **PR #86 merged + production verified:** [PR #86](https://github.com/zoelsner/baseball/pull/86)
  was squash-merged into `main` as `627aa58`. Main checks passed, Railway
  deployed the new payload, and direct production verification showed
  `/api/hot-swaps/latest` returning the current TJ Friedl/Ildemaro Vargas
  proposal with `movability.state = locked`, `fantrax_movability = blocked`,
  `writes_enabled = false`, and the Today UI rendering `Locked`, `Fantrax
  movability`, and `Propose swap blocked`.
- **Time-aware hot-swap contract production verified:** [PR #88](https://github.com/zoelsner/baseball/pull/88)
  was squash-merged into `main` as `fcdc1e2`. This added the next read-only
  safety layer.
  The proposal now carries a non-executable contract with stable OUT/IN
  players, ordered slot moves for direct and multi-step chains, projected
  benefit, movability state, blocked gates, confirmation copy, and input hash.
  Movability is now conservative across Fantrax raw lock data plus MLB schedule
  game-start timing: a started game locks the proposal even if Fantrax says the
  row is movable, and a same-day game missing start time stays `unknown`.
  Claude Opus xhigh review was blocked by tenant policy; the recorded internal
  checkpoint fixed the multi-step contract gap and a datetime/date parser edge
  case. Verification: focused recommendation/attention tests passed (`41`
  tests), full Python suite passed (`179` tests), `git diff --check` passed,
  direct native `esbuild` rebuild passed with no bundle diff, PR #88 checks
  passed, and production `/api/hot-swaps/latest` now returns
  `proposal.contract`, `proposal.executable = false`, complete ordered
  `slot_moves`, `requires_multi_step = true`, `movability.state = locked`, and
  `writes_enabled = false`. Browser verification on the real Railway URL shows
  the locked Ildemaro Vargas for TJ Friedl card, safety checklist, Ask
  Skipper, Deep research, no `first snapshot was empty`, no waiting-roster
  error, and no console errors.
- **Ramp-oriented Today context slice production verified:** [PR #89](https://github.com/zoelsner/baseball/pull/89)
  was squash-merged into `main`, then Railway was forced to redeploy after the
  first production smoke caught a stale frontend bundle. Production now serves
  `app.js?v=33b257ebc7c8`. The real Railway Today page shows Matchup first,
  Hot Swaps second, and Attention Queue after the action surface; Hot Swaps
  now explains the current matchup context (`Trailing by 14.0`, `6d left`) and
  the projected benefit (`+9.1`) before the execution gates. The card also
  renders the blocked `Propose swap`, `Ask Skipper`, and `Deep research`
  actions above the long evidence section. Main checks passed (`Frontend
  build`, `Python import smoke`, `Local frontend E2E`, and `E2E against
  Railway`). Direct production API verification on snapshot `221` shows 37/37
  trusted lineup slots, 40/40 eligibility and FP/G coverage, future-game
  coverage present, one read-only proposal in `/api/hot-swaps/latest`, and no
  `first snapshot was empty` or waiting-roster regression. Browser verification
  on the real Railway URL showed no console errors.

## Next steps, in order ([#66](https://github.com/zoelsner/baseball/issues/66) tracks activation)

1. **Wire the hot-swap proposal confirmation path** — keep `Propose swap`
   disabled until #63's executor safety can accept a lineup-only proposal with
   named OUT/IN players, trusted slot provenance, trusted movability, a
   preflight refresh, Zach confirmation, and post-write verification.
2. **Add the eval/decision receipt surface** — expose source, model version,
   input hash, accepted/rejected outcome, and later hindsight result so the app
   demonstrates AI evaluation and QA principles for the Ramp story.
3. **Finish #67 real-slot proof archival** — the production gate is now clear
   via raw `posId`, but keep `diagnose_slot_provenance.py` available for DOM
   slot proof if Fantrax changes raw roster semantics.
4. **Rework #63's Selenium flows** against the DOM map on the PR. Add the hard guard: refuse `drop_player` for `Min`/IL-slot players. Cloud-friendly to write; not to test.
5. **Re-run write scenarios (3/5/6/7b) locally, headful, with Zach watching.** Judge is the real IL-move target. Local-only — needs Mac + Fantrax creds.
6. **Set Railway tokens**, verify 503→401 behavior by curl.
7. **Merge #63, wire Zo** — phase 1 vocabulary only (`move_to_il`, `change_slot`). One real end-to-end loop (queue -> Telegram -> yes -> executed -> `action_logs` row) closes #66.

## Safety rules (non-negotiable — full text on [#66](https://github.com/zoelsner/baseball/issues/66#issuecomment-4695871271))

No write without Zach watching + approving the named player · phased Zo vocabulary (slot moves → adds → maybe drops) · `Min`/IL prospects undroppable · no add/drop recommendations until #67 lands · fail closed everywhere.

## Cloud session kickoff (paste this on your phone)

> Read CLAUDE.md and STATUS.md, then issues #67 and #66 and the two Claude comments on PR #63 (review summary + manual test results with the DOM map). Work on #67: find the real lineup-slot source — check the installed fantraxapi package for roster sections or status fields the scrape ignores; if the API truly lacks it, implement the lineup-btn DOM read described on PR #63. Fix `extract_roster`, treat `Min` as a reserved slot alongside IL/IR (v2StarterRows in v2-pages.jsx, RESERVED_SLOTS in sandlot_attention.py, waiver IL-stash protection), and add unit tests using the live roster shape from the PR comment. Branch `fix/67-roster-slots`, PR when CI is green. Don't touch the executor write paths, and never attempt live Fantrax writes — those run locally with Zach watching.
