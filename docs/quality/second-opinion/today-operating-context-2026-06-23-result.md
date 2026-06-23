# Second Opinion Result: Today Operating Context

## Claude Feedback Summary

Claude agreed that moving matchup context to the top is directionally right,
but said reordering alone would still feel like a dashboard. The screen needs
connective tissue that makes the flow read as an operations system:

- Matchup state must causally connect to the hot-swap recommendation.
- Blocked execution should read as an intentional safety policy, not an
  unfinished disabled button.
- Confidence/provenance should use real values and avoid decorative certainty.
- Snapshot freshness should appear near the matchup status.
- A credible agent should handle "hold/no action" states cleanly.

## Accepted Into This Slice

- Matchup status now renders before Hot Swaps.
- The matchup card includes snapshot freshness.
- Hot Swaps copy now references the current margin, days left, and projected
  benefit from the top recommendation.
- Attention Queue remains below Hot Swaps as secondary monitoring.
- Existing blocked execution and safety checklist remain intact; no write path
  was added.

## Deferred Follow-Ups

- Build a more explicit "hold" hero state when no swap clears the threshold.
- Add a compact decision-receipt/eval surface for source, model version,
  input hash, accepted/rejected outcome, and hindsight result.
- Tighten blocked execution wording further around dry-run/approval policy when
  executor work begins.

## Verification

- `./node_modules/.bin/esbuild web/sandlot/main.jsx --bundle --minify --format=iife --outfile=web/sandlot/app.js --define:process.env.NODE_ENV=\"production\" --legal-comments=none` passed.
- `git diff --check` passed.
- Local browser verification used a mock API server serving the current
  production snapshot with the rebuilt branch bundle. It showed Matchup first,
  Hot Swaps second, Attention Queue third, causal Hot Swaps copy with current
  trailing margin and projected benefit, and no console errors.
- Local npm Playwright could not run in this shell because `npm`/`node` is not
  available; the Playwright spec was updated for CI.
