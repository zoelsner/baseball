# STATUS

> Living next-steps file. Update this at the end of any session that changes the plan.
> Last updated: **2026-06-22** (production roster scrape fix in progress).

## Where things stand

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
  refresh / recommendation tests passed (`65 tests`), full Python suite passed
  (`160 tests`), import smoke passed, `git diff --check` passed, and direct
  `esbuild` rebuild passed. Deployment and Railway production verification are
  still pending.
- **Zo hot-swap safety issue:** [#82](https://github.com/zoelsner/baseball/issues/82)
  tracks the future Zo confirmation/protected-player action architecture.

## Next steps, in order ([#66](https://github.com/zoelsner/baseball/issues/66) tracks activation)

1. **Finish #67 real-slot proof** — with valid local Fantrax cookies, a saved
   raw `getTeamRosterInfo` payload, or a saved roster-page HTML file plus
   matching snapshot, refresh/read-only inspect `slot_source` coverage from raw
   `statusId`/slot fields and/or `dom.lineup-btn` slots. If active lineup slots
   still resolve as `position_fallback`, run the live diagnostic with
   `--capture-roster-dom`; if that proves trusted active slots, integrate the
   read-only `lineup-btn` DOM slot parser into the scrape. Keep recommendation
   gates fail-closed until this is proven.
2. **Wire the hot-swap proposal confirmation path** — keep `Propose swap`
   disabled until #63's executor safety can accept a lineup-only proposal with
   named OUT/IN players, slot provenance proof, and Zach confirmation.
3. **Rework #63's Selenium flows** against the DOM map on the PR. Add the hard guard: refuse `drop_player` for `Min`/IL-slot players. Cloud-friendly to write; not to test.
4. **Re-run write scenarios (3/5/6/7b) locally, headful, with Zach watching.** Judge is the real IL-move target. Local-only — needs Mac + Fantrax creds.
5. **Set Railway tokens**, verify 503→401 behavior by curl.
6. **Merge #63, wire Zo** — phase 1 vocabulary only (`move_to_il`, `change_slot`). One real end-to-end loop (queue → Telegram → yes → executed → `action_logs` row) closes #66.

## Safety rules (non-negotiable — full text on [#66](https://github.com/zoelsner/baseball/issues/66#issuecomment-4695871271))

No write without Zach watching + approving the named player · phased Zo vocabulary (slot moves → adds → maybe drops) · `Min`/IL prospects undroppable · no add/drop recommendations until #67 lands · fail closed everywhere.

## Cloud session kickoff (paste this on your phone)

> Read CLAUDE.md and STATUS.md, then issues #67 and #66 and the two Claude comments on PR #63 (review summary + manual test results with the DOM map). Work on #67: find the real lineup-slot source — check the installed fantraxapi package for roster sections or status fields the scrape ignores; if the API truly lacks it, implement the lineup-btn DOM read described on PR #63. Fix `extract_roster`, treat `Min` as a reserved slot alongside IL/IR (v2StarterRows in v2-pages.jsx, RESERVED_SLOTS in sandlot_attention.py, waiver IL-stash protection), and add unit tests using the live roster shape from the PR comment. Branch `fix/67-roster-slots`, PR when CI is green. Don't touch the executor write paths, and never attempt live Fantrax writes — those run locally with Zach watching.
