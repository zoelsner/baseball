import { test, expect } from '@playwright/test';
import { waitForAppMount, gotoTab } from '../fixtures/sandlot';

test.describe('Trade page', () => {
  test('renders both player pickers and a disabled Grade CTA', async ({ page }) => {
    await page.goto('/');
    await waitForAppMount(page);

    const tradeTab = page.getByRole('button', { name: 'Trade', exact: true });
    if (await tradeTab.count()) {
      await tradeTab.click();
    } else {
      await gotoTab(page, 'League');
      await page.getByRole('button', { name: /Grade an offer/i }).click();
    }

    // Both V2PlayerPicker labels.
    await expect(page.getByText(/^You give$/i)).toBeVisible();
    await expect(page.getByText(/^You get$/i)).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Grade an offer', exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Add player to You give', exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Add player to You get', exact: true })).toBeVisible();

    // The Grade CTA exists. With no players picked it should not be ready —
    // V2Primary renders the helper subtitle "Pick at least one player on each side".
    await expect(page.getByText(/Pick at least one player on each side/i)).toBeVisible();

    // A trade is not gradeable until both sides have at least one player.
    // Preserve that as a real native disabled state, not only an in-handler guard.
    await expect(page.getByRole('button', { name: 'Grade', exact: true })).toBeDisabled();

    const addGive = page.getByRole('button', { name: 'Add player to You give', exact: true });
    const addGiveBox = await addGive.boundingBox();
    expect(addGiveBox).not.toBeNull();
    expect(addGiveBox!.height).toBeGreaterThanOrEqual(40);
    await addGive.click();
    await expect(page.getByRole('textbox', { name: 'Search players for You give', exact: true })).toBeFocused();
  });
});
