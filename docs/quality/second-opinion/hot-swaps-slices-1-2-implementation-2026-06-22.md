# Hot Swaps Slices 1-2 Implementation Review Prompt

Act as a skeptical senior engineering reviewer. Review this focused plan before implementation.

Goal:
Unpause Sandlot Hot Swaps safely by fixing only the two remaining data-readiness blockers:

1. Future-game coverage for the current Fantrax scoring period.
2. Trusted lineup-slot provenance for active swap-participating roster rows.

Current production evidence:
- Latest Railway snapshot has non-empty roster data.
- FP/G coverage is ok.
- Eligibility coverage is ok.
- Future-game coverage is 0/40.
- Slot provenance is partial.
- Hot Swaps and Today advice are paused.

Important constraints:
- No Fantrax writes.
- No Zo writes.
- No add/drop execution.
- No trade execution.
- No executor activation.
- Read-only recommendations only.

Planned Slice 1:
- Add MLB Stats schedule support for team schedules by MLB team id and date window.
- Resolve Fantrax team abbreviations to MLB team ids with explicit aliases.
- During refresh, enrich both my roster and opponent rosters with provenance-backed future-game data.
- For hitters, count remaining team games in the scoring period.
- For pitchers, do not multiply FP/G by all team games. Count only pitcher-specific probable starts or keep pitcher opportunity conservative and scoped out of recommendation math.
- Add lower-bound date handling so already-played games are not counted.
- Make future-game quality provenance-aware, so empty arrays or failed team mappings cannot pass the readiness gate.

Planned Slice 2:
- Use the existing read-only Fantrax DOM slot proof path, or an equivalent trusted source, to prove active lineup slots.
- Replace untrusted `position_fallback` active slots only when trusted DOM evidence matches the player.
- Add diagnostics for DOM proof failures without marking the roster scrape failed.
- Keep Hot Swaps fail-closed unless the rows needed for swap recommendations have trusted slot provenance.

Questions:
1. What are the correctness risks in this plan?
2. What is the safest architecture for future-game enrichment so hitter and pitcher projections are not mixed up?
3. Should future-game readiness be all-player, active-player, hitter-only, or proposal-participant scoped?
4. What exact diagnostics should be recorded so production pause reasons are narrow and actionable?
5. What tests must exist before this is safe to ship?
6. What should be explicitly deferred until after read-only proposals are trustworthy?

Please be concrete. Assume this is a production fantasy-baseball assistant where bad recommendations can cause roster damage even before writes are enabled.
