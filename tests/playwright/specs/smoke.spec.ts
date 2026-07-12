import { test, expect } from '@playwright/test';
import { waitForAppMount } from '../fixtures/sandlot';

test.describe('app boot', () => {
  test('SPA mounts and shows the bottom tab bar', async ({ page }) => {
    const consoleErrors: string[] = [];
    page.on('console', msg => {
      if (msg.type() === 'error') consoleErrors.push(msg.text());
    });
    await page.route('**/api/snapshot/latest', async route => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          team_name: 'Zach Sandlot',
          team_id: 'team-zach',
          snapshot_id: 'smoke-snapshot',
          taken_at: '2026-06-21T12:00:00Z',
          freshness: { state: 'fresh', age_minutes: 0 },
          roster_meta: {},
          standings: [],
          roster: [],
          player_index: [],
          matchup: null,
          data_quality: { projection_ready: false, recommendations_ready: false },
        }),
      });
    });
    await page.route('**/api/recommendation-receipts/latest', route => route.fulfill({ status:204 }));
    await page.route('**/api/recommendation-learning', route => route.fulfill({
      status:200,
      contentType:'application/json',
      body:JSON.stringify({ summary:{ scored:0, accepted_and_observed:0 }, evidence_checkpoint:{ requirements:[] }, autopilot_eligible:false }),
    }));

    await page.goto('/');
    await waitForAppMount(page);

    // The durable tabs are wired up. `exact: true` because the empty-state Today
    // page renders an "Ask Skipper" CTA that would otherwise collide with the
    // "Skipper" tab match. Trade moved under League in #57; the League spec
    // carries that migration assertion when the target deploy has the new UI.
    const tabOrder = ['Today', 'Roster', 'Skipper', 'Adds', 'League'];
    for (const label of tabOrder) {
      await expect(page.getByRole('button', { name: label, exact: true })).toBeVisible();
    }
    const centers = await Promise.all(tabOrder.map(async label => {
      const box = await page.getByRole('button', { name: label, exact: true }).boundingBox();
      expect(box, `${label} tab should have a layout box`).not.toBeNull();
      return (box!.x + box!.width / 2);
    }));
    expect(centers).toEqual([...centers].sort((a, b) => a - b));

    // No hard JS errors on boot. Allow benign React/CDN noise but flag real ones.
    const fatal = consoleErrors.filter(e =>
      !/Download the React DevTools/i.test(e) &&
      !/babel/i.test(e) &&
      !/source map/i.test(e),
    );
    expect(fatal, `Console errors on boot:\n${fatal.join('\n')}`).toEqual([]);
  });
});
