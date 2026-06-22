Act as a skeptical senior engineering reviewer. Review this Sandlot feature slice before it is finalized.

Goal:
Build a lineup-only hot-swap recommendation card for the Today Attention Queue. It must clearly name the OUT and IN players, explain why, show short-term outlook, risk, confidence, data source/provenance, and keep execution blocked until a separate Fantrax write-safety path is ready.

Non-negotiable safety rules:
- No add/drop execution.
- No Fantrax writes or live mutations.
- Replacement items must not expose ready-to-submit `change_slot` payloads.
- Advice must stay fail-closed when `lineup_recommendations_ready` is not explicitly true.
- Never suggest dropping protected/minors/IL players.
- The card may route to Skipper / deep research for uncertainty, but not execute.

Current design:
- `sandlot_matchup.rank_matchup_improvement_actions()` still computes only legal bench-to-active lineup swaps.
- Each accepted lineup recommendation now includes `replacement_card`:
  - `move_in` and `move_out` player summaries
  - `projected_benefit`
  - `reason`, `short_term_outlook`, `risk`, `confidence`
  - `provenance`
  - `safety`
  - `execution: { state: "blocked", label: "Propose swap", reason: ... }`
- `sandlot_attention.build_queue()` renders replacement items as non-executable proposals:
  - `action: None`
  - `actions: []`
  - `replacement: replacement_card`
  - `blocked_action: replacement_card.execution`
- Today UI renders a richer card with OUT/IN players, benefit, confidence/risk/source chips, Why/Outlook/Risk/Source lines, disabled "Propose swap blocked", "Ask Skipper", and "Deep research".
- Existing fail-closed provenance gate remains in front of matchup recommendations and Today UI.

Validation already run:
- Focused backend safety tests: `52 tests`, passing.
- Full Python suite: `138 tests`, passing.
- Rebuilt `web/sandlot/app.js` with esbuild.
- Local Playwright `today-attention.spec.ts`: `4 tests`, passing against `http://127.0.0.1:4173` with `SANDLOT_EXPECT_SLOT_GATE=1`.

Please identify:
1. correctness or architecture risks
2. missing edge cases
3. safety leaks around execution or protected players
4. UI/data contract problems
5. validation gaps worth closing before commit
