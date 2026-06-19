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
      await expect(page.getByText(/no positive waiver swaps|no cards|nothing in this filter/i).first()).toBeVisible();
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

  test('continues a waiver card in Skipper as an unsent draft', async ({ page }) => {
    test.skip(
      process.env.GITHUB_EVENT_NAME === 'pull_request',
      'PR E2E targets production Railway, not the branch bundle; this runs locally and after merge on main.',
    );

    const topCard = {
      id: 'swap-test',
      rank: 1,
      net_delta: 0.7,
      confidence: 'Low',
      add: { id: 'fa-1', name: 'Brandon Young', positions: 'SP', team: 'BAL' },
      move_out: { id: 'roster-1', name: 'Bryan Hudson', positions: 'RP', team: 'MIL' },
      fills_position: 'SP',
      evidence_chips: ['_cells inferred FP/G', 'Dynasty check'],
      why: 'Brandon Young is a watch-list fit, but the FP/G edge is unverified.',
      risk: 'The FP/G source is inferred from unlabeled Fantrax cells.',
      dynasty_note: 'Check dynasty context before moving Bryan Hudson out.',
    };

    await page.route('**/api/waiver-swaps/latest', async route => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          cards: [topCard],
          brief: { state: 'missing' },
          protected_move_outs: [],
        }),
      });
    });

    await page.goto('/');
    await waitForAppMount(page);
    await gotoTab(page, 'Adds');

    const addName = topCard?.add?.name;
    const outName = topCard?.move_out?.name;

    await page.getByRole('button', { name: /continue in skipper/i }).first().click();

    await expect(page.getByRole('dialog', { name: /swap context/i })).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Skipper' })).toHaveCSS('color', 'rgb(15, 23, 42)');

    const draft = page.getByPlaceholder(/ask about your roster/i);
    await expect(draft).toBeVisible();
    await expect(draft).toHaveValue(new RegExp(`add ${addName.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}`, 'i'));
    await expect(draft).toHaveValue(new RegExp(`move out ${outName.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}`, 'i'));

    await expect(page.locator('text=Help me pressure-test this waiver swap').first()).toHaveCount(0);
  });
});
