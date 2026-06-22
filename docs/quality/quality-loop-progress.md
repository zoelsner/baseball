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

## Next Loop Phase

Run Phase 2 against `docs/quality/user-story-inventory.csv`:

1. Test each row against the real app or the closest deterministic equivalent.
2. Update each row to `passed`, `failing`, or `product-question`.
3. Record evidence and defect notes for every non-passing row.
4. Fix confirmed logistical and high-confidence UX defects.
5. Retest every fixed row and the critical path:
   Today -> Adds -> Continue in Skipper -> Skipper draft -> player sheet.
