# Second-Opinion Result: Skipper Web Fallback

Command:

```bash
~/.local/bin/claude -p "$(cat docs/quality/second-opinion/skipper-web-fallback-2026-06-21.md)"
```

Status: ran successfully on 2026-06-21.

## Findings Accepted

- Capture OpenRouter `url_citation` annotations and render them as first-class
  web sources instead of trusting model prose citations.
- Treat `web_search` in the final stream event as actual usage, not merely
  permission. Keep a separate `web_search_requested` field.
- Split web-search `available` from `default_enabled` so deployments can offer
  the feature without defaulting it on.
- Make the Skipper UI respect server options: hide the Web fallback control
  when the server marks web search unavailable.
- Attach the paid OpenRouter web-search tool only to the first model attempt,
  avoiding duplicate web-search spend on fallback retries.
- Add tests for server options, source extraction, request-body wiring, and
  fallback retry behavior.

## Findings Rejected Or Deferred

- Deterministic named-entity gating before attaching the web tool is deferred.
  It is attractive for cost control, but reliable player extraction from free
  text deserves a separate implementation and test pass. Current mitigation is
  explicit user toggle, default/deploy controls, result caps, and first-attempt
  only web tool attachment.
- Persisting web-source metadata into chat history is deferred. The immediate
  goal is live answer trust. Persisted provenance needs a chat-message schema
  decision.

## External Docs Verified

- OpenRouter server tools docs confirm `openrouter:web_search` belongs in the
  Chat Completions `tools` array.
- OpenRouter web-search docs confirm `max_results`, `max_total_results`, and
  `search_context_size` parameters.
- OpenRouter docs confirm web results are surfaced as standardized
  `url_citation` annotations.

## Design Changes Made

- `SkipperClient.stream()` extracts URL citations and web-search usage from
  stream chunks.
- `/api/skipper/messages` emits a `sources` SSE event before `done`.
- The `done` event includes both `web_search_requested` and actual
  `web_search` usage.
- `V2Skipper` merges `sources` events into the assistant bubble.
- `V2Bubble` renders a compact Web sources list with outbound links.
- `/api/skipper/options` now reports separate web-search availability and
  default state.
