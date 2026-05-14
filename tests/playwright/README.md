# Sandlot Playwright tests

End-to-end tests targeting the deployed Sandlot V1 on Railway.

## Setup (once)

```bash
cd tests/playwright
npm install
npx playwright install chromium
```

## Run

```bash
npm test                # headless mobile (iPhone 14)
npm run test:headed     # see the browser
npm run test:ui         # Playwright UI mode for picking/inspecting tests
```

## Targeting a different deploy

```bash
SANDLOT_URL=https://staging.example.up.railway.app npm test
```

The default points at the production Railway URL configured in `playwright.config.ts`.

## How tests are structured

- Tests run against the **real deployed app** with **real snapshot data**, so
  assertions are shape-based where possible (e.g. score format, presence of
  cards) rather than tied to specific values.
- For features not yet deployed (in flight on a feature branch), tests can use
  Playwright's `route` to inject the new field on top of the live snapshot —
  see `specs/today-projection.spec.ts` for the pattern.

## CI (no laptop needed)

`.github/workflows/playwright.yml` runs the suite on:

- **push to `main`** — sleeps 60s, polls `/api/health` until Railway's new
  deploy is live, then runs tests.
- **`workflow_dispatch`** — manual trigger from the Actions tab (or GitHub
  Mobile).
- **daily cron** — 14:30 UTC smoke check, catches Fantrax-side drift between
  scrape runs (cookie expiry, schema changes).

To override the target URL, add a repo secret named `SANDLOT_URL`. Otherwise
the workflow falls back to the production URL hard-coded in the YAML.

Failure artifacts (`playwright-report/`) upload automatically and are
viewable from the Actions run page on phone or web.

## Notes

- Sandlot is a no-bundler SPA: index.html loads React + Babel via CDN and
  transforms `.jsx` files in-browser. First paint waits on Babel, so tests
  wait for the bottom tab bar (`text=Today`) to confirm the app has mounted.
