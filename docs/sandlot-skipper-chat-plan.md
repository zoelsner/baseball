# Sandlot — Skipper Chat (V2 Feature) Plan

## Context

Sandlot V1 just shipped (real-data viewer on Railway). The next feature is **Skipper chat**: a conversational LLM assistant for roster Q&A grounded in the latest Fantrax snapshot.

User decisions locked via brainstorming:

- **Use case:** Roster Q&A only (no opinionated strategy, no trade grading — those are separate features)
- **Models:** Kimi (Moonshot) primary, DeepSeek V4 Flash fallback — both via OpenRouter (OpenAI-compatible API)
- **Context routing:** Default to tier 2 (my roster + standings); escalate to tier 3 (+ all 12 rosters) on keyword match
- **UI placement:** Existing 5th bottom-nav tab (`Skipper` — already scaffolded as mock at `web/sandlot/v2-pages.jsx`)
- **Memory:** Persistent in Postgres (no localStorage)
- **Streaming:** Yes, SSE
- **Identity:** Single-user V1 — implicit session, `FANTRAX_TEAM_ID` is the user

Builds on V1's working foundation: FastAPI + Postgres + cookie-backed Fantrax scrape.

## Recommended Approach

```
[Skipper tab in v2-pages.jsx]
    │  user types message
    │  fetch POST /api/skipper/messages (SSE response)
    ▼
[FastAPI: stream_chat handler]
    1. Append user msg → chat_messages (Postgres)
    2. Load latest snapshot from sandlot_db
    3. detect_tier(prompt, snapshot) → 2 or 3
    4. build_context(tier, snapshot) → text block
    5. build_messages(system + context + recent history + user msg)
    6. stream from OpenRouter (Kimi → fallback DeepSeek on error)
    7. yield SSE chunks: {"type":"token","text":"..."}
    8. on stream end: append assistant msg → chat_messages
       yield {"type":"done","tier":2,"model":"kimi"}
    ▼
[Frontend: append tokens to current bubble; persist on done]
```

### Decisions

- **Pre-store all-rosters in every snapshot.** Modify `fantrax_data.collect_all()` to call existing `extract_all_team_rosters` and include result as `snapshot["all_team_rosters"]`. Tradeoff: refresh time +3-8s, snapshot size +~30KB. Accepted because the alternative (lazy-load mid-chat) couples the chat handler to the scraper and complicates error paths. Postgres handles 100KB JSONB rows trivially.
- **Single implicit session in V1.** Schema supports multi-session, but UI shows one continuous thread. Multi-session UI is a V2 add with no schema change.
- **Skip multi-turn context window pruning for V1.** Send full chat history each turn until we measure cost/latency. We'll add windowing only if a user crosses ~50 messages.

## Files to Create

| Path | Purpose |
|---|---|
| `sandlot_skipper.py` | OpenRouter client wrapper (DeepSeek V4 Flash → Kimi fallback), tier detection, context formatter, system prompt, message builder, streaming generator |

(no new frontend files — wire existing `V2Skipper` page in `v2-pages.jsx`)

## Files to Modify

| Path | Change |
|---|---|
| `requirements.txt` | Add `openai>=1.0` (for OpenRouter's OpenAI-compatible API) |
| `sandlot_db.py` | Add `chat_sessions` and `chat_messages` tables to `init_schema()`; add helpers: `get_or_create_default_session()`, `list_chat_messages(session_id)`, `append_chat_message(session_id, role, content, tier=None, model=None)` |
| `sandlot_api.py` | Add 3 endpoints: `GET /api/skipper/messages` (history), `POST /api/skipper/messages` (SSE stream), `DELETE /api/skipper/messages` (clear history). Use FastAPI `StreamingResponse` with `media_type="text/event-stream"`. |
| `fantrax_data.py` | Modify `collect_all()` to call `extract_all_team_rosters(api, my_team_id)` and add result as `snapshot["all_team_rosters"]`. Wrap in try/except so a partial failure doesn't fail the whole refresh. |
| `web/sandlot/v2-pages.jsx` | Wire the existing `V2Skipper` component (around line 1098) to real backend. Replace mock state with: (a) on mount, fetch `GET /api/skipper/messages` to load history, (b) on send, fetch `POST /api/skipper/messages` and consume SSE stream, appending tokens to current bubble, (c) on done, mark message complete. No localStorage. |
| `.env.example` | Add `OPENROUTER_API_KEY=` |

## Existing Functions to Reuse

| Source | What | Why |
|---|---|---|
| `claude_analyzer.py:64-83` `_trim_for_prompt()` | strips raw blobs, caps FA pool | adapt to strip `raw` from roster rows when building Skipper context |
| `decision_engine.py:423-439` `_slim_my_roster()` | reduces row to {name, slot, positions, team, fppg, fpts, age, injury} | exact format we want for context |
| `fantrax_data.py:291-323` `extract_all_team_rosters()` | already implemented but never called from `collect_all()` | wire it up |
| `sandlot_db.py:157-167` `latest_successful_snapshot()` | snapshot loader | reuse as-is |
| `decision_engine.py:384-397` Anthropic streaming pattern | error handling shape | adapt to OpenAI SDK shape (`chunk.choices[0].delta.content`) |

## Build Order

1. **DB schema** — add `chat_sessions`, `chat_messages` to `sandlot_db.init_schema()`. Add helper functions. Verify by running `init_schema()` against Railway DB.
2. **Scraper change** — modify `collect_all()` to include `all_team_rosters`. Run a manual refresh; confirm snapshot has 12 team rosters. Verify size + duration acceptable (~12s, ~40KB).
3. **Skipper module** — `sandlot_skipper.py` with: `SkipperClient` class (OpenRouter client with DeepSeek V4 Flash→Kimi fallback), `detect_tier()`, `build_context()`, `SYSTEM_PROMPT` constant, `build_messages()`, `stream_response()` generator.
4. **API endpoints** — wire `GET/POST/DELETE /api/skipper/messages` in `sandlot_api.py`. Use `StreamingResponse` for SSE. Append messages to DB at start (user) and end (assistant). Include tier + model in DB row for debugging.
5. **Frontend wiring** — replace the mock state in `V2Skipper` with real fetch calls + SSE consumer.
6. **Local end-to-end test** — `uvicorn` against Railway DB. Open Skipper tab. Test tier 2 ("who's my best SP?"), tier 3 ("how does my pitching compare to the league?"), persistence (reload → history shows). Test fallback (set `OPENROUTER_API_KEY=invalid` and verify DeepSeek path takes over).
7. **Deploy** — push to main; Railway auto-deploys. Confirm `OPENROUTER_API_KEY` exists on Railway web service. Smoke test on Railway URL.

## Verification

**API**
```bash
curl http://127.0.0.1:8000/api/skipper/messages

curl -N -X POST http://127.0.0.1:8000/api/skipper/messages \
     -H "Content-Type: application/json" \
     -d '{"content": "Who is my best 2B?"}'
# Expect: stream of "data: {...}\n\n" chunks ending with done event
```

**Browser**
- Open Skipper tab → empty thread
- Type "Who's my best 2B?" → see streaming response, ~3s
- Type "Compare my pitching to the league" → tier 3, longer load (~8s) referencing other teams
- Reload → both prior turns visible

**Fallback**
- Set `OPENROUTER_API_KEY` to a bogus value, restart, send a message
- Expect: log "Kimi failed, falling back to DeepSeek" and DeepSeek still responds

**Data integrity**
```sql
SELECT count(*), role FROM chat_messages GROUP BY role;
SELECT tier, model, count(*) FROM chat_messages WHERE role='assistant' GROUP BY 1,2;
```

## Out of Scope For This Iteration

- Multi-session UI (schema supports it; UI shows one thread)
- Tool/function calling (deterministic keyword routing only)
- Trade grader / strategy advisor (separate spec)
- pybaseball MLB stats in context (V2)
- Cost telemetry / usage dashboard (defer)
- Streaming-during-error retry (V1 fallback is per-request, not per-token)

## Open Questions for Implementation

- **Exact OpenRouter model IDs.** Verify at impl time:
  - DeepSeek primary: `deepseek/deepseek-v4-flash`
  - Kimi fallback: `moonshotai/kimi-k2`
  - Use OpenRouter's `/v1/models` endpoint to confirm.
- **System prompt voice.** Default draft: neutral, grounded, refuses to speculate beyond snapshot data. V1 = "neutral helpful assistant," not "strategist".
