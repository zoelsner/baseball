import { test, expect } from '@playwright/test';
import { waitForAppMount, captureSnapshot, gotoTab } from '../fixtures/sandlot';

function shapedSnapshot(overrides: Record<string, any> = {}) {
  return {
    team_name: 'Zach Sandlot',
    team_id: 'team-zach',
    snapshot_id: 'snapshot-ui-test',
    taken_at: '2026-07-10T12:00:00Z',
    freshness: { state: 'fresh', age_minutes: 12 },
    roster_meta: {},
    roster: [
      { id: 'healthy-1', name: 'Healthy One', positions: 'OF', team: 'LAD', slot: 'OF', slot_source: 'raw.statusId', fppg: 5.8 },
      { id: 'healthy-2', name: 'Healthy Two', positions: '1B', team: 'ATL', slot: '1B', slot_source: 'raw.statusId', fppg: 4.8 },
      { id: 'healthy-3', name: 'Healthy Three', positions: 'SP', team: 'SEA', slot: 'SP', slot_source: 'raw.statusId', fppg: 12.2 },
    ],
    standings: [
      { team_id: 'team-a', team_name: 'First Standings Team', rank: 1, fantasy_points: 100, win: 8, loss: 2, tie: 0 },
    ],
    player_index: [],
    matchup: {
      week: 16,
      my_score: 100,
      opponent_score: 90,
      opponent_team_name: 'First Opponent',
      days_left: 3,
      recommendations: { recommendations: [] },
    },
    data_quality: {
      projection_ready: true,
      recommendations_ready: true,
      lineup_recommendations_ready: true,
      add_drop_recommendations_ready: true,
      lineup_slots: { state: 'ok', trusted: 3, total: 3, reason: 'Trusted lineup slots' },
    },
    ...overrides,
  };
}

test.describe('Today page', () => {
  test('renders matchup card with shape-correct scores and margin', async ({ page }) => {
    const snapshotPromise = captureSnapshot(page);
    await page.goto('/');
    await waitForAppMount(page);
    const snapshot = await snapshotPromise;

    // If the deployed snapshot has a matchup with scores, the matchup card renders.
    // Otherwise the empty state takes over — assert that path explicitly.
    const m = snapshot?.matchup;
    const hasScores = m && (m.my_score !== undefined || m.myScore !== undefined);

    if (!hasScores) {
      await expect(page.getByText(/no matchup|off week|tbd|fantrax snapshot/i)).toBeVisible();
      test.info().annotations.push({
        type: 'note',
        description: 'Deployed snapshot has no matchup scores; only empty-state asserted.',
      });
      return;
    }

    // X.X · X.X score format renders for both sides somewhere on the page.
    const bodyText = await page.locator('body').innerText();
    expect(bodyText).toMatch(/\b\d{1,3}\.\d\b\s*·\s*\d{1,3}\.\d\b/);

    // Margin label is always present alongside any rendered matchup.
    await expect(page.getByText(/^margin$/i)).toBeVisible();
  });

  test('shows opponent label from snapshot', async ({ page }) => {
    const snapshotPromise = captureSnapshot(page);
    await page.goto('/');
    await waitForAppMount(page);
    const snapshot = await snapshotPromise;

    const oppName = snapshot?.matchup?.opponent_team_name;
    test.skip(!oppName, 'No opponent in current snapshot; nothing to assert.');

    // Escape regex metacharacters in opponent name (team names sometimes
    // contain `.` or `+`).
    const escaped = oppName.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    await expect(page.getByText(new RegExp(`vs\\s+${escaped}`, 'i'))).toBeVisible();
  });
});

test.describe('Today trust and app-shell state', () => {
  test('does not call an unavailable snapshot clear', async ({ page }) => {
    await page.route('**/api/snapshot/latest', async route => {
      await route.fulfill({
        status: 503,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'Database unavailable' }),
      });
    });

    await page.goto('/');
    await waitForAppMount(page);

    await expect(page.getByText('No snapshot to check', { exact: true })).toBeVisible();
    await expect(page.getByText('No current issues', { exact: true })).toHaveCount(0);
    await expect(page.getByText(/Waiting for the first successful Fantrax snapshot/)).toBeVisible();
  });

  test('qualifies an old empty queue and uses a non-green freshness dot', async ({ page }) => {
    await page.route('**/api/snapshot/latest', async route => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(shapedSnapshot({ freshness: { state: 'old', age_minutes: 2_880 } })),
      });
    });

    await page.goto('/');
    await waitForAppMount(page);

    await expect(page.getByText('Snapshot too old to call clear', { exact: true })).toBeVisible();
    await expect(page.getByText('No current issues', { exact: true })).toHaveCount(0);
    await expect(page.locator('button[aria-label="Refresh Fantrax data"] > span')).toHaveCSS('background-color', 'rgb(220, 38, 38)');
  });

  test('silently refetches the stored snapshot on focus using GET', async ({ page }) => {
    let current = shapedSnapshot();
    const methods: string[] = [];
    await page.route('**/api/snapshot/latest', async route => {
      methods.push(route.request().method());
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(current) });
    });

    await page.goto('/');
    await waitForAppMount(page);
    await gotoTab(page, 'League');
    await expect(page.getByText('First Standings Team', { exact: true })).toBeVisible();

    current = shapedSnapshot({
      snapshot_id: 'snapshot-ui-test-2',
      freshness: { state: 'stale', age_minutes: 1_200 },
      standings: [
        { team_id: 'team-b', team_name: 'Updated Standings Team', rank: 1, fantasy_points: 120, win: 9, loss: 1, tie: 0 },
      ],
      matchup: {
        week: 16,
        my_score: 101,
        opponent_score: 99,
        opponent_team_name: 'Updated Opponent',
        days_left: 3,
        recommendations: { recommendations: [] },
      },
    });
    await page.evaluate(() => window.dispatchEvent(new Event('focus')));

    await expect(page.getByText('Updated Standings Team', { exact: true })).toBeVisible();
    expect(methods.length).toBeGreaterThanOrEqual(2);
    expect(methods.every(method => method === 'GET')).toBe(true);

    await gotoTab(page, 'Today');
    await expect(page.getByText('No issues in the stale snapshot', { exact: true })).toBeVisible();
    await expect(page.locator('button[aria-label="Refresh Fantrax data"] > span')).toHaveCSS('background-color', 'rgb(223, 112, 66)');
  });

  test('keeps the displayed snapshot age ticking', async ({ page }) => {
    await page.clock.install();
    await page.route('**/api/snapshot/latest', async route => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(shapedSnapshot({ freshness: { state: 'fresh', age_minutes: 59 } })),
      });
    });

    await page.goto('/');
    await waitForAppMount(page);
    await expect(page.getByText('snapshot 59m old', { exact: true })).toBeVisible();

    await page.clock.fastForward(60_000);
    await expect(page.getByText('snapshot 1h old', { exact: true })).toBeVisible();
  });

  test('keeps manual refresh single-flight and disabled while pending', async ({ page }) => {
    let refreshCount = 0;
    let releaseRefresh!: () => void;
    const refreshGate = new Promise<void>(resolve => { releaseRefresh = resolve; });
    await page.route('**/api/snapshot/latest', async route => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(shapedSnapshot()) });
    });
    await page.route('**/api/refresh', async route => {
      refreshCount += 1;
      await refreshGate;
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ snapshot: shapedSnapshot({ snapshot_id: 'snapshot-after-refresh' }) }),
      });
    });

    await page.goto('/');
    await waitForAppMount(page);
    await expect(page.getByText('snapshot 12m old', { exact: true })).toBeVisible();
    const refresh = page.getByRole('button', { name: 'Refresh Fantrax data', exact: true });
    await refresh.evaluate(button => {
      const control = button as HTMLButtonElement;
      control.click();
      control.click();
    });

    await expect.poll(() => refreshCount).toBe(1);
    await expect(page.getByRole('button', { name: 'Refreshing Fantrax data', exact: true })).toBeDisabled();

    releaseRefresh();
    await expect(page.getByRole('button', { name: 'Refresh Fantrax data', exact: true })).toBeEnabled();
  });

  test('resets the main content scroll position when the page changes', async ({ page }) => {
    const longRoster = Array.from({ length: 40 }, (_, index) => ({
      id: `player-${index}`,
      name: `Player ${index}`,
      positions: index % 2 ? 'OF' : 'SP',
      team: 'LAD',
      slot: index % 2 ? 'OF' : 'SP',
      slot_source: 'raw.statusId',
      fppg: index % 2 ? 4.2 : 11.5,
    }));
    await page.route('**/api/snapshot/latest', async route => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(shapedSnapshot({ roster: longRoster })),
      });
    });

    await page.goto('/');
    await waitForAppMount(page);
    await gotoTab(page, 'Roster');
    await expect(page.getByRole('button', { name: /Player 0/ })).toBeVisible();
    const mainScroll = page.getByTestId('main-scroll');
    await mainScroll.evaluate(element => { element.scrollTop = 600; });
    await expect.poll(() => mainScroll.evaluate(element => element.scrollTop)).toBeGreaterThan(0);

    await gotoTab(page, 'Today');
    await expect.poll(() => mainScroll.evaluate(element => element.scrollTop)).toBe(0);
  });
});
