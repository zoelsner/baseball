Act as a skeptical senior engineering reviewer. Review the current branch after implementation.

Context:
- Repository: `zoelsner/baseball`
- Current branch: `feature/skipper-web-trust-visuals`
- Commit under review: `034baf9 Add Skipper source trust metadata`
- Compare against `main`.

Goal of the implemented slice:
Harden Sandlot Skipper's web-fallback architecture for roster optimization:
- server-side web-search decisioning so web context is only allowed for clear missing-data/public-context cases
- trusted vs supplemental citation metadata
- persisted assistant-message metadata for sources, confidence, and web-search decisions
- live and restored Skipper UI read-quality badge
- Opus/xhigh second-opinion loop documented for future work

Please inspect the actual code and tests, especially:
- `sandlot_skipper.py`
- `sandlot_api.py`
- `sandlot_db.py`
- `web/sandlot/v2-pages.jsx`
- `tests/test_sandlot_skipper_web_trust.py`
- `tests/playwright/specs/skipper-web-fallback.spec.ts`
- docs under `docs/quality/`

Validation already run:
- `./.venv/bin/python -m py_compile sandlot_skipper.py sandlot_api.py sandlot_db.py`
- `./.venv/bin/python -m unittest tests.test_sandlot_skipper_web_trust tests.test_sandlot_skipper_config tests.test_sandlot_skipper_projection`
- `PATH=/Applications/Codex.app/Contents/Resources/cua_node/bin:$PATH npm run build:sandlot`
- `PATH=/Applications/Codex.app/Contents/Resources/cua_node/bin:$PATH SANDLOT_URL=http://127.0.0.1:8017 tests/playwright/node_modules/.bin/playwright test smoke.spec.ts skipper-web-fallback.spec.ts --config tests/playwright/playwright.config.ts`
- Visual screenshot check of the Skipper read-quality badge.

Known local caveat:
Full local `unittest discover` still fails on pre-existing local environment issues: `fantraxapi` import shape and Python 3.9 missing `unittest.assertNoLogs`.

Please identify:
1. merge-blocking correctness or architecture issues
2. non-blocking but important follow-up risks
3. test gaps that matter for this PR
4. UI/UX concerns with the read-quality badge or source trust display
5. whether you would merge, request changes, or split scope

Be blunt and concrete. Prefer file/function-level findings over general advice.
