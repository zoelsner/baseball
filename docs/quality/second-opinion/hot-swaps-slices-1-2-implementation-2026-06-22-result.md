# Hot Swaps Slices 1-2 Claude Review Result

Date: 2026-06-22

Command:

```bash
/Users/zachoelsner/.local/bin/claude -p --model opus --effort xhigh --tools "" --no-session-persistence --max-budget-usd 2 --system-prompt "You are a skeptical senior engineering reviewer. You cannot use tools. Do not ask to read files. Do not emit tool calls. Answer only from the user prompt, with concrete implementation advice." "$(cat docs/quality/second-opinion/hot-swaps-slices-1-2-implementation-2026-06-22.md)"
```

## Accepted Guidance

- Treat `0/40` future-game coverage as a diagnostic failure, not proof that every row should block Hot Swaps.
- Separate schedule coverage from projection math. Schedule enrichment can prove team schedule provenance, but projection must decide how to count it per player type.
- Do not let pitchers consume hitter/team-game counts. Pitchers should count only explicit pitcher-specific probable starts or be scoped out of recommendation math for now.
- Gate Hot Swaps at the proposal-participant level, not all roster rows. A swap proposal should emit only when every touched row has the relevant trusted data.
- Start hitter-only readiness first. Pitcher probable-start modeling should be deferred unless explicit provenance exists.
- Use MLB schedule by numeric team id with explicit Fantrax abbreviation mapping and unresolved-team diagnostics.
- Distinguish mapped-empty schedule from mapping/fetch failure. Empty can be a real off day; mapping/fetch failure must pause affected proposals.
- Add frozen-now/window-bound tests so already-played games are excluded and late-today future games can still count.
- Use Fantrax player id for DOM slot proof. Name-only matching must not upgrade slot provenance.
- Keep DOM capture failures diagnostic-only; do not mark roster scrape failed solely because DOM proof failed.

## Plan Changes From Review

- Slice 1 will implement schedule enrichment as provenance plus player-type counters, not a flat `future_games` field that all rows consume the same way.
- Future-game readiness will become proposal-participant scoped, with global coverage retained as a diagnostic section.
- Read-only Hot Swaps can become hitter-ready before pitcher-ready. Pitcher hot swaps remain blocked unless explicit probable-start evidence exists.
- Pause reasons must name actionable blockers, especially unmapped team abbreviations and slot-proof gaps.

## Deferred

- Fantrax writes.
- Zo writes.
- Add/drop execution.
- Trade execution.
- Pitcher probable-start/streaming model beyond explicit probable-start schedule evidence.
- AI-driven deterministic ranking.
