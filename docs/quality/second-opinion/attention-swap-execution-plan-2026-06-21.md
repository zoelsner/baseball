# Attention Swap Execution Plan - 2026-06-21

Act as a skeptical senior engineering reviewer. Review this plan before
implementation. The goal is to make Sandlot's Attention Queue produce a
concrete proposed roster move, not just a vague "inspect this player" alert.

## Goal

Build the first production-grade swap flow for Sandlot:

- When Today finds a concrete lineup replacement opportunity, the queue should
  show who Sandlot thinks Zach should swap in, who would move out, and why.
- Start with lineup-only roster optimization from the existing matchup
  replacement chain. Waiver/add-drop comparison is deferred until #67 slot
  safety is fixed and add/drop guards are active.
- The comparison should use the best available deterministic snapshot outlook:
  projected period impact, FP/G delta, win-probability impact, remaining games,
  eligibility fit, confidence, risk, and data-quality warnings.
- The UI should include a clear action path:
  - review the recommended swap
  - send/propose the action for explicit confirmation by Zach or Zo Computer
  - start a deep-research flow when snapshot data is insufficient
- Sandlot must not silently execute Fantrax writes. Any Fantrax write remains
  behind the token-gated machine action path and explicit per-action
  confirmation.

## Current Code Reality

- Today currently builds queue items in two places:
  - backend: `sandlot_attention.py`
  - frontend mirror: `web/sandlot/v2-pages.jsx`
- `GET /api/attention` already returns machine-readable attention items with
  `action` / `actions` fields.
- `sandlot_matchup.py` already ranks legal bench-to-active lineup swaps via
  `rank_matchup_improvement_actions`.
- `sandlot_waivers.py` already ranks deterministic waiver swap cards, but this
  slice must not surface add/drop recommendations while slot reliability is
  unresolved.
- The current checkout does not contain the actual `POST /api/actions` executor
  route or `sandlot_actions.py`; repo docs say it is in draft work and blocked
  by the real lineup-slot issue.
- `STATUS.md` says issue #67 is still important: some snapshot `slot` values may
  represent position, not real Fantrax lineup slot. This is a content blocker
  for swap recommendations, not only an execution blocker, because active/bench
  classification and legal swap simulation depend on `slot`.
- Bottom navigation already has Skipper in the middle in this checkout:
  Today, Roster, Skipper, Adds, League.

## Reservations

1. Do not ship a button labeled "Execute" until the executor route exists,
   Railway tokens are set, #67 slot safety is fixed, and the flow has a
   confirmed dry run.
2. For this slice, the primary CTA should be "Propose swap" or "Send to Zo",
   not "Execute swap", unless it is only opening a confirmation draft.
3. Do not surface waiver/add-drop recommendations in this slice. The standing
   repo safety rule says no add/drop recommendations until #67 lands.
4. Public web/search can supplement missing outlook or news context, but it
   cannot verify Fantrax ownership, waiver availability, league FP/G, or lineup
   slot. The UI must label those differences.
5. Trades are excluded from this build. A future trade version should require a
   deeper research session and separate human confirmation language.

## Revised Plan After Claude Review

The original plan proposed matching recommendations to every player-specific
attention item and including waiver/add-drop candidates. The Claude Opus/xhigh
review rejected that sequence because #67 can make active/bench reads wrong.

Accepted revision:

- Fix or gate slot reliability before emitting any swap recommendation.
- Build the first UI from the existing `replacement` attention item, not from a
  new per-player matching engine.
- Keep the first slice lineup-only.
- Defer waiver/add-drop recommendations and trade workflows.
- Make `/api/attention` the canonical source for Today and Zo.

## Proposed Data Contract

Enrich the existing backend `replacement` attention item with a compact
lineup-swap object. Keep top-level `action` and `actions` unchanged for
backward compatibility.

```json
{
  "id": "replacement-lineup-swap-1",
  "kind": "replacement",
  "title": "Review lineup swap",
  "reason": "Bench Bat BN -> OF; TJ Friedl OF -> BN. Projected gain +2.4 points.",
  "swap": {
    "type": "lineup_swap",
    "status": "recommended | blocked | unavailable",
    "action_state": "proposal_only | blocked",
    "headline": "Swap Bench Bat in for TJ Friedl",
    "summary": "Bench Bat projects better for the current scoring period.",
    "move_out": {
      "id": "tj-friedl",
      "name": "TJ Friedl",
      "team": "CIN",
      "positions": "OF/UT",
      "slot": "OF",
      "fpg": 1.4
    },
    "move_in": {
      "id": "bench-bat",
      "name": "Bench Bat",
      "team": "SEA",
      "positions": "OF",
      "source": "roster_bench",
      "fpg": 3.9
    },
    "outlook": {
      "window_label": "Period 13",
      "points_delta": 2.4,
      "fpg_delta": 2.5,
      "win_probability_delta": 0.02,
      "games_delta": 1,
      "confidence": "high",
      "risk": "low"
    },
    "evidence_chips": [
      "higher FP/G",
      "more remaining games",
      "legal OF swap"
    ],
    "warnings": [
      "Confirm Fantrax lineup slot before write action."
    ],
    "confirmation": {
      "channel": "zo",
      "copy": "Propose: move Bench Bat into OF and TJ Friedl to BN for +2.4 projected points. Confirm?"
    },
    "deep_research_prompt": "Research whether TJ Friedl should be replaced this week..."
  }
}
```

Notes:

- `swap` is only attached to the existing `replacement` item.
- Top-level `actions` remains the machine-action payload list; `swap` explains
  the same action chain for humans and Zo.
- `action_state` should be `blocked` if slot reliability is unknown, snapshot
  freshness is old, or action vocabulary is not safe.
- The frontend should call `/api/attention` for canonical attention items
  instead of duplicating the enriched swap logic in JavaScript.

## Ranking Strategy

1. Use the existing global matchup replacement recommendation from
   `sandlot_matchup.rank_matchup_improvement_actions`.
2. Only enrich the top `replacement` item when the action chain has one bench
   player promoted and one active player demoted.
3. If slot reliability is unknown, return the `replacement` item as blocked:
   explain that Sandlot found a theoretical lineup move but will not propose it
   until Fantrax lineup slots are verified.
4. If the snapshot is old, return `research_needed`/blocked instead of a
   proposed swap.
5. Do not match waiver cards or per-player low-output items in this slice.

## UI Plan

Update the Today Attention Queue card so the `replacement` item can show a
compact swap visual:

- left side: current player, status, slot, FP/G
- center: swap icon plus delta badge
- right side: proposed replacement, source badge (bench or free agent), FP/G
- below: one-sentence "why", risk/confidence chips, and any warning
- actions:
  - "Review swap" opens the Roster/player sheet context
  - "Propose swap" opens/sends a confirmable Zo/Skipper draft
  - "Deep research" opens Skipper with the structured prompt

Polish constraints:

- Minimum 40px hit areas.
- Tabular numbers for deltas and FP/G.
- No text-overflow failures on mobile; long names wrap or ellipsize in fixed
  tracks.
- Use existing icons and colors; do not add a new decorative visual language.
- Button copy must preserve the manual Fantrax boundary.

## Execution And Zo Flow

Phase 1 should not require live Fantrax writes.

- If action executor is unavailable or unsafe, "Propose swap" should create a
  structured confirmation payload only.
- When the action executor is present and safe, Zo can poll/read
  `/api/attention`, message Zach with the `confirmation.copy`, and submit the
  top-level `actions` to the token-gated action API only after Zach
  confirms that exact move.
- The UI should never bypass Zo confirmation and should never execute a trade.

## Implementation Slices

1. Slot reliability gate
   - Add a deterministic data-quality signal for whether roster rows have real
     Fantrax lineup slots.
   - If slot reliability is unknown or bad, block swap proposals instead of
     rendering a confident move.

2. Backend swap enrichment
   - Add helper(s) in `sandlot_attention.py` to enrich only the existing
     `replacement` item with `swap`.
   - Reuse the existing matchup ranking and action chain.
   - Do not read or match waiver cards in this slice.

3. API ownership
   - Keep `/api/attention` as the canonical queue for UI and Zo.
   - Keep old fields (`title`, `reason`, `chips`, `action`, `actions`) backward
     compatible.

4. UI rendering
   - Teach `V2Today` to load and render canonical `/api/attention` items when
     available, with the current JS queue as a degraded fallback only.
   - Add a compact `V2AttentionSwapRecommendation` subcomponent for
     `replacement.swap`.
   - Add safe CTAs for review, propose/send, and deep research.

5. Confirmation draft
   - Build the Skipper/Zo prompt from `swap.confirmation` and
     `deep_research_prompt`.
   - Do not call Fantrax-write APIs directly in this slice.

6. Tests and verification
   - Unit test broken slot shape -> blocked, not proposed.
   - Unit test old snapshot/freshness -> blocked, not proposed.
   - Unit test non-matching IDs do not false-match move chains.
   - API contract test for `swap` shape and top-level action compatibility.
   - Playwright test for a Today replacement item showing out -> in, delta, why,
     blocked copy when applicable, and the CTAs.
   - Browser verification on local app and deployed Railway if available.
   - Claude post-implementation review before final commit.

## Loop To Apply

Goal: Build the first safe Attention Queue lineup-swap visualization and
confirmation-prep flow so a real user can open Today, see exactly who Sandlot
would swap out and in when slot data is trusted, understand why from
deterministic snapshot data, and send the proposed move for explicit
confirmation without any silent Fantrax write.

Loop:

1. Inspect the real code, product rules, data shapes, and tests before editing.
2. Write the design and get a second opinion with:
   `~/.local/bin/claude -p --model opus --effort xhigh "$(cat docs/quality/second-opinion/attention-swap-execution-plan-2026-06-21.md)"`
3. Incorporate accepted reviewer findings into this plan.
4. Implement in slices: backend contract, API payload, UI rendering,
   confirmation draft, tests.
5. After each slice, run the closest real validation:
   targeted Python tests, JSX build, Playwright/browser flow, API curl or
   TestClient checks.
6. Auto-review before commit:
   architecture boundary, safety copy, data-quality edge cases, mobile layout,
   backwards compatibility, and test gaps.
7. Run `claude -p --model opus --effort xhigh` again on the final diff and
   accept or explicitly reject findings.
8. Update `STATUS.md` and the quality progress tracker.
9. Commit, push, and open/update the PR with evidence.

## Reviewer Questions

Please identify:

1. Architecture risks in attaching recommendations to attention items.
2. Whether the data contract is too broad or missing fields for Zo.
3. Edge cases around #67 slot safety, add/drop risk, stale snapshots, missing
   free-agent data, and player ID mismatches.
4. Simpler implementation paths that still produce a concrete proposed swap.
5. Test gaps and the highest-impact changes before implementation.
