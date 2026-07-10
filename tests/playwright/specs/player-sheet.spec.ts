import { test, expect } from '@playwright/test';
import { waitForAppMount, gotoTab } from '../fixtures/sandlot';

test.describe('V2PlayerSheet (bottom sheet)', () => {
  test('opens when tapping a roster row and closes on dismiss', async ({ page }) => {
    const player = {
      id: 'player-sheet-test',
      name: 'Player Sheet Test',
      positions: 'OF',
      team: 'LAD',
      slot: 'OF',
      slot_source: 'raw.lineupSlot',
      fppg: 4.2,
      age: 27,
    };
    await page.route('**/api/snapshot/latest', async route => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          team_name: 'Zach Sandlot',
          team_id: 'team-zach',
          snapshot_id: 'player-sheet-snapshot',
          taken_at: '2026-07-10T12:00:00Z',
          freshness: { state: 'fresh', age_minutes: 12 },
          roster_meta: {},
          roster: [player],
          standings: [],
          player_index: [{ ...player, source: 'mine' }],
          matchup: null,
          data_quality: {
            projection_ready: false,
            recommendations_ready: true,
            lineup_recommendations_ready: true,
            add_drop_recommendations_ready: false,
          },
        }),
      });
    });
    await page.route('**/api/player/player-sheet-test', async route => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          player: { ...player, source: 'my_roster' },
          group: 'hitting',
          games: [],
          sparkline: [],
          trend: { direction: 'flat' },
          take: { text: 'A deterministic player-sheet test.' },
          snapshot_freshness: { state: 'fresh', age_minutes: 12 },
          profile_cache: { take: { state: 'ready', pending: false } },
        }),
      });
    });

    await page.goto('/');
    await waitForAppMount(page);
    await gotoTab(page, 'Roster');

    const playerDetailPromise = page.waitForResponse(
      res => /\/api\/player\/[^/]+$/.test(res.url()),
      { timeout: 10_000 },
    );

    const rosterRow = page.getByRole('button').filter({ hasText: player.name });
    await expect(rosterRow).toHaveCount(1);
    await rosterRow.click();

    const detailRes = await playerDetailPromise;
    expect(detailRes.ok()).toBe(true);

    // Sheet header repeats the player's name. After open, the name should
    // appear at least twice (row + sheet header).
    await expect.poll(async () => {
      return await page.getByText(player.name, { exact: false }).count();
    }, { timeout: 5_000 }).toBeGreaterThanOrEqual(2);

    await expect(page.getByRole('dialog', { name: 'Player details', exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Close', exact: true })).toBeFocused();

    // Keyboard dismissal preserves a usable modal flow on desktop and mobile
    // assistive keyboards; backdrop and explicit close remain available.
    await page.keyboard.press('Escape');

    await expect(page.getByRole('dialog', { name: 'Player details', exact: true })).toHaveCount(0);
  });
});
