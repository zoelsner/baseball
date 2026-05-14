import { test, expect } from '@playwright/test';
import { waitForAppMount, waitForSnapshotLoaded } from '../fixtures/sandlot';

/**
 * Matchup projection UI lives on `feat/matchup-projection-v01`. The deployed
 * frontend does NOT yet contain `V2WinProbabilityRing`, so injecting the
 * `projection` field via route-mock alone is insufficient — there's no JSX on
 * prod to render the ring.
 *
 * Marked `fixme` until the branch ships. After merge + deploy:
 *   1. Drop the `test.fixme` calls below
 *   2. Re-run: `npm test -- specs/today-projection.spec.ts`
 *   3. The route-mock keeps tests deterministic on real data shapes.
 */
test.fixme(true, 'Pending feat/matchup-projection-v01 deploy — see file header.');

async function overlayProjection(page: import('@playwright/test').Page, projection: any) {
  await page.route('**/api/snapshot/latest', async route => {
    const res = await route.fetch();
    const body = await res.json();
    if (body?.matchup) body.matchup.projection = projection;
    await route.fulfill({ response: res, json: body });
  });
}

test.describe('Today — matchup projection ring', () => {
  test('shows green ring + projected line when win probability ≥ 60%', async ({ page }) => {
    await overlayProjection(page, {
      projected_my: 270.4,
      projected_opp: 240.1,
      my_remaining_games: 18,
      opp_remaining_games: 17,
      win_probability: 0.78,
      complete: false,
    });

    await page.goto('/');
    await waitForAppMount(page);
    await waitForSnapshotLoaded(page);

    await expect(page.getByText(/^78$/)).toBeVisible();
    await expect(page.getByText(/% TO WIN/i)).toBeVisible();
    await expect(page.getByText(/Projected\s+270\.4\s*[—-]\s*240\.1/i)).toBeVisible();
  });

  test('shows red ring when win probability < 40%', async ({ page }) => {
    await overlayProjection(page, {
      projected_my: 200.0,
      projected_opp: 250.0,
      my_remaining_games: 10,
      opp_remaining_games: 12,
      win_probability: 0.18,
      complete: false,
    });

    await page.goto('/');
    await waitForAppMount(page);
    await waitForSnapshotLoaded(page);

    await expect(page.getByText(/^18$/)).toBeVisible();
    await expect(page.getByText(/% TO WIN/i)).toBeVisible();
  });

  test('hides ring when matchup is complete and tied', async ({ page }) => {
    await overlayProjection(page, {
      projected_my: 200.0,
      projected_opp: 200.0,
      my_remaining_games: 0,
      opp_remaining_games: 0,
      win_probability: 0.5,
      complete: true,
    });

    await page.goto('/');
    await waitForAppMount(page);
    await waitForSnapshotLoaded(page);

    // Matchup card should still render (margin etc.), but the ring is hidden.
    await expect(page.getByText(/% TO WIN/i)).toHaveCount(0);
  });

  test('hides ring when win_probability is missing', async ({ page }) => {
    await overlayProjection(page, {
      projected_my: 250.0,
      projected_opp: 240.0,
      my_remaining_games: 5,
      opp_remaining_games: 4,
      win_probability: null,
      complete: false,
    });

    await page.goto('/');
    await waitForAppMount(page);
    await waitForSnapshotLoaded(page);

    await expect(page.getByText(/% TO WIN/i)).toHaveCount(0);
  });
});
