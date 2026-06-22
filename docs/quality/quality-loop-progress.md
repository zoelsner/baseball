# Quality Loop Progress

## 2026-06-21

- Wrote the canonical loop spec in `docs/quality/sandlot-quality-loop.md`.
- Created the canonical tracker in `docs/quality/user-story-inventory.csv`.
- Completed Phase 1 inventory from the current app shell, V2 UI code, FastAPI
  routes, and existing test surfaces.
- Covered app shell, Today, Roster, League, Adds, Trade, Settings, Player Sheet,
  Skipper, and public/internal API surfaces used by the app or agent flows.
- Noted Phase 2 watchlist items while inventorying:
  bottom nav order versus desired Skipper placement, hidden Settings reachability,
  and the Trade grade CTA visual disabled affordance.
- Added a Second-Opinion Gate to the loop: before API/data-model/security/model
  tool-calling design hardens, run `claude -p` with a skeptical senior-review
  prompt and record accepted/rejected findings.
- Added reusable prompt template `docs/quality/second-opinion-gate.md` so the
  gate applies to future features, refactors, APIs, model tools, migrations,
  and architecture changes.
- Added a trial prompt for this project at
  `docs/quality/second-opinion/skipper-web-fallback-2026-06-21.md`.

## Second-Opinion Gate Notes

- 2026-06-21: Attempted to locate `claude` for the Skipper web-search fallback
  API/tool-calling design. No `claude` binary was available on PATH or in the
  usual local install paths from this Codex shell, so the external gate has not
  run yet.
- Until `claude -p` is available, use the same prompt as an internal red-team
  review and mark the design as internally reviewed only.
- 2026-06-21: Installed/authenticated Claude Code on the machine and ran the
  external gate for Skipper web fallback with `~/.local/bin/claude -p`.
  Result captured in
  `docs/quality/second-opinion/skipper-web-fallback-2026-06-21-result.md`.
  Accepted findings changed the design to render real URL citation sources,
  split web-search availability/default state, avoid duplicate web-tool spend
  on fallback retries, and add tests for those behaviors.
- 2026-06-21: Implemented the Skipper nav move and Skipper web fallback after
  the second-opinion gate. Updated tracker rows `APP-004` and `SKP-009` with
  local unit/browser evidence.
- 2026-06-22: Started the hot-swap safety execution path on
  `fix/attention-slot-reliability`. GitHub issue creation was blocked by a
  connector 403 and no local `gh` binary, so the issue draft was written to
  `docs/quality/issue-drafts/zo-safe-roster-action-confirmation.md`.
- 2026-06-22: Added the first #67 mitigation gate: lineup and add/drop
  recommendations now require trusted roster slot provenance and fail closed
  when `slot_source` is `position_fallback`. Generic trade grading remains
  available because it does not depend on active/bench slot execution.
- 2026-06-22: Hardened the Fantrax roster adapter for the installed
  `fantraxapi>=0.2.0` shape: preserve raw `getTeamRosterInfo` responses,
  support `roster_info`/`row.pos`/`row.fppg`, patch fragile current-version
  `RosterRow` MLB future-game parsing, recover roster capacity from raw
  `statusTotals`, and add regression coverage for trusted `statusId` slots.
  Focused adapter/recommendation tests and the full Python suite pass locally
  (`113 tests`).
- 2026-06-22: Live read-only Fantrax verification remains blocked locally:
  this checkout has no `.env`, `FANTRAX_COOKIES_JSON`, or
  `.cookies/fantrax.json`; `import_chrome_cookies.py` timed out after 30s,
  likely waiting on macOS keychain access. Do not mark #67 complete until a
  real authenticated refresh proves active slot coverage.
- 2026-06-22: Attempted the Second-Opinion Gate with
  `~/.local/bin/claude -p --model opus --effort xhigh`; Claude returned
  `session limit · resets 12:20pm (America/New_York)`. Internal skeptical
  review found and fixed the current-version `RosterRow` parser gap.
- 2026-06-22: GitHub connector writes were blocked by `403 Resource not
  accessible by integration`, and no local `gh` binary is installed. Used the
  existing macOS git credential helper with the GitHub REST API to open draft
  PR [#81](https://github.com/zoelsner/baseball/pull/81) and safety issue
  [#82](https://github.com/zoelsner/baseball/issues/82).
- 2026-06-22: Queried production read-only APIs for snapshot `209`
  (`2026-06-22T13:02:23Z`). The live Attention Queue still emitted a
  replacement action chain (`Daniel Schneemann -> OF; Brooks Lee -> 3B;
  TJ Friedl -> RES`) even though production slot provenance showed
  `position_fallback` for 20/37 roster rows, including active lineup rows.
  Feeding those production rows into this branch's local data-quality gate
  returned `lineup_recommendations_ready=False` and
  `add_drop_recommendations_ready=False`. After tightening
  `sandlot_attention`, the same production-shaped input returns no Attention
  Queue swap/output/replacement items from local branch code, proving PR #81
  blocks that unsafe recommendation class until real active-slot extraction
  lands.
- 2026-06-22: User explicitly waived the blocked Claude gate for this slice
  after repeated `claude -p --model opus --effort xhigh` session-limit
  failures. Continued with internal skeptical review and documented the waiver
  here rather than stalling the hot-swap safety work.
- 2026-06-22: Closed the frontend gap in `web/sandlot/v2-pages.jsx`: the Today
  Attention Queue now preserves slot provenance, requires
  `data_quality.lineup_slots.state == "ok"` before showing lineup/output or
  replacement advice, and renders an explicit `Advice paused` state for
  untrusted active-slot data. Local browser verification against the rebuilt
  bundle proves the unsafe replacement card is hidden and the pause explanation
  is visible. Added Playwright regression coverage in
  `tests/playwright/specs/today-attention.spec.ts`.
- 2026-06-22: Verification after the frontend gate: `build:sandlot` passed,
  local Playwright against `http://127.0.0.1:4173` passed
  (`today-attention.spec.ts`, 3 tests), focused Python safety tests passed
  (`51 tests`), full Python suite passed (`115 tests`), and `git diff --check`
  passed. A direct Railway Playwright run intentionally still shows the unsafe
  production behavior until PR #81 is deployed.
- 2026-06-22: Initial PR CI showed why branch-only frontend regressions should
  not run inside the Railway production smoke: `E2E against Railway` failed on
  the new paused-advice assertion because production is not yet deployed from
  PR #81. Added a separate `Local frontend E2E` workflow job that builds
  `web/sandlot/app.js`, serves the rebuilt app on localhost, and runs the
  slot-provenance Attention Queue regression with
  `SANDLOT_EXPECT_SLOT_GATE=1`; Railway E2E keeps the same deployed-app smoke
  responsibility.
- 2026-06-22: Added `diagnose_slot_provenance.py`, a read-only diagnostic for
  the remaining #67 proof gap. It can inspect a Sandlot snapshot URL/file or,
  once cookies are available, perform a live Fantrax roster read without
  writing Fantrax actions, DB rows, snapshots, or cookies. The command reports
  row-level `slot_source` coverage, active untrusted rows, data-quality
  `lineup_slots`, raw status/key histograms for live reads, and exits `2` under
  `--require-trusted` unless roster-slot provenance is fully trusted.
- 2026-06-22: Ran the diagnostic against
  `https://web-production-90664.up.railway.app/api/snapshot/latest`; result is
  still `fail_closed`: 37 roster rows, 17 trusted, 20 untrusted, all 20 active
  rows untrusted, slot sources `{"position_fallback": 20, "raw.statusId": 17}`.
  This is current production evidence that PR #81's gates are still necessary
  until authenticated active-slot extraction is proven.
- 2026-06-22: Ran `claude -p --model opus --effort xhigh` for a skeptical
  review of the new diagnostic. Accepted findings: make the diagnostic verdict
  measure slot provenance rather than full recommendation readiness; warn when
  rows lack any `slot_source` field; remove hardcoded `statusId == "1"` raw
  active assumptions by reporting slot keys per status id; add `.gitignore`
  protection for local tool/artifact directories; reuse existing
  `sandlot_data_quality` and `fantrax_data` provenance logic; add exit-code
  tests for `--require-trusted`.
- 2026-06-22: Tried to use the Codex Chrome path for signed-in Fantrax read-only
  proof. Chrome is running and the native host manifest is correct, but the
  Codex Chrome Extension is not installed/enabled in the selected Chrome
  profile, so browser control cannot read the signed-in page. Cookie inspection
  through the browser is also intentionally out of scope for Chrome-control
  safety rules. Added `import_fantrax_cookies_manual.py` as a keychain-free
  fallback: paste a Fantrax request `Cookie:` header locally, write
  `.cookies/fantrax.json` without printing values, then run
  `diagnose_slot_provenance.py --require-trusted`.
- 2026-06-22: Ran `claude -p --model opus --effort xhigh` on the manual cookie
  fallback before commit. Accepted findings: remove the unnecessary `auth.py`
  import so the manual path stays independent of Selenium/webdriver imports,
  write the cookie file atomically with `0600` permissions, warn when secrets
  are passed inline on the command line, stop printing all cookie names, and add
  tests proving no cookie values are printed.
- 2026-06-22: Audited Skipper as another recommendation surface. Found that a
  snapshot carrying stale `matchup.recommendations` could still put lineup-swap
  advice in chat context or deterministic matchup text even when
  `data_quality.lineup_recommendations_ready` was false. Added a Skipper-local
  fail-closed gate: omit stale recommendations, strip `slot`/`slot_source`
  fields from chat context when lineup slots are not explicitly trusted, add
  `lineup_advice`/`add_drop_advice` paused blocks, and pause deterministic
  move/watch/position reads until slot provenance is trusted.
- 2026-06-22: Ran two `claude -p --model opus --effort xhigh` reviews on the
  Skipper gate. Accepted findings: gate the ungated "Move read" sentence,
  strengthen tests so swap framing cannot slip through, and strip/test
  `slot_source` as well as `slot`. Local focused Skipper/recommendation tests
  pass (`47 tests`), full Python suite passes (`135 tests`), and the production
  snapshot diagnostic still exits `fail_closed` with all 20 active rows
  untrusted.
- 2026-06-22: Resumed PR #81 without restarting the old loop. Confirmed pushed
  head `5f85995` was green across Railway smoke, local frontend E2E, frontend
  build, and Python import smoke. Tightened the remaining compatibility
  fallback: `/api/attention`, matchup recommendations, waiver cards, Skipper,
  and Today UI now require explicit `lineup_recommendations_ready === true` /
  `add_drop_recommendations_ready === true` before surfacing lineup or add/drop
  advice. Missing action-readiness flags now render a paused reason instead of
  falling back to legacy `recommendations_ready`.
- 2026-06-22: Verification for the explicit-readiness gate: focused backend
  safety tests passed (`53 tests`), full Python suite passed (`139 tests`),
  rebuilt `web/sandlot/app.js` with local `esbuild`, local Playwright against
  `http://127.0.0.1:4173` passed (`today-attention.spec.ts`, 4 tests,
  `SANDLOT_EXPECT_SLOT_GATE=1`), `git diff --check` passed, and the production
  read-only diagnostic still exits `fail_closed` with 20/20 active rows
  untrusted (`position_fallback`).
- 2026-06-22: Continued from green PR #81 into the lineup-only hot-swap card
  slice. `sandlot_matchup` now emits an explainable `replacement_card` for
  legal bench-to-active swaps with OUT/IN players, projected benefit, outlook,
  risk, confidence, source/provenance, safety flags, and blocked `Propose swap`
  execution metadata. `/api/attention` replacement items now carry that card
  with `action: null` and `actions: []`, so no lineup swap is ready-to-submit
  until the executor confirmation path is separately proven.
- 2026-06-22: Today renders the hot-swap proposal inside the Attention Queue
  with OUT/IN players, confidence/risk/source chips, Why/Outlook/Risk/Source
  lines, disabled `Propose swap blocked`, and `Ask Skipper` / `Deep research`
  handoffs. Local Playwright verifies the card ordering, blocked button,
  provenance display, and Skipper draft handoff. Slot-provenance paused states
  still hide replacement advice.
- 2026-06-22: External `claude -p --model opus --effort xhigh` review was not
  run for the hot-swap card because the sandbox reviewer rejected sending
  private project details to an external service. Kept the review prompt at
  `docs/quality/second-opinion/lineup-hot-swap-card-2026-06-22.md` and ran an
  internal skeptical pass instead. Accepted finding: skip real backend
  recommendations entirely when a complete OUT/IN `replacement_card` cannot be
  built, rather than falling back to vague replacement advice.
- 2026-06-22: Verification for the hot-swap card slice: focused backend safety
  tests passed (`52 tests`), full Python suite passed (`138 tests`), rebuilt
  `web/sandlot/app.js` with local `esbuild`, local Playwright against
  `http://127.0.0.1:4173` passed (`today-attention.spec.ts`, 4 tests,
  `SANDLOT_EXPECT_SLOT_GATE=1`), production-smoke-compatible local Playwright
  without the branch flag passed (`2 passed`, `2 skipped`), and
  `git diff --check` passed.
- 2026-06-22: Resumed PR #81 at pushed head `3d96d8a329149be41872b32168109ccf73fad071`.
  GitHub Actions was green for `Playwright` run `27975957250` (`Local frontend
  E2E`, `E2E against Railway`) and `CI` run `27975957259` (`Python import
  smoke`, `Frontend build`). Re-ran the production read-only slot diagnostic
  against `https://web-production-90664.up.railway.app/api/snapshot/latest`;
  it still exits `2` under `--require-trusted` with `fail_closed`, 37 roster
  rows, 17 trusted rows, 20 untrusted rows, and all 20 active rows untrusted.
- 2026-06-22: Added `--raw-roster-file` to `diagnose_slot_provenance.py` so a
  saved raw Fantrax `getTeamRosterInfo` payload can be inspected without local
  cookies, Chrome control, Fantrax writes, DB writes, or snapshot writes. The
  mode reports status/slot-key coverage from raw rows but deliberately returns
  `raw_only`; `--require-trusted` still exits `2` until normalized Sandlot
  roster rows carry trusted `slot_source` values. Verification: diagnostic unit
  tests passed (`10 tests`), focused backend safety tests passed (`54 tests`),
  full Python suite passed (`140 tests`), and `git diff --check` passed. Local
  Node/npm was not available in this sandbox shell for a same-run Playwright
  rerun; PR #81's GitHub `Local frontend E2E` remains the branch-only browser
  regression check, and Railway E2E remains the production smoke.
- 2026-06-22: Tightened raw roster diagnostics to apply the same
  `fantrax_data._assigned_slot_from_raw` normalization used by the scraper.
  Raw-payload mode now reports current-extractor assignment counts, assignment
  source counts, normalized slot counts, status lookup, and assigned/unassigned
  examples. This makes a saved `getTeamRosterInfo` payload useful for proving
  whether Fantrax returned real lineup/reserve slot fields, while still keeping
  `--require-trusted` fail-closed until normalized roster rows have trusted
  `slot_source`. Verification: `tests.test_slot_provenance_diagnostic` passed
  (`10 tests`), `tests.test_fantrax_data_roster_slots` passed (`6 tests`),
  full Python suite passed (`140 tests`), direct `esbuild` rebuild passed with
  no bundle diff, `git diff --check` passed, and production
  `/api/snapshot/latest` still exits `2` with all 20 active rows untrusted.
- 2026-06-22: Added `fantrax_dom.py`, a read-only saved-HTML parser for the
  Fantrax roster DOM map from PR #63: player rows can be anchored by headshot
  URLs containing `hs{player_id}_` or player/scorer data attributes, then the
  row's `lineup-btn` text is normalized into trusted `dom.lineup-btn` slot
  evidence. `diagnose_slot_provenance.py --roster-dom-file` can inspect DOM
  evidence by itself (`dom_only`, still fail-closed under `--require-trusted`)
  or overlay the DOM slots onto a matching snapshot file/URL so
  `--require-trusted` passes only when every normalized roster row has trusted
  slot provenance. Verification: `tests.test_fantrax_dom` and
  `tests.test_slot_provenance_diagnostic` passed (`17 tests`), focused
  slot/data-quality/attention tests passed (`41 tests`), full Python suite
  passed (`147 tests`), direct `esbuild` rebuild passed with no bundle diff,
  `git diff --check` passed, and production `/api/snapshot/latest` still exits
  `2` with all 20 active rows untrusted.
- 2026-06-22: Ran an internal skeptical review on the DOM proof boundary and
  accepted one finding: do not treat broad row-level labels containing
  "lineup" as slot controls, because that could read unrelated row text like a
  player's eligible position. Tightened the parser to require `lineup-btn` or a
  button-like lineup control and added a regression test. Attempted a minimal
  non-secret `claude -p --model opus --effort xhigh` second-opinion prompt, but
  it produced no output for several minutes and was interrupted; no external
  findings were available.
- 2026-06-22: Extended the DOM proof path from saved HTML to live read-only
  capture. `fantrax_dom.capture_roster_html` installs existing cookies into a
  Selenium browser, opens the Fantrax roster URL, waits for document readiness,
  and returns `page_source`; it does not click controls or mutate Fantrax.
  `diagnose_slot_provenance.py --capture-roster-dom --require-trusted` now
  combines live `getTeamRosterInfo` data with live roster-page `lineup-btn`
  slots when cookies/env are available. The flag is intentionally live-only;
  file/URL diagnostics must use saved `--roster-dom-file` evidence instead.
  Verification: `tests.test_fantrax_dom` and
  `tests.test_slot_provenance_diagnostic` passed (`21 tests`), focused
  slot/data-quality/attention tests passed (`41 tests`), full Python suite
  passed (`151 tests`), and production `/api/snapshot/latest` still exits `2`
  with all 20 active rows untrusted. This checkout still has no `.env`,
  `.cookies/fantrax.json`, saved raw roster JSON, or saved roster HTML, so real
  live Fantrax proof remains pending external credentials/data. A second
  minimal non-secret `claude -p --model opus --effort xhigh` prompt for this
  live DOM capture design produced no output within 60 seconds and was
  interrupted; no external findings were available.
- 2026-06-22: Moved DOM slot application into the data layer with
  `fantrax_data.apply_trusted_slot_overrides()` and an optional
  `slot_overrides` argument on `extract_roster()`. The helper upgrades only
  rows whose existing slot source is untrusted or missing, skips conflicting
  DOM evidence, and preserves already-trusted raw reserved-slot proof unless a
  caller explicitly opts into replacement. The diagnostic now uses this same
  helper for snapshot+DOM overlays, so the future scrape integration has one
  canonical path for `dom.lineup-btn` slot provenance. Verification:
  `tests.test_fantrax_data_roster_slots` passed (`8 tests`), focused
  DOM/diagnostic/data-quality/attention tests passed (`56 tests`), full Python
  suite passed (`153 tests`), and production `/api/snapshot/latest` still exits
  `2` with all 20 active rows untrusted. A minimal non-secret
  `claude -p --model opus --effort xhigh` prompt for the data-layer trust
  boundary produced no output within 60 seconds and was interrupted; no
  external findings were available.
- 2026-06-22: Integrated the read-only DOM slot proof path into
  `sandlot_refresh` behind `SANDLOT_CAPTURE_ROSTER_DOM_SLOTS=1`. When enabled
  with valid cookies, refresh captures the Fantrax roster page source, parses
  `lineup-btn` slot evidence, applies trusted `dom.lineup-btn` overrides
  through `fantrax_data.apply_trusted_slot_overrides()`, and records
  top-level `slot_provenance` metadata (`dom_slots_found`,
  `dom_slots_applied`, conflicts, and non-fatal capture errors). Capture
  failure does not add snapshot `errors`, does not mark recommendations ready,
  and leaves existing fail-closed gates in charge. Verification:
  focused refresh/DOM/diagnostic/data-quality/attention tests passed
  (`74 tests`), full Python suite passed (`155 tests`), direct `esbuild`
  rebuild passed, `git diff --check` passed, and production
  `/api/snapshot/latest` still exits `2` under `--require-trusted` with
  37 roster rows, 17 trusted rows, 20 untrusted rows, and all 20 active rows
  untrusted. This sandbox shell still lacks Node/npm/npx, so local Playwright
  could not be rerun here; PR #81's GitHub `Local frontend E2E` job remains the
  branch-only UI regression proof while Railway E2E remains production smoke.
- 2026-06-22: Completion audit for the shipping goal: PR #81 head
  `cab7c5f9cc6b0deea1fdea8daeb85ef3548c2684` was clean, mergeable, and green
  across GitHub `CI #130` and `Playwright #154` (Python/unit smoke, frontend
  build, Local frontend E2E, Railway production smoke). The branch satisfies
  the hot-swap card contract and safety gates, but the PR was still marked
  draft and production was still on old behavior: deployed `/api/attention`
  returned output/replacement items while the deployed snapshot diagnostic
  still exited `fail_closed` with all 20 active rows untrusted. Attempting to
  mark the draft PR ready and merge through stored GitHub credentials was
  rejected by the sandbox approval reviewer because that exact high-impact
  repo action needs explicit user approval. No merge/deploy action was taken.
- 2026-06-22: User explicitly approved marking PR #81 ready and
  squash-merging into `main`. Reconfirmed PR head
  `d2714001f96acf8b099d912427475e3cda401ee1`, GitHub `CI #132`, and
  `Playwright #156`, then marked the PR ready and squash-merged it as
  `fc366f7bfc55027112a4ab2a8590a9c1581fabbb`. Main push verification passed:
  `CI #133` (Python import/unit smoke, frontend build) and `Playwright #157`
  (Local frontend E2E, Railway production smoke). Post-deploy production
  verification passed the safety invariant: `/api/attention` returned
  `{"count": 0}` with no `output` or `replacement` items while
  `diagnose_slot_provenance.py --snapshot-url ... --require-trusted` still
  exited `2` with `fail_closed`, 37 roster rows, 17 trusted rows, 20 untrusted
  rows, and all 20 active rows untrusted. Browser-level verification from this
  sandbox was attempted through Playwright, but the bundled browser was absent
  and system Chrome aborted under sandbox control; GitHub Railway E2E plus
  direct production API checks are the deployed evidence for this slice.
- 2026-06-22: Started the next hot-swaps slice on
  `feature/hot-swap-proposal-safety`: the lineup-only replacement card now
  carries a read-only `proposal` object with deterministic proposal id,
  blocked status, writes disabled, confirmation required, and a visible safety
  checklist (trusted slots, lineup-only move, protected players excluded,
  execution safety blocked). Today renders the proposal safety ledger, and the
  Ask Skipper handoff includes the proposal id/status plus `writes enabled:
  no`. No Fantrax write, Zo write, add/drop, or trade automation path was
  enabled. Verification: focused recommendation/attention/data-quality tests
  passed (`42 tests`), full Python suite passed (`155 tests`), direct
  `esbuild` rebuild passed, and `git diff --check` passed.
- 2026-06-22: Promoted the read-only hot-swap proposal contract to the
  Attention Queue item level as `item.proposal`, while preserving the nested
  `replacement.proposal` for card rendering. The frontend queue builder mirrors
  that shape and the card now prefers the item-level proposal when present.
  This gives the future confirmation/executor slice a stable proposal handle
  without enabling any writes.

## Next Loop Phase

Run Phase 2 against `docs/quality/user-story-inventory.csv`:

1. Test each row against the real app or the closest deterministic equivalent.
2. Update each row to `passed`, `failing`, or `product-question`.
3. Record evidence and defect notes for every non-passing row.
4. Fix confirmed logistical and high-confidence UX defects.
5. Retest every fixed row and the critical path:
   Today -> Adds -> Continue in Skipper -> Skipper draft -> player sheet.
