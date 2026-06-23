Act as a skeptical senior engineering reviewer. Review this design before implementation.

Goal:
Add a read-only movability/lock gate to Sandlot Hot Swaps before any lineup-only swap can be treated as executable. The app must keep Fantrax writes disabled, but it should tell the user whether a proposed OUT/IN move appears movable, locked, or unknown based on current Fantrax snapshot evidence.

Current production state:
- Production Hot Swaps is now ready and emits one read-only proposal:
  - OUT: TJ Friedl, active OF -> RES, slot_source raw.posId
  - IN: Ildemaro Vargas, RES -> UT, slot_source raw.statusId
  - projected benefit: +9.1 points
  - confidence: high
  - execution remains blocked
- Data gates are now healthy:
  - roster rows: 37
  - lineup-slot provenance: 37/37 ok
  - future-game coverage: 40/40 ok
- The raw Fantrax rows for both participants include:
  - eligibleStatusIds supporting active/reserve statuses
  - eligiblePosIds supporting target slot eligibility
  - scorer.disableLineupChange: true
- I infer `disableLineupChange: true` is likely a lock/movability signal, but it has not yet been empirically proven against the live Fantrax UI.

Current code shape:
- `sandlot_matchup.py` builds read-only lineup swap recommendations.
- `_participant_blocker()` currently blocks protected players, untrusted slot provenance, and untrusted future-game provenance.
- `_lineup_replacement_card()` emits `replacement` with move_in/move_out, projected_benefit, provenance, safety, and execution.
- `_lineup_swap_proposal()` always returns blocked `executor_ready`.
- `sandlot_api.py` exposes `/api/hot-swaps/latest` by wrapping Attention Queue replacement items.
- `web/sandlot/v2-pages.jsx` renders the Hot Swap card with OUT/IN, points, confidence/risk/source, safety checks, disabled Propose swap, Ask Skipper, and Deep research.

Proposed design:
1. Add a normalized `movability` object to each replacement card:
   - `state`: `movable`, `locked`, or `unknown`
   - `label`: user-facing short label
   - `reason`: concise reason
   - `source`: e.g. `fantrax.raw.scorer.disableLineupChange`
   - `participants`: per-player state for move_in and move_out
2. For now:
   - If any participant has `raw.scorer.disableLineupChange === true`, mark the proposal `locked`.
   - If all participants explicitly have `disableLineupChange === false`, mark `movable`.
   - If the field is missing or not boolean for any participant, mark `unknown`.
3. Add a `movability` safety check to `proposal.safety_checks`:
   - `passed` when movable
   - `blocked` when locked
   - `warning` when unknown
4. Keep `proposal.status = blocked` and `writes_enabled = false` in every state because the executor contract is still out of scope.
5. Update UI copy:
   - show `Locked`, `Movable`, or `Movability unknown`
   - if locked: say Fantrax currently marks one or more participants as unavailable for lineup changes
   - keep `Propose swap blocked`
6. Add tests:
   - locked participant yields movability locked and blocked safety check
   - both false yields movable and passed safety check while executor remains blocked
   - missing field yields unknown/warning and executor remains blocked
   - UI/API payload carries the movability object

Non-goals:
- No Fantrax writes.
- No Zo writes.
- No add/drop/trade execution.
- No live mutation.
- No attempt to bypass Fantrax locks.

Questions:
1. Is treating `disableLineupChange: true` as a hard lock the right fail-closed interpretation?
2. Should unknown be warning-only or hard-blocked for execution-readiness even though writes are still disabled?
3. Where should the movability gate live architecturally: blocker before recommendation emission, replacement card metadata, or both?
4. What edge cases/tests are missing?
5. What is the highest-impact change before we eventually enable a confirmed lineup-only executor?
