import { test, expect } from '@playwright/test';
import { waitForAppMount, captureSnapshot, gotoTab } from '../fixtures/sandlot';

test.describe('V2PlayerSheet (bottom sheet)', () => {
  test('opens when tapping a roster row and closes on dismiss', async ({ page }) => {
    const snapshotPromise = captureSnapshot(page);
    await page.goto('/');
    await waitForAppMount(page);
    const snapshot = await snapshotPromise;
    await gotoTab(page, 'Roster');

    const firstRow = (snapshot?.roster ?? []).find((r: any) => r?.name);
    test.skip(!firstRow, 'Snapshot has no roster rows.');

    const playerDetailPromise = page.waitForResponse(
      res => /\/api\/player\/[^/]+$/.test(res.url()),
      { timeout: 10_000 },
    );

    await page.getByText(firstRow.name, { exact: false }).first().click();

    const detailRes = await playerDetailPromise;
    expect(detailRes.ok()).toBe(true);

    // Sheet header repeats the player's name. After open, the name should
    // appear at least twice (row + sheet header).
    await expect.poll(async () => {
      return await page.getByText(firstRow.name, { exact: false }).count();
    }, { timeout: 5_000 }).toBeGreaterThanOrEqual(2);

    // Dismiss via the sheet's explicit Close button (aria-label="Close").
    // Escape isn't wired up; backdrop click works but is harder to target.
    await page.getByRole('button', { name: 'Close', exact: true }).click();

    await expect.poll(async () => {
      return await page.getByText(firstRow.name, { exact: false }).count();
    }, { timeout: 5_000 }).toBeLessThanOrEqual(1);
  });
});
