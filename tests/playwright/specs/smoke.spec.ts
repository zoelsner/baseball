import { test, expect } from '@playwright/test';
import { waitForAppMount } from '../fixtures/sandlot';

test.describe('app boot', () => {
  test('SPA mounts and shows the bottom tab bar', async ({ page }) => {
    const consoleErrors: string[] = [];
    page.on('console', msg => {
      if (msg.type() === 'error') consoleErrors.push(msg.text());
    });

    await page.goto('/');
    await waitForAppMount(page);

    // The durable tabs are wired up. `exact: true` because the empty-state Today
    // page renders an "Ask Skipper" CTA that would otherwise collide with the
    // "Skipper" tab match. Trade moved under League in #57; the League spec
    // carries that migration assertion when the target deploy has the new UI.
    for (const label of ['Today', 'Roster', 'Adds', 'League', 'Skipper']) {
      await expect(page.getByRole('button', { name: label, exact: true })).toBeVisible();
    }

    // No hard JS errors on boot. Allow benign React/CDN noise but flag real ones.
    const fatal = consoleErrors.filter(e =>
      !/Download the React DevTools/i.test(e) &&
      !/babel/i.test(e) &&
      !/source map/i.test(e),
    );
    expect(fatal, `Console errors on boot:\n${fatal.join('\n')}`).toEqual([]);
  });
});
