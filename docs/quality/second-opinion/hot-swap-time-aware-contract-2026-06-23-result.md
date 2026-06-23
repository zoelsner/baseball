# Second Opinion Result: Hot Swap Time-Aware Contract

## Claude Attempt

Attempted:

```bash
~/.local/bin/claude -p "$(cat docs/quality/second-opinion/hot-swap-time-aware-contract-2026-06-23.md)" --model opus --effort xhigh
```

Result: blocked by tenant policy because the prompt would send repo-derived
implementation details to an external service. No workaround was attempted.

## Internal Checkpoint Findings

### Accepted Must-Fix Findings

- The initial contract named the headline OUT/IN players but did not preserve
  the full ordered lineup chain for multi-step "free up a slot" swaps. A safe
  future executor needs the complete chain, not just the promoted/demoted pair.
  Fixed by adding `contract.slot_moves` and `contract.requires_multi_step`,
  with regression coverage for a three-step chain.
- The shared `_parse_date()` helper treated `datetime` as `date` because
  `datetime` subclasses `date`. Schedule-lock comparisons could misbehave if
  MLB game data arrived as a Python `datetime` object rather than an ISO
  string. Fixed by handling `datetime` first and returning `value.date()`.

### Accepted Follow-Ups

- Future write execution should still perform a fresh Fantrax preflight before
  any click/write, compare the live row ids/slots to the stored proposal
  contract, then require Zach confirmation from the fresh preflight result.
- The current read-only contract is sufficient for display and future executor
  design, but executor work should add post-write verification and immutable
  action logging before writes are enabled.
- Keep the UI wording focused on the user decision; retain deeper provider and
  schedule evidence in the API payload for debugging and automation.

## Verification After Fixes

- `PYTHONPYCACHEPREFIX=/tmp/sandlot-pyc .venv/bin/python -m py_compile sandlot_matchup.py`
- `.venv/bin/python -m unittest tests.test_sandlot_recommendations tests.test_sandlot_attention` passed: 41 tests.
- `.venv/bin/python -m unittest discover -s tests` passed: 179 tests.
- `git diff --check` passed.
- `./node_modules/.bin/esbuild web/sandlot/main.jsx --bundle --minify --format=iife --outfile=web/sandlot/app.js --define:process.env.NODE_ENV=\"production\" --legal-comments=none` passed with no bundle diff.
