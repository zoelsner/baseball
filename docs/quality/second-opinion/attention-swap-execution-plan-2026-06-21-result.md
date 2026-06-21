# Claude Review Result - Attention Swap Execution Plan - 2026-06-21

Command:

```bash
~/.local/bin/claude -p --model opus --effort xhigh "$(cat docs/quality/second-opinion/attention-swap-execution-plan-2026-06-21.md)"
```

## Verdict

The plan's data contract and UI direction are reasonable, but the sequencing is
wrong. Issue #67 corrupts recommendation content, not just write safety, because
the matchup and attention logic classify active vs bench players from `slot`.
`STATUS.md` says that `slot` may currently be position, not real lineup slot.

The reviewer also found that waiver add/drop recommendations violate the
standing safety rule in `STATUS.md`: no add/drop recommendations until #67
lands, because keeper/minor/IL protection depends on reliable slots.

## Accepted Findings

- Treat #67 as a content blocker, not a warning chip.
- Add a slot-reliability/data-quality gate before emitting actionable lineup
  recommendations.
- Cut waiver/add-drop recommendations from this slice entirely.
- Build the first swap card only from the existing matchup `replacement` item
  and its existing action chain.
- Do not create a broad new recommendation engine attached to every attention
  item yet.
- Use `/api/attention` as the canonical attention source for UI and Zo instead
  of embedding a second representation in `/api/snapshot/latest`.
- Keep top-level `action` / `actions` backward compatible.
- Add tests for broken slot shape, stale freshness, non-matching player IDs,
  and existing action-contract compatibility.

## Rejected Or Deferred Findings

- None rejected. The review lines up with the repo safety rules and current
  code reality.

## Updated Direction

Reorder the loop:

1. Fix or gate #67 first: establish real lineup slot reliability, or block swap
   recommendations when slots are untrusted.
2. Build a lineup-only swap card from the existing replacement item returned by
   `sandlot_attention.py`.
3. Render the out -> in visual and safe CTAs: review, propose/send to Zo, deep
   research.
4. Defer waiver/add-drop recommendations until slot safety and add/drop guards
   are complete.
5. Run a post-implementation Claude Opus/xhigh review on the final diff.

## Reviewer Detail Summary

- `sandlot_matchup._is_active_lineup_row` and `_is_bench_row` classify roster
  rows by `slot`; if `slot` is actually a position, swap ranking is wrong.
- `snapshot_data_quality` does not currently detect slot-source reliability.
- Waiver protection depends on slot-based protected move-out logic, so waiver
  proposals are unsafe while slot semantics are uncertain.
- A new `recommendation` object on every item risks duplicating
  `matchup.recommendations`, `/api/attention` action chains, and the current JS
  queue mirror.
- The smaller first slice is enough to satisfy the user-facing need for the
  first safe subset: concrete lineup swap visualization and confirmation prep.
