Act as a skeptical senior engineering reviewer. Review this design before implementation.

Goal:
Harden Sandlot Skipper's web fallback so it only searches public web sources when the Fantrax snapshot cannot answer the user's question, renders captured citation trust consistently, persists web metadata with chat history, and renders a clear in-chat visual showing whether Skipper's answer is good, mixed, or risky.

Current system:
- FastAPI app in `sandlot_api.py`.
- Skipper prompt/client helpers in `sandlot_skipper.py`.
- Postgres schema and chat persistence in `sandlot_db.py`.
- React UI source in `web/sandlot/v2-pages.jsx`, bundled to `web/sandlot/app.js`.
- Skipper streams SSE from `POST /api/skipper/messages`.
- Current SSE events: `token`, `sources`, `done`, `error`.
- Current chat history table stores `role`, `content`, `tier`, and `model`.
- Current web fallback uses OpenRouter `openrouter:web_search`, extracts `url_citation` annotations, and renders source links in the newest assistant bubble.

Proposed implementation:
1. Add `chat_messages.metadata JSONB NOT NULL DEFAULT '{}'` through an idempotent migration in `init_schema()`.
2. Update chat persistence to accept optional metadata and return it from `GET /api/skipper/messages`.
3. Add a deterministic `web_search_decision(user_text, snapshot, requested)` helper:
   - denied if user toggle or server availability disables web.
   - allowed when snapshot data explicitly missing: no `free_agents`, no `player_index`, no roster, named player not found in snapshot index, or prompt asks for age/news/injury/recent public context for a player.
   - denied when the snapshot can answer normal roster/matchup/lineup questions.
   - returns `{requested, allowed, reason, missing_players}` for API telemetry and UI state.
4. Pass web fallback prompt/tool only when `decision.allowed` is true.
5. Add source policy:
   - prefer trusted baseball domains such as `mlb.com`, `espn.com`, `baseball-reference.com`, `fangraphs.com`, `cbssports.com`, and `rotowire.com`.
   - mark captured citations with `trust: trusted|supplemental`, `domain`, and `source_name`.
   - discourage assistant prose from claiming source brands that do not appear in captured citations, and render only captured citations as UI source truth.
6. Add a lightweight backend answer confidence heuristic after the response:
   - `good`: snapshot-backed or web was allowed but not actually used because the answer stayed on snapshot data.
   - `mixed`: deterministic snapshot data is limited, or web was actually used and trusted web sources were captured.
   - `risky`: web was actually used without trusted captured sources, or Skipper did not produce a reliable answer.
   - Return this as SSE `confidence` and persist it in assistant metadata.
7. UI renders a compact "Read quality" badge inside assistant bubbles with label, short reason, and supporting source counts, using text plus color.
8. Tests:
   - unit tests for gating, source classification, confidence heuristic, metadata persistence shape.
   - API tests through mocked Skipper stream where feasible.
   - Playwright test showing the visual badge and restored history metadata.

Please identify:
1. correctness or architecture risks
2. missing edge cases
3. simpler alternatives
4. validation and test gaps
5. highest-impact changes before implementation
