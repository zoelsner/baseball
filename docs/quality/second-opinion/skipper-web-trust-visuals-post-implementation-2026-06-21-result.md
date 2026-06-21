# Claude post-implementation review: Skipper web trust visuals

Date: 2026-06-21

Command:

```bash
~/.local/bin/claude -p --model opus --effort xhigh "$(cat docs/quality/second-opinion/skipper-web-trust-visuals-post-implementation-2026-06-21.md)"
```

## Verdict

Claude reviewed the actual `feature/skipper-web-trust-visuals` branch against
`main` and recommended narrow request-changes before merge, not a scope split.

## Accepted and fixed

- Web fallback turned off should not downgrade pure snapshot answers from green
  to yellow. Fixed by carrying structural missing-data signals even when web is
  disabled, then warning only when those signals show the prompt actually asked
  for external/missing context.
- Supplemental-only web sources should not share the same red treatment as a
  broken answer. Fixed by grading captured supplemental-only sources as
  `mixed` with a "Supplemental sources" label, while web usage with no captured
  sources remains `risky`.
- Added regression tests for both badge-logic fixes.

## Follow-ups kept out of this patch

- Citation canonicalization for redirector/AMP URLs can improve trusted-domain
  classification, but the current baseline intentionally classifies only the
  captured URL domain.
- Some OpenRouter/model paths may use web results without URL annotations. The
  current badge is limited to captured citations plus usage telemetry; a live
  OpenRouter manual smoke should be added before depending on the badge as a
  production calibration signal.
- Lowercase or single-surname player extraction is brittle. This should become
  a focused follow-up instead of widening the current gate heuristics late in
  this slice.
- `web_search_allowed()` is retained for compatibility with existing tests and
  callers but is no longer the main production gate.

## Decision

Patch the two badge-logic findings in this branch and rerun the targeted unit
suite before pushing the follow-up commit.
