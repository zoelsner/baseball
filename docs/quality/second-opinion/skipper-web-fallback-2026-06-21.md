# Second-Opinion Gate: Skipper Web Fallback

Run from the repo root after Claude Code is authenticated:

```bash
claude -p "$(cat docs/quality/second-opinion/skipper-web-fallback-2026-06-21.md)"
```

---

Act as a skeptical senior engineering reviewer. Review this design before
implementation.

Goal:
Move Skipper to the center of the bottom nav and add a web-search fallback for
missing Sandlot/Fantrax snapshot data. The user wants Skipper to be able to
search public web sources when a player swap question cannot be answered from
the local snapshot, while keeping Fantrax/Sandlot data authoritative for league
facts.

Current design:

- Frontend reorders bottom nav to Today, Roster, Skipper, Adds, League.
- Frontend adds a compact `Web fallback` toggle in Skipper, default on.
- `POST /api/skipper/messages` includes `web_search: true/false`.
- FastAPI adds `web_search: bool = True` to `SkipperMessageIn`.
- `/api/skipper/options` advertises web-search availability, default, and tool
  id.
- Backend preserves deterministic snapshot-grounded replies: if
  `deterministic_reply()` can answer from snapshot, no web search is used.
- For LLM replies, when web search is allowed, `sandlot_skipper.build_messages`
  adds a system prompt that says to search only when snapshot data is missing.
- `SkipperClient.stream()` passes OpenRouter's `openrouter:web_search` server
  tool with capped results.
- Fantrax/Sandlot snapshot remains authoritative for league-specific facts:
  roster slot, roster ownership, free-agent availability, Fantrax/Sandlot FP/G,
  matchup score, waiver-card confidence, and projected deltas.
- Web results may supplement public MLB facts: player stats, age, team, role,
  injury/news context, and recent performance.
- Skipper must separate Snapshot facts from Web facts and include source names
  or links for web-backed claims.

Relevant constraints:

- Do not send secrets, cookies, API keys, or private production data to the
  reviewer.
- OpenRouter web search is request-local and may add cost, so result caps are
  required.
- The app is mobile-first; Skipper controls must fit the existing compact chat
  header.
- Current tests should avoid spending API credits; request-body and client
  wiring can be tested with mocks.
- The screenshot motivating this shows a swap prompt where the snapshot has
  Bryan Hudson but lacks Martin Perez/free-agent data, so Skipper should be able
  to search public sources for Perez while clearly saying what is not verified
  by Fantrax.

Validation plan:

- Unit tests for `web_search_default_enabled`, `web_search_allowed`, prompt
  injection, and OpenRouter tool request wiring.
- Playwright test for bottom nav order.
- Playwright test that Skipper sends `web_search: true` by default and
  `web_search: false` after toggling off.
- Existing Skipper projection and deterministic-reply tests should keep passing.
- Browser smoke against the local app after rebuilding `web/sandlot/app.js`.

Please identify:

1. correctness or architecture risks
2. missing edge cases
3. simpler alternatives
4. validation and test gaps
5. security, privacy, cost, or operational risks
6. the highest-impact changes to make before implementation

Be blunt. Focus on ways this could mislead the user, spend too much money, make
answers harder to trust, become hard to test, or break the existing Skipper
snapshot-first behavior.
