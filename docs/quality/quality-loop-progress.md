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

## Next Loop Phase

Run Phase 2 against `docs/quality/user-story-inventory.csv`:

1. Test each row against the real app or the closest deterministic equivalent.
2. Update each row to `passed`, `failing`, or `product-question`.
3. Record evidence and defect notes for every non-passing row.
4. Fix confirmed logistical and high-confidence UX defects.
5. Retest every fixed row and the critical path:
   Today -> Adds -> Continue in Skipper -> Skipper draft -> player sheet.
