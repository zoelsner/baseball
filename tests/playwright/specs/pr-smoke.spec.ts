import fs from 'node:fs';
import path from 'node:path';
import { test, expect, Page } from '@playwright/test';
import { waitForAppMount, gotoTab } from '../fixtures/sandlot';

const mockSnapshot = {
  snapshot_id: 999,
  taken_at: '2026-06-21T18:00:00Z',
  freshness: { state: 'fresh', age_minutes: 3 },
  team_id: 'team-me',
  team_name: 'Codex Sandlot',
  roster: [
    { id: 'roster-1', name: 'Bryan Hudson', positions: 'RP', slot: 'BN', team: 'MIL', fppg: 5.1, fpts: 95.4 },
    { id: 'roster-2', name: 'Aaron Judge', positions: 'OF', slot: 'OF', team: 'NYY', fppg: 10.2, fpts: 201.7 },
  ],
  roster_meta: { active: 2, active_max: 22 },
  standings: [
    { team_id: 'team-me', team_name: 'Codex Sandlot', owner: 'Zach', rank: 1, fantasy_points: 4201.5, win: 8, loss: 3 },
    { team_id: 'team-2', team_name: 'The Opponent', owner: 'Skipper', rank: 2, fantasy_points: 4102.1, win: 7, loss: 4 },
  ],
  player_index: [
    { id: 'roster-1', name: 'Bryan Hudson', source: 'my_roster' },
    { id: 'fa-1', name: 'Brandon Young', source: 'free_agent' },
  ],
  matchup: null,
  data_quality: {
    state: 'ready',
    projection_reasons: [],
    recommendation_reasons: [],
  },
};

const mockSwap = {
  id: 'swap-test',
  rank: 1,
  net_delta: 0.7,
  confidence: 'Low',
  add: { id: 'fa-1', name: 'Brandon Young', positions: 'SP', team: 'BAL' },
  move_out: { id: 'roster-1', name: 'Bryan Hudson', positions: 'RP', team: 'MIL' },
  fills_position: 'SP',
  evidence_chips: ['Estimated FP/G', 'Keeper check'],
  why: 'Brandon Young is a watch-list fit, but the FP/G edge needs manual verification.',
  risk: "Brandon Young's FP/G is estimated from the Fantrax row; verify the scoring value and role before treating this as actionable.",
  dynasty_note: 'Keeper age is missing; verify dynasty value before moving Bryan Hudson out.',
};

async function mockSandlotApis(page: Page) {
  await page.route('**/api/snapshot/latest', route => {
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(mockSnapshot) });
  });
  await page.route('**/api/waiver-swaps/latest', route => {
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ cards: [mockSwap], brief: { state: 'missing' }, protected_move_outs: [] }),
    });
  });
  await page.route('**/api/skipper/messages', route => {
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ session_id: 1, snapshot_id: mockSnapshot.snapshot_id, messages: [] }),
    });
  });
  await page.route('**/api/skipper/options', route => {
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        default_model: 'deepseek/deepseek-v4-flash',
        models: [{ id: 'deepseek/deepseek-v4-flash', label: 'DeepSeek V4 Flash', short: 'DS Flash' }],
        reasoning: { default_enabled: false, default_effort: 'medium', efforts: ['minimal', 'low', 'medium', 'high'] },
      }),
    });
  });
}

test.describe('PR local UI smoke', () => {
  test('branch bundle supports the mocked Adds to Skipper flow', async ({ page }) => {
    const consoleErrors: string[] = [];
    page.on('console', msg => {
      if (msg.type() === 'error') consoleErrors.push(msg.text());
    });

    await mockSandlotApis(page);
    await page.goto('/');
    await waitForAppMount(page);

    for (const label of ['Today', 'Roster', 'Adds', 'League', 'Skipper']) {
      await expect(page.getByRole('button', { name: label, exact: true })).toBeVisible();
    }

    await gotoTab(page, 'Adds');
    await expect(page.getByText(mockSwap.add.name, { exact: false }).first()).toBeVisible();
    await expect(page.getByText(mockSwap.move_out.name, { exact: false }).first()).toBeVisible();

    const artifactsDir = path.resolve(process.cwd(), 'artifacts');
    fs.mkdirSync(artifactsDir, { recursive: true });
    await page.screenshot({ path: path.join(artifactsDir, 'pr-smoke-adds.png'), fullPage: true });

    await page.getByRole('button', { name: /continue in skipper/i }).first().click();

    const draft = page.getByPlaceholder(/ask about your roster/i);
    await expect(draft).toBeVisible();
    await expect(draft).toHaveValue(/add Brandon Young/i);
    await expect(draft).toHaveValue(/move out Bryan Hudson/i);
    await page.screenshot({ path: path.join(artifactsDir, 'pr-smoke-skipper.png'), fullPage: true });

    const fatal = consoleErrors.filter(error =>
      !/Download the React DevTools/i.test(error) &&
      !/source map/i.test(error),
    );
    expect(fatal, `Console errors on mocked PR smoke:\n${fatal.join('\n')}`).toEqual([]);
  });
});
