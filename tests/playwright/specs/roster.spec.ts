import { test, expect } from '@playwright/test';
import { waitForAppMount, captureSnapshot, gotoTab } from '../fixtures/sandlot';

test.describe('Roster page', () => {
  test('renders position cards with player rows', async ({ page }) => {
    const snapshotPromise = captureSnapshot(page);
    await page.goto('/');
    await waitForAppMount(page);
    const snapshot = await snapshotPromise;

    await gotoTab(page, 'Roster');

    const rows = (snapshot?.roster ?? []).filter((r: any) => r?.name);
    test.skip(rows.length === 0, 'Snapshot has no roster rows.');

    // Pick a roster row name and assert it renders. Use first() — the same
    // name might appear in a position card and a different surface elsewhere
    // (e.g. the player_index list).
    const sample = rows[0].name;
    await expect(page.getByText(sample, { exact: false }).first()).toBeVisible();
  });
});
