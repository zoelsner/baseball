# STATUS

> Living next-steps file. Update this at the end of any session that changes the plan.
> Last updated: **2026-06-21** (after snapshot-scoped Skipper + waiver trust notes work).

## Where things stand

- **2026-06-21 app polish branch:** Skipper chat/history is scoped to the
  latest successful snapshot, waiver cards now show user-facing Trust notes for
  inferred FP/G and keeper-age uncertainty, and the bottom nav order is Today /
  Roster / Skipper / Adds / League. This is read-only app/UI work and does not
  change executor write paths.
- **`GET /api/attention` is live** ([#64](https://github.com/zoelsner/baseball/issues/64) / [PR #65](https://github.com/zoelsner/baseball/pull/65), merged + deployed). Returns the ordered queue with ready-to-submit `POST /api/actions` payloads. E2E spec validates it daily.
- **Executor [PR #63](https://github.com/zoelsner/baseball/pull/63) (draft):** reviewed, rebased onto main, CI green. First manual test run (2026-06-10): **all five deterministic guards PASS against prod, zero unintended writes** — but the Selenium layer failed safe (`player_row_not_found`) and needs a click-flow rewrite against the real Fantrax DOM. The DOM map (row anchor = headshot URL `hs{player_id}_`, two-click `lineup-btn` slot model, `remove`/`swap_horiz` icon actions) is in the PR comments.
- **Blocker — [#67](https://github.com/zoelsner/baseball/issues/67):** snapshot `slot` is the player's *position*, not their lineup slot (raw scrape never had it). Attention queue / roster health / waiver IL-protection all compute on wrong slots. Live truth as of 2026-06-10: Skubal + Woodruff already IR-stashed; **only Judge is IR in an active slot**; Condon/Montes are in dynasty `Min` slots (protected prospects).
- **Not yet done:** Railway tokens (`SANDLOT_ACTIONS_TOKEN`, `SANDLOT_REFRESH_TOKEN`) unset — the executor endpoint is fail-closed (503) until then. Zo Computer not wired.

## Next steps, in order ([#66](https://github.com/zoelsner/baseball/issues/66) tracks activation)

1. **Fix #67** — find the real lineup-slot source (check fantraxapi for ignored roster sections/status; else read `lineup-btn` text from the roster DOM during the existing scrape session). Treat `Min` as reserved alongside IL/IR everywhere. Cloud-friendly work.
2. **Rework #63's Selenium flows** against the DOM map on the PR. Add the hard guard: refuse `drop_player` for `Min`/IL-slot players. Cloud-friendly to write; not to test.
3. **Re-run write scenarios (3/5/6/7b) locally, headful, with Zach watching.** Judge is the real IL-move target. Local-only — needs Mac + Fantrax creds.
4. **Set Railway tokens**, verify 503→401 behavior by curl.
5. **Merge #63, wire Zo** — phase 1 vocabulary only (`move_to_il`, `change_slot`). One real end-to-end loop (queue → Telegram → yes → executed → `action_logs` row) closes #66.

## Safety rules (non-negotiable — full text on [#66](https://github.com/zoelsner/baseball/issues/66#issuecomment-4695871271))

No write without Zach watching + approving the named player · phased Zo vocabulary (slot moves → adds → maybe drops) · `Min`/IL prospects undroppable · no add/drop recommendations until #67 lands · fail closed everywhere.

## Cloud session kickoff (paste this on your phone)

> Read CLAUDE.md and STATUS.md, then issues #67 and #66 and the two Claude comments on PR #63 (review summary + manual test results with the DOM map). Work on #67: find the real lineup-slot source — check the installed fantraxapi package for roster sections or status fields the scrape ignores; if the API truly lacks it, implement the lineup-btn DOM read described on PR #63. Fix `extract_roster`, treat `Min` as a reserved slot alongside IL/IR (v2StarterRows in v2-pages.jsx, RESERVED_SLOTS in sandlot_attention.py, waiver IL-stash protection), and add unit tests using the live roster shape from the PR comment. Branch `fix/67-roster-slots`, PR when CI is green. Don't touch the executor write paths, and never attempt live Fantrax writes — those run locally with Zach watching.
