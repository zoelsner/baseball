import { test, expect } from '@playwright/test';
import { waitForAppMount } from '../fixtures/sandlot';

/**
 * Matchup projection regression coverage. `V2WinProbabilityRing` landed in #17
 * and the ring + "Projected X.X — Y.Y" line live on the Today page (see
 * v2-pages.jsx:668 and :883-887). Route-mocks inject `projection` into
 * /api/snapshot/latest so tests are deterministic regardless of the actual
 * scrape state on prod.
 *
 * Originally gated behind `test.fixme` with assertions written ahead of the
 * deploy. The gate masked: (1) a broken `waitForSnapshotLoaded` import that
 * doesn't exist in fixtures, (2) assertions on text the UI never renders
 * (e.g. "% TO WIN"). Rewritten in #35 to match what the Today page actually
 * shows; gate removed so the suite catches future regressions.
 */

async function overlayProjection(page: import('@playwright/test').Page, projection: any) {
  await page.route('**/api/snapshot/latest', async route => {
    const res = await route.fetch();
    const body = res.ok() ? await res.json() : {
      status: 'success',
      source: 'test',
      snapshot_id: 1,
      taken_at: new Date().toISOString(),
      freshness: { state: 'fresh', age_minutes: 0 },
      roster: [{ id: 'mine-1', name: 'Roster Player', slot: 'OF', fppg: 2.0 }],
      roster_meta: { active: 1, active_max: 20 },
      standings: [],
      player_index: [],
      errors: [],
      data_quality: {
        projection_ready: true,
        recommendations_ready: false,
        lineup_recommendations_ready: false,
      },
      matchup: {
        my_score: 100,
        opponent_score: 100,
        opponent_team_name: 'Test Opponent',
        period_number: 1,
        end: '2026-07-12',
        complete: false,
      },
    };
    if (body?.matchup) body.matchup.projection = projection;
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
  });
}

test.describe('Today — matchup projection ring', () => {
  test('renders projected line when win probability is high (>=60%)', async ({ page }) => {
    await overlayProjection(page, {
      projected_my: 270.4,
      projected_opp: 240.1,
      my_remaining_games: 18,
      opp_remaining_games: 17,
      win_probability: 0.78,
      probability_calibrated: true,
      complete: false,
    });

    await page.goto('/');
    await waitForAppMount(page);

    await expect(page.getByText(/Projected\s+270\.4\s*[—-]\s*240\.1/i)).toBeVisible();
  });

  test('renders projected line when win probability is low (<40%)', async ({ page }) => {
    await overlayProjection(page, {
      projected_my: 200.0,
      projected_opp: 250.0,
      my_remaining_games: 10,
      opp_remaining_games: 12,
      win_probability: 0.18,
      probability_calibrated: true,
      complete: false,
    });

    await page.goto('/');
    await waitForAppMount(page);

    await expect(page.getByText(/Projected\s+200\.0\s*[—-]\s*250\.0/i)).toBeVisible();
  });

  test('hides projected line when matchup is complete (tied or not)', async ({ page }) => {
    await overlayProjection(page, {
      projected_my: 200.0,
      projected_opp: 200.0,
      my_remaining_games: 0,
      opp_remaining_games: 0,
      win_probability: 0.5,
      probability_calibrated: true,
      complete: true,
    });

    await page.goto('/');
    await waitForAppMount(page);
    // Wait for the matchup card to actually render before asserting absence.
    // "Margin" label is rendered by V2MatchupCard whenever a matchup exists,
    // so it's a stable readiness signal that's independent of projection state.
    await expect(page.getByText(/^margin$/i)).toBeVisible();
    await expect(page.getByText(/^Projected\s+\d/i)).toHaveCount(0);
  });

  test('keeps the projected score but labels missing probability evidence', async ({ page }) => {
    await overlayProjection(page, {
      projected_my: 250.0,
      projected_opp: 240.0,
      my_remaining_games: 5,
      opp_remaining_games: 4,
      win_probability: null,
      probability_calibrated: true,
      complete: false,
    });

    await page.goto('/');
    await waitForAppMount(page);
    await expect(page.getByText(/^margin$/i)).toBeVisible();
    await expect(page.getByText(/Projected\s+250\.0\s*[—-]\s*240\.0/i)).toBeVisible();
    await expect(page.getByText(/probability unavailable/i)).toBeVisible();
  });

  test('labels uncalibrated probability and does not render a probability ring', async ({ page }) => {
    await overlayProjection(page, {
      projected_my: 250.0,
      projected_opp: 240.0,
      my_remaining_games: 5,
      opp_remaining_games: 4,
      win_probability: 0.91,
      probability_calibrated: false,
      complete: false,
    });

    await page.goto('/');
    await waitForAppMount(page);
    await expect(page.getByText(/probability uncalibrated/i)).toBeVisible();
    await expect(page.getByText('EDGE', { exact: true })).toHaveCount(0);
  });
});
