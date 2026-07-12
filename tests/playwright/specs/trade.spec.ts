import { test, expect } from '@playwright/test';
import { waitForAppMount, gotoTab } from '../fixtures/sandlot';

async function mockTradeAdvisor(page: import('@playwright/test').Page) {
  await page.route('**/api/skipper/options', route => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({}) }));
  await page.route('**/api/waiver-swaps/latest', route => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ brief: { state: 'missing' } }) }));
  await page.route('**/api/skipper/messages', route => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ messages: [] }) }));
  await page.route('**/api/snapshot/latest', async route => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        snapshot_id: 321,
        team_id: 'me',
        team_name: 'Sandlot',
        freshness: { state: 'fresh', age_minutes: 1 },
        roster: [{ id: 'm1', name: 'My Second Baseman', slot: '2B', positions: '2B', fppg: 2.0 }],
        standings: [{ team_id: 'me', team_name: 'Sandlot', rank: 1, fantasy_points: 100 }],
        player_index: [
          { id: 'm1', name: 'My Second Baseman', source: 'mine', slot: '2B', positions: '2B', team: 'ME', fppg: 2.0 },
          { id: 'o1', name: 'Their Outfielder', source: 'league', slot: 'OF', positions: 'OF', team: 'OPP', fppg: 1.5 },
        ],
      }),
    });
  });
  await page.route('**/api/trades/grade', async route => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        snapshot_id: 321,
        letter_grade: 'C',
        headline: 'Current-rate deficit · lower FP/G',
        fairness: 0.9,
        my_delta: -0.5,
        their_delta: 0.5,
        age_delta: 1,
        my_weakest_position: '2B',
        rationale: 'The current snapshot rate favors the other roster.',
        counters: [{
          tier: 'balanced', acceptance_band: 'balanced', my_delta: 0.5,
          give: [{ id: 'm1', name: 'My Second Baseman' }],
          get: [{ id: 'o1', name: 'Their Outfielder' }, { id: 'o2', name: 'Their Second Baseman' }],
          rationale: 'Adds 2B help while preserving a fair current-rate package.',
        }],
        analysis: {
          recommendation: { action: 'counter', title: 'Counter before accepting', detail: 'Adds 2B help while preserving a fair current-rate package.' },
          horizons: [
            { key: 'current_rate', label: 'Current rate', status: 'modeled', value: -0.5, unit: 'FP/G', detail: 'Net change from current snapshot scoring rates.' },
            { key: 'this_week', label: 'This week', status: 'unavailable', value: null, unit: null, detail: 'Weekly games and lineup usage are not modeled yet.' },
            { key: 'rest_of_season', label: 'Rest of season', status: 'unavailable', value: null, unit: null, detail: 'Rest-of-season playing time is not modeled yet.' },
            { key: 'dynasty', label: 'Dynasty', status: 'limited', value: 1, unit: 'yr avg age', detail: 'Average age is only a directional signal.' },
          ],
          roster_fit: { weakest_position: '2B', acquired_positions: ['OF'], fills_weakest_position: false, label: 'Does not directly fill 2B', detail: 'The get side covers OF, not your weakest current-rate position.' },
          recommended_counter: { tier: 'balanced' },
          skipper_prompt: 'Analyze this proposed trade: I give My Second Baseman; I get Their Outfielder. Do not claim unsupported certainty.',
          manual_only: true,
        },
      }),
    });
  });
}

async function gradeMockOffer(page: import('@playwright/test').Page) {
  await gotoTab(page, 'League');
  await page.getByRole('button', { name: /Grade an offer/i }).click();
  await page.getByRole('button', { name: 'Add player to You give', exact: true }).click();
  await page.getByRole('group', { name: 'You give player options' }).getByRole('button').first().click();
  await page.getByRole('button', { name: 'Add player to You get', exact: true }).click();
  await page.getByRole('group', { name: 'You get player options' }).getByRole('button').first().click();
  await page.getByRole('button', { name: 'Grade', exact: true }).click();
}

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

  test('separates trade horizons and carries the exact offer into Skipper', async ({ page }) => {
    const consoleErrors: string[] = [];
    page.on('console', message => {
      if (message.type() === 'error') consoleErrors.push(message.text());
    });
    page.on('pageerror', error => consoleErrors.push(error.message));
    await page.setViewportSize({ width: 390, height: 860 });
    await mockTradeAdvisor(page);
    await page.goto('/');
    await waitForAppMount(page);
    await gradeMockOffer(page);

    await expect(page.getByRole('heading', { name: 'Counter before accepting' })).toBeVisible();
    await expect(page.getByText('-0.50 FP/G', { exact: true })).toBeVisible();
    await expect(page.getByText('Not modeled', { exact: true })).toHaveCount(2);
    await expect(page.getByText('Does not directly fill 2B', { exact: true })).toBeVisible();
    await expect(page.getByText(/never auto-accepts/i)).toBeVisible();
    await page.getByRole('button', { name: 'Ask Skipper', exact: true }).click();
    await expect(page.getByPlaceholder(/Ask about your roster/i)).toHaveValue(/I give My Second Baseman; I get Their Outfielder/i);
    expect(consoleErrors).toEqual([]);
  });

  test('hides stale analysis as soon as the reviewed offer is edited', async ({ page }) => {
    await mockTradeAdvisor(page);
    await page.goto('/');
    await waitForAppMount(page);
    await gradeMockOffer(page);

    await expect(page.getByRole('heading', { name: 'Counter before accepting' })).toBeVisible();
    await page.getByRole('button', { name: 'Edit offer', exact: true }).click();
    await expect(page.getByRole('heading', { name: 'Counter before accepting' })).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Grade', exact: true })).toBeVisible();
  });
});
