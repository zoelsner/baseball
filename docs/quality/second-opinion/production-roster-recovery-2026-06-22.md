Act as a skeptical senior engineering reviewer. Review this production recovery checkpoint before we move back into Hot Swaps feature work.

Goal:
Restore production roster data and Hot Swaps readiness after the Hot Swaps UI shipped but Railway snapshots had `roster: []` and `errors: ["roster: 'Roster' object has no attribute 'positions'"]`.

Implemented changes:
- `fantrax_data._team_roster()` now tries raw `getTeamRosterInfo` first and normalizes raw rows directly instead of depending on upstream `Roster` / `RosterRow` / `Player` construction.
- `_raw_request()` tries any installed helper, then `FantraxAPI._request`, then direct authenticated `fxpa/req` via the API object's session and league id.
- `extract_all_team_rosters()` uses the same raw-first roster path.
- `sandlot_refresh.py` marks refreshes `failed` when my-roster rows are missing or roster section errors occur, using the existing failed status and errors.
- No Fantrax writes, Zo writes, add/drop/trade execution, or executor path was enabled.
- Raw production table mapping was corrected after live verification: cell 1 is season FPts and cell 2 is FP/G.

Verification:
- Local focused scraper / refresh / recommendation tests: 67 passed.
- Full Python suite: 162 passed.
- Import smoke passed.
- Railway deployed `ffb2b32` successfully for both web and cron.
- Manual production refresh after deploy succeeded: refresh run 298, snapshot 217, 37 roster rows, errors `[]`.
- Production sample values are sane:
  - Salvador Perez: 142.0 FPts, 1.95 FP/G
  - Christian Walker: 231.0 FPts, 2.92 FP/G
  - TJ Friedl: 64.0 FPts, 1.36 FP/G
  - Bryan Hudson: RES, raw.statusId, 107.5 FPts, 2.99 FP/G
- `/api/snapshot/latest`: snapshot 217, roster_count 37, my_roster state ok.
- `/api/attention`: snapshot 217, 0 items.
- `/api/hot-swaps/latest`: snapshot 217, state paused, 0 proposals.
- Production UI in browser: no `first snapshot was empty`, no `Waiting for roster data`, no console errors. Hot Swaps is visible and paused.

Known remaining data-readiness limits:
- `lineup_slots` is still partial: 17/37 trusted rows, active rows still mostly `position_fallback`.
- Hot Swaps is paused for `Future-game coverage 0/40, plus 1 more issue`.
- The app is now truthfully paused instead of broken/empty, but it is not yet producing actionable Hot Swaps recommendations.

Please identify:
1. correctness or architecture risks in this recovery
2. missing tests or production checks before considering the recovery complete
3. whether the direct `fxpa/req` fallback is acceptable or should be refactored
4. whether the remaining paused Hot Swaps state is an acceptable stopping point for this goal
5. the highest-impact next step before building Fantrax write/execution flows
