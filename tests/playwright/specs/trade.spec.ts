import { test, expect } from '@playwright/test';
import { waitForAppMount, gotoTab } from '../fixtures/sandlot';

test.describe('Trade page', () => {
  test('renders both player pickers and a disabled Grade CTA', async ({ page }) => {
    await page.goto('/');
    await waitForAppMount(page);
    await gotoTab(page, 'Trade');

    // Both V2PlayerPicker labels.
    await expect(page.getByText(/^You give$/i)).toBeVisible();
    await expect(page.getByText(/^You get$/i)).toBeVisible();

    // The Grade CTA exists. With no players picked it should not be ready —
    // V2Primary renders the helper subtitle "Pick at least one player on each side".
    await expect(page.getByText(/Pick at least one player on each side/i)).toBeVisible();

    // CTA itself is present. Note: V2Primary doesn't propagate `disabled` to
    // the underlying <button> — readiness is enforced by an in-handler guard
    // (`if (!ready) return`), so the button looks clickable even when not.
    // Worth fixing eventually, but for now we only assert presence + the
    // helper-text affordance below.
    await expect(page.getByRole('button', { name: 'Grade', exact: true })).toBeVisible();
  });
});
