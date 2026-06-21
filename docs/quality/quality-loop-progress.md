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
- 2026-06-21: Added the next Skipper hardening loop for roster optimization:
  trusted/supplemental citation metadata, persisted chat metadata, structural
  read-quality badges, and live/history UI rendering. Reran the second-opinion
  gate explicitly pinned to Claude Opus with `--effort xhigh`; accepted findings
  fixed confidence logic so it depends on actual web usage rather than tool
  permission, made deterministic reads respect degraded projection data, and
  narrowed web-search intent keywords. Updated tracker row `SKP-010`.
- Future product direction captured from Zach: one-click roster-change/swap
  preparation can build on the same trust metadata, but trade execution should
  remain a separate, deeper research workflow with much stricter specificity.

## Next Loop Phase

Run Phase 2 against `docs/quality/user-story-inventory.csv`:

1. Test each row against the real app or the closest deterministic equivalent.
2. Update each row to `passed`, `failing`, or `product-question`.
3. Record evidence and defect notes for every non-passing row.
4. Fix confirmed logistical and high-confidence UX defects.
5. Retest every fixed row and the critical path:
   Today -> Adds -> Continue in Skipper -> Skipper draft -> player sheet.
