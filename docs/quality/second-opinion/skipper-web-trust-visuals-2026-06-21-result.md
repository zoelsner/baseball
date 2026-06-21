# Claude second-opinion result: Skipper web trust visuals

Date: 2026-06-21

Command:

```bash
~/.local/bin/claude -p --model opus --effort xhigh "$(cat docs/quality/second-opinion/skipper-web-trust-visuals-2026-06-21.md)"
```

Note: an earlier unpinned `claude -p` pass was discarded as the authoritative
gate. The loop now pins Opus with extra-high effort.

## Key feedback accepted

- Move assistant-message persistence until after streaming has collected sources and quality metadata. The current order cannot persist final provenance.
- Add `chat_messages.metadata JSONB` and return it through history so live stream and restored history share one message shape.
- Classify captured URL citations by trusted vs supplemental source domains.
- Keep rendered source links tied to captured citations, not model prose claims.
- Make the quality visual structural, not a prose-grep confidence score.
- Include deterministic replies in the metadata/quality path.
- Count distinct source domains, not just URLs.
- Fold quality/source metadata into the existing `done` contract where possible instead of introducing unnecessary SSE events.
- Base the quality badge on actual web usage and captured sources, not merely
  on whether the server allowed web search.
- Make deterministic replies respect degraded snapshot data quality.
- Trim web-search intent keywords to avoid turning ordinary stats questions
  into paid web-search opportunities.
- Add unit coverage for web gating, source classification, source summary, and
  quality assessment.

## Feedback adapted

- Claude recommended descoping deterministic web gating because player extraction from free text can be brittle. The implementation keeps a conservative gate, but only for clear structural signals:
  - explicit user/server disable denies web.
  - deterministic snapshot replies deny web.
  - normal roster/matchup questions deny web.
  - named players missing from the snapshot index, waiver/free-agent prompts with missing free-agent data, or explicit public-context prompts can allow web.
  - missing `player_index` alone does not blanket-enable web.
- Claude noted that citation consistency is not truly enforceable through
  prompt text alone. The implementation treats captured citations as UI truth
  and avoids promising that model prose source names are enforced.

## Feedback rejected for now

- Reducing the visual to two states. The user explicitly asked for a visible good/not-good read, and the product already uses good/warn/bad states. The implementation keeps three levels but labels the middle as a verification state rather than pretending to be precise.

## Implementation target

- Server-owned `web_search_decision`.
- Trusted-source metadata on citations.
- Structural `confidence` metadata on every assistant message.
- Persisted/restored source and confidence UI.
- Targeted unit and Playwright coverage.

## Deferred follow-ups

- Mid-stream model fallback can still produce best-effort partial output before
  retry, which is the app's existing streaming contract. A transactional stream
  reset would require a broader SSE/UI protocol change.
- Redirector/AMP URL canonicalization for citations is useful but not required
  for the trusted-domain baseline.
