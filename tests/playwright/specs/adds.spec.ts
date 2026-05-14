import { test, expect } from '@playwright/test';
import { waitForAppMount, gotoTab } from '../fixtures/sandlot';

test.describe('Adds (waiver swaps) page', () => {
  test('renders waiver cards with the API-reported add player names', async ({ page }) => {
    const swapsPromise = page.waitForResponse(
      r => r.url().includes('/api/waiver-swaps/latest') && r.ok(),
      { timeout: 15_000 },
    );

    await page.goto('/');
    await waitForAppMount(page);
    await gotoTab(page, 'Adds');

    const swaps = await (await swapsPromise).json();
    const cards = swaps?.cards || [];

    // The page may legitimately render an empty state when no positive
    // swaps exist. Assert that branch explicitly when cards is empty.
    if (cards.length === 0) {
      await expect(page.getByText(/no positive waiver swaps|no cards|nothing in this filter/i)).toBeVisible();
      test.info().annotations.push({
        type: 'note',
        description: 'API returned 0 waiver cards; only empty-state asserted.',
      });
      return;
    }

    // Top-ranked card: its add player's name should be on the page.
    const topAdd = cards[0]?.add?.name;
    test.skip(!topAdd, 'Top waiver card missing add.name; nothing to assert.');
    await expect(page.getByText(topAdd, { exact: false }).first()).toBeVisible();
  });
});
