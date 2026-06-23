# Claude Checkpoint: Production Roster Recovery

Model: `opus`
Effort: `xhigh`
Date: 2026-06-22

## Verdict

Claude agreed the production roster data incident is fixed and verified, but
warned not to call Hot Swaps "ready." The correct framing is data-integrity
recovery plus an honest paused state.

## High-Impact Feedback Accepted

- Fix the stale object-parser fallback so it uses the same live Fantrax stat
  table mapping as the raw path: cell 1 = FPts, cell 2 = FP/G.
- Check direct `fxpa/req` HTTP status before parsing JSON so HTML/error
  responses do not hide the real HTTP failure.
- Treat Hot Swaps as still blocked by missing future-game coverage and partial
  lineup-slot provenance.

## Follow-Up Risks

- Future Fantrax column reorder could silently break positional cell fallback;
  header-driven mapping or a sanity check should be added.
- Repeated failed snapshots are stored and only successful snapshots are
  pruned; failed snapshot retention should be capped.
- Add direct coverage that failed snapshots are excluded from latest-successful
  reads.

## Next Slice

Do not build Fantrax writes yet. Build future-game coverage first, then close
the trusted active-slot provenance gap, then emit real Hot Swaps proposals.
